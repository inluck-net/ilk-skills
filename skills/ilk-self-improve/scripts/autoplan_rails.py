"""Rails for auto-planned batches: kernel guard, ranking, screening, master checks.

Part of sub-plan an-auto-planned-batch-stays-inside-its-rails.

Pure functions, no claude.  Imports ``safety_kernel``, ``plan_status``,
``promote_next_master`` and ``triage_backlog`` by ``sys.path`` relative to
``__file__``.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent.parent  # repo root (three levels up from scripts/)
_LOOP_SCRIPTS = _HERE.parent.parent / "ilk-loop" / "scripts"
_WATCHDOG_SCRIPTS = _HERE.parent.parent / "ilk-watchdog" / "scripts"

if str(_LOOP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LOOP_SCRIPTS))
if str(_WATCHDOG_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_WATCHDOG_SCRIPTS))

from safety_kernel import (  # noqa: E402
    kernel_entries,
    touches_kernel as _sk_touches_kernel,
)
from plan_status import (  # noqa: E402
    extract_subplan_files,
    parse_frontmatter,
)
from promote_next_master import write_status  # noqa: E402
from triage_backlog import read_backlog_strict  # noqa: E402


# ── 1. touches_kernel ────────────────────────────────────────────────────────


def touches_kernel(path: str, kernel: dict | None = None) -> str | None:
    """Return the kernel entry hit by *path*, or ``None``.

    Delegates to ``safety_kernel.touches_kernel`` (the single source of truth
    for the kernel list).  The sub-plan's ``PROTECTED_KERNEL`` tuple is kept
    only as a test fixture — a pin that every entry in it is covered by
    ``kernel_entries()``.
    """
    return _sk_touches_kernel(path, kernel=kernel if kernel is not None else _load_kernel_cached())


# ── 2. screen_candidate ──────────────────────────────────────────────────────

# Regex to find path-like tokens in free text.
_PATH_TOKEN_RE = re.compile(r"[\w./\\-]+\.\w+")


def screen_candidate(entry: dict, kernel: dict | None = None) -> str | None:
    """Return the first kernel entry mentioned in *entry*'s text fields, or None.

    Searches ``title``, ``gap``, ``proposed_fix`` and every value in
    ``evidence`` (recursively flattened).  A hit is either a full kernel path
    or a basename that matches a kernel entry.  *kernel* is the planned
    project's own kernel (``load_project_kernel``); None means the toolkit's.
    """
    if kernel is None:
        kernel = _load_kernel_cached()

    # Collect all text to search.
    texts: list[str] = []
    texts.append(entry.get("title", ""))
    texts.append(entry.get("gap", ""))
    texts.append(entry.get("proposed_fix", ""))
    _collect_strings(entry.get("evidence", {}), texts)

    full_text = " ".join(texts)

    # Check each kernel entry path against the text.
    for tier_name in ("rules", "kernel"):
        for e in kernel.get(tier_name, []):
            epath = e["path"]
            if epath in full_text:
                return epath
            # Also check basename of the path.
            basename = os.path.basename(epath.rstrip("/"))
            if basename and basename in full_text:
                return epath

    # Check basenames.
    for e in kernel.get("kernel_basenames", []):
        name = e["name"]
        if name in full_text:
            return f"basename:{name}"

    # Also try path-like tokens in the text against touches_kernel.
    for token in _PATH_TOKEN_RE.findall(full_text):
        hit = _sk_touches_kernel(token, kernel=kernel)
        if hit is not None:
            return hit

    return None


def _collect_strings(obj: Any, out: list[str]) -> None:
    """Recursively collect all string values from *obj*."""
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_strings(v, out)


#: Auto-planned masters rank below every consumer/owner master.  Promotion is
#: priority desc (promote_next_master._prio, which reads null/"P0" as 0), so 0
#: tied with gh-resolve's 90 null-priority masters and lost only on age.
#: -1 sorts below all of them.  Judgment call (2026-10-10, track B); wrong if
#: a reader rejects negative priorities.
AUTO_PLANNED_PRIORITY = -1


def load_project_kernel(kernel_file: Path) -> dict:
    """Load a planned project's own safety kernel, FAIL CLOSED.

    Unlike ``_load_kernel_cached`` (the toolkit's, which tolerates a missing
    file), a project kernel that cannot be read or has no ``kernel`` list
    raises ``ValueError``: the caller must refuse to plan for that project
    rather than screen against nothing.
    """
    try:
        data = json.loads(Path(kernel_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"project kernel unreadable: {kernel_file}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("kernel"), list):
        raise ValueError(f"project kernel has no kernel list: {kernel_file}")
    data.setdefault("rules", [])
    data.setdefault("kernel_basenames", [])
    return data


# Kernel cache (loaded once per process, invalidated on file change).
_kernel_cache: dict | None = None
_kernel_cache_mtime: float = -1.0


def _load_kernel_cached() -> dict:
    """Load and cache the safety-kernel.json."""
    global _kernel_cache, _kernel_cache_mtime
    kfile = _REPO / "skills" / "ilk-loop" / "safety-kernel.json"
    try:
        mtime = kfile.stat().st_mtime
    except FileNotFoundError:
        return {"rules": [], "kernel": [], "kernel_basenames": []}
    if _kernel_cache is not None and mtime == _kernel_cache_mtime:
        return _kernel_cache
    try:
        data = json.loads(kfile.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"rules": [], "kernel": [], "kernel_basenames": []}
    _kernel_cache = data
    _kernel_cache_mtime = mtime
    return data


# ── 3. rank ──────────────────────────────────────────────────────────────────

# Sources admitted by rank, in priority order (lower index = higher priority).
# Triage has no freshness gate; all others require last_seen (or first_seen)
# within FRESH_DAYS of now.  Judgment call: 14 days — basis: 33+32+12 fresh
# rows vs 3 triage; stale ones are refused by the planner itself (AUTOPLAN:
# stale), cost ~2.5 min, back off 1 h, and stop after 2 attempts per row.
# Wrong if more than half of non-triage starts end stale over a week; then
# shorten the window.
ELIGIBLE_SOURCES = ("triage", "supervisor", "owner-session", "feedback", "session")
FRESH_DAYS = 14

# Source tier for sorting (lower = higher priority).
_SOURCE_TIER = {
    "triage": 0,
    "supervisor": 1,
    "owner-session": 1,
    "feedback": 2,
    "session": 2,
}


def _parse_date(s: str) -> datetime | None:
    """Parse an ISO-8601 timestamp string, returning a tz-aware datetime or None."""
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _relations(entry: dict) -> dict:
    """An entry's relations, or {} when a writer left another shape.

    Writers have emitted [] and comma strings (da8107a9); on 2026-10-08 six
    rows with ``relations: []`` made rank() raise on every tick (0d03cc65).
    """
    rel = entry.get("relations")
    return rel if isinstance(rel, dict) else {}


def _as_int(value: Any) -> int:
    """An int for ranking: a bool, a non-numeric string or None counts as 0."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _is_smoothness(entry: dict) -> bool:
    """A smoothness regression outranks feature work.

    True when ``relations.smoothness`` is truthy (the filer sets it) OR the
    title starts with ``"batch verify exceeded"`` — the latter so the legacy
    slow-verify rows, which carry no relations, rank first without a data
    migration.  Eligibility is untouched; this is a sort key only.
    """
    if _relations(entry).get("smoothness"):
        return True
    title = entry.get("title")
    return isinstance(title, str) and title.startswith("batch verify exceeded")


def rank(entries: list[dict], *, now: datetime | None = None,
         sources: tuple[str, ...] | list[str] | None = None) -> list[dict]:
    """Filter and rank eligible autoplan candidates.

    Eligible: ``status == "open"``, ``source in ELIGIBLE_SOURCES``,
    ``relations.autoplan_attempts < 2``, no ``relations.autoplan_blocked``.
    For every source except ``triage``, ``last_seen`` (else ``first_seen``)
    must parse and be within ``FRESH_DAYS`` of *now*.  An unparseable date
    on a non-triage row makes it ineligible (fail closed).

    Sort: smoothness first (a smooth->not-smooth regression outranks
    feature work; see ``_is_smoothness``), then source tier (triage 0,
    supervisor/owner-session 1, feedback/session 2), then has
    escalations > 0 desc, urgent desc, seen_count desc, first_seen asc,
    id asc.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    # A project other than the toolkit declares its own sources
    # (autoplan.backlog.sources); None keeps the toolkit's list.
    admitted = tuple(sources) if sources else ELIGIBLE_SOURCES

    eligible = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("status") != "open":
            continue
        source = e.get("source")
        if source not in admitted:
            continue
        rel = _relations(e)
        if _as_int(rel.get("autoplan_attempts", 0)) >= 2:
            continue
        if rel.get("autoplan_blocked"):
            continue
        # Freshness gate: triage has no date requirement.
        if source != "triage":
            date_str = e.get("last_seen") or e.get("first_seen", "")
            dt = _parse_date(date_str)
            if dt is None:
                continue  # fail closed: unparseable → ineligible
            if (now - dt).days > FRESH_DAYS:
                continue  # stale
        eligible.append(e)

    eligible.sort(key=lambda e: (
        -int(_is_smoothness(e)),
        _SOURCE_TIER.get(e.get("source", ""), 99),
        -(_as_int(_relations(e).get("escalations", 0)) > 0),
        -bool(_relations(e).get("urgent", False)),
        -_as_int(e.get("seen_count", 0)),
        str(e.get("first_seen", "")),
        str(e.get("id", "")),
    ))
    return eligible


# ── 4. check_master ──────────────────────────────────────────────────────────


def check_master(master_path: Path, plans_dir: Path,
                 kernel: dict | None = None) -> list[str]:
    """Check a master's batch against the rails. Returns problems (empty = clean).

    Checks:
    - Every registry file exists.
    - The LAST registry file has ``batch_verification: true``, no other one does.
    - For each non-verification sub-plan, every ``scope_paths`` entry and every
      backticked path in ``Write``/``Edit`` bullets passes ``touches_kernel``.
    - No other MASTER in *plans_dir* with ``auto_planned: true`` has status
      ``queued`` or ``active``.
    """
    problems: list[str] = []
    try:
        text = master_path.read_text(encoding="utf-8-sig")
    except OSError:
        return [f"cannot read master: {master_path.name}"]

    subplan_files = extract_subplan_files(text)
    if not subplan_files:
        problems.append("master has no registered sub-plans")
        return problems

    # 1. Every registry file exists.
    for fname in subplan_files:
        if not (plans_dir / fname).exists():
            problems.append(f"registered sub-plan missing: {fname}")

    # 2. Last registry file has batch_verification: true, no other one does.
    verify_indices: list[int] = []
    for i, fname in enumerate(subplan_files):
        sub_path = plans_dir / fname
        if not sub_path.exists():
            continue
        try:
            sub_text = sub_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        sub_fm = parse_frontmatter(sub_text)
        if sub_fm.get("batch_verification", "").strip().lower() == "true":
            verify_indices.append(i)

    # Find the last existing sub-plan index.
    last_existing = len(subplan_files) - 1
    while last_existing >= 0 and not (plans_dir / subplan_files[last_existing]).exists():
        last_existing -= 1

    if not verify_indices:
        problems.append("no sub-plan has batch_verification: true")
    else:
        # The last existing sub-plan must be the verification one.
        if last_existing not in verify_indices:
            problems.append(
                f"verification sub-plan is not last: "
                f"{subplan_files[verify_indices[0]]} at index {verify_indices[0]}, "
                f"last is {subplan_files[last_existing]} at index {last_existing}"
            )
        # No other sub-plan should have batch_verification: true.
        non_last = [i for i in verify_indices if i != last_existing]
        for idx in non_last:
            problems.append(
                f"non-last sub-plan has batch_verification: "
                f"{subplan_files[idx]} at index {idx}"
            )

    # 3. Check scope_paths and Write/Edit bullets for kernel hits.
    write_edit_re = re.compile(r"(?:Write|Edit)\s+`([^`]+)`", re.IGNORECASE)

    for fname in subplan_files:
        sub_path = plans_dir / fname
        if not sub_path.exists():
            continue
        try:
            sub_text = sub_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        sub_fm = parse_frontmatter(sub_text)

        # Skip the verification sub-plan's directory scope_paths (its only
        # writes are attributed fixes inside the batch's own diff).
        is_verify = sub_fm.get("batch_verification", "").strip().lower() == "true"

        # Parse scope_paths from the frontmatter string.
        raw_scope = sub_fm.get("scope_paths", "")
        scope_paths: list[str] = _parse_scope_paths(raw_scope)

        if not is_verify:
            for sp in scope_paths:
                hit = touches_kernel(sp, kernel)
                if hit is not None:
                    problems.append(
                        f"{fname}: scope_path hits kernel: {sp} -> {hit}"
                    )

        # Check Write/Edit bullets (skip for verification sub-plan).
        if not is_verify:
            for match in write_edit_re.finditer(sub_text):
                bp = match.group(1)
                hit = touches_kernel(bp, kernel)
                if hit is not None:
                    problems.append(
                        f"{fname}: Write/Edit bullet hits kernel: {bp} -> {hit}"
                    )

    # 4. No other auto-planned master already queued or active.
    fm = parse_frontmatter(text)
    for other in sorted(plans_dir.glob("MASTER-*.md")):
        if other == master_path:
            continue
        try:
            other_text = other.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        other_fm = parse_frontmatter(other_text)
        if other_fm.get("auto_planned", "").strip().lower() == "true":
            other_status = other_fm.get("status", "").strip().lower()
            if other_status in ("queued", "active"):
                problems.append(
                    f"another auto-planned master is {other_status}: {other.name}"
                )

    return problems


def _parse_scope_paths(raw: str) -> list[str]:
    """Parse scope_paths from a frontmatter string value.

    Handles both block sequence (``- "path"``) and flow form (``[a, b]``).
    """
    raw = raw.strip()
    if not raw or raw == "[]":
        return []
    if raw.startswith("["):
        inner = raw[1:-1] if raw.endswith("]") else raw[1:]
        return [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]
    # Block sequence
    paths: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("- "):
            val = line[2:].strip().strip('"').strip("'")
            if val:
                paths.append(val)
    return paths


# ── 5. mark_master ───────────────────────────────────────────────────────────


def mark_master(
    master_path: Path,
    *,
    candidate_id: str,
    run_id: str,
) -> None:
    """Insert ``auto_planned: true``, ``autoplan_candidate`` and ``autoplan_run``
    into the master's frontmatter (replacing existing values).  Atomic write.
    """
    text = master_path.read_text(encoding="utf-8-sig")
    if not text.startswith("---"):
        raise ValueError(f"{master_path.name}: no frontmatter block")
    fm_end = text.find("\n---", 3)
    if fm_end < 0:
        raise ValueError(f"{master_path.name}: no closing ---")

    frontmatter = text[3:fm_end]
    rest = text[fm_end:]  # includes closing --- and everything after

    lines = frontmatter.splitlines(keepends=True)
    new_lines: list[str] = []
    replaced_auto = False
    replaced_cand = False
    replaced_run = False
    replaced_priority = False

    for line in lines:
        key = line.split(":")[0].strip() if ":" in line else ""
        if key == "auto_planned":
            new_lines.append(f'auto_planned: "true"\n')
            replaced_auto = True
        elif key == "autoplan_candidate":
            new_lines.append(f"autoplan_candidate: {candidate_id}\n")
            replaced_cand = True
        elif key == "autoplan_run":
            new_lines.append(f"autoplan_run: {run_id}\n")
            replaced_run = True
        elif key == "priority":
            new_lines.append(f"priority: {AUTO_PLANNED_PRIORITY}\n")
            replaced_priority = True
        else:
            new_lines.append(line)

    # Ensure the last existing line ends with a newline before appending.
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    if not replaced_auto:
        new_lines.append(f'auto_planned: "true"\n')
    if not replaced_cand:
        new_lines.append(f"autoplan_candidate: {candidate_id}\n")
    if not replaced_run:
        new_lines.append(f"autoplan_run: {run_id}\n")
    if not replaced_priority:
        new_lines.append(f"priority: {AUTO_PLANNED_PRIORITY}\n")

    new_text = "---" + "".join(new_lines) + rest
    tmp = master_path.with_suffix(master_path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, master_path)


# ── 6. mark_candidate ────────────────────────────────────────────────────────


def mark_candidate(
    entry_id: str,
    *,
    status: str | None = None,
    relations: dict | None = None,
    increment: dict | None = None,
    backlog_dir: Path | None = None,
) -> dict:
    """Under the lock, strict read, update one entry, atomic write.

    *relations* overwrites keys; *increment* adds each value to the stored
    count (missing or non-int counts as 0), read and written under the same
    lock.  A set-to-1 counter never reached rank()'s ``>= 2`` rail
    (backlog 9888c62bd85cfb82).

    Returns the updated entry dict.  Raises ``KeyError`` for unknown *entry_id*
    (writes nothing).
    """
    if backlog_dir is None:
        raise ValueError("backlog_dir is required")

    lock_path = backlog_dir / "candidates.json.lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        entries = read_backlog_strict(backlog_dir)

        # Find the target entry.
        target = None
        for e in entries:
            if e.get("id") == entry_id:
                target = e
                break
        if target is None:
            raise KeyError(f"unknown candidate id: {entry_id}")

        # Apply updates.
        if status is not None:
            target["status"] = status
        if (relations is not None or increment is not None) and not isinstance(
            target.get("relations", {}), dict
        ):
            # Keep a malformed value for the owner instead of raising
            # (0d03cc65): the row stays writable and nothing is lost.
            target["relations_malformed"] = target["relations"]
            target["relations"] = {}
        if relations is not None:
            existing_rels = target.get("relations", {})
            existing_rels.update(relations)
            target["relations"] = existing_rels
        if increment is not None:
            existing_rels = target.get("relations", {})
            for key, by in increment.items():
                cur = existing_rels.get(key, 0)
                if not isinstance(cur, int) or isinstance(cur, bool):
                    cur = 0
                existing_rels[key] = cur + by
            target["relations"] = existing_rels

        # Atomic write.
        p = backlog_dir / "candidates.json"
        fd, tmp_path = tempfile.mkstemp(
            dir=str(backlog_dir), suffix=".tmp", prefix="candidates."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp_path, p)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return target

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()