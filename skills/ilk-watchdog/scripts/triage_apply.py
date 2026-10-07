#!/usr/bin/env python3
"""Apply a triage decision within its enforced write set.

Part of sub-plan a-triage-decision-is-applied-within-its-rails.

The model proposes; code disposes.  Every write goes through
``_assert_write_allowed`` which raises on any path outside the approved set.
Stdlib only; imports ``ilk_audit``, ``blacklist_status``, and ``plan_status``
via ``sys.path`` from sibling / cousin ``scripts/`` directories.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Ensure the scripts dirs are importable.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(_HERE))

from blacklist_status import write_resume_ack
from ilk_audit import audit_day, read_audit, write_audit
from ilk_notify import main as _notify_main
from plan_status import parse_frontmatter


def ilk_notify(*, event: str, project: str, detail: str | None = None) -> None:
    """Send a desktop notification (fire-and-forget)."""
    args = ["--event", event, "--project", project]
    if detail:
        args.extend(["--detail", detail])
    _notify_main(args)

# ── constants ────────────────────────────────────────────────────────────────

VALID_ACTIONS = frozenset({
    "reopen", "amend", "ack-and-relaunch", "park-and-escalate",
})
_MAX_FINDING_LEN = 4000
_IDEMPOTENCY_DAYS = 7

# ── write-set enforcement ────────────────────────────────────────────────────

# Paths that triage_apply is allowed to write.  All are relative to data_dir.
_ALLOWED_WRITE_PREFIXES = (
    "plans/",
    "runtime/launcher/blacklist-cleared.json",
    "runtime/triage/",
    "audit/",
)

# Explicitly forbidden paths (even if they match a prefix).
_FORBIDDEN_PATTERNS = (
    re.compile(r"^plans/MASTER-"),
    re.compile(r"batch-gate"),
    re.compile(r"logs/verification/"),
)


def _assert_write_allowed(path: Path, data_dir: Path) -> None:
    """Raise if *path* is outside the enforced write set.

    The write set is:
    - ``<data_dir>/plans/<sub-plan>.md`` (never a MASTER file)
    - ``<data_dir>/runtime/launcher/blacklist-cleared.json``
    - ``<data_dir>/runtime/triage/``
    - ``<data_dir>/audit/``

    Anything else — including paths in the project repo, MASTER files,
    ``batch-gate*.json``, and ``logs/verification/`` — is rejected.
    """
    try:
        rel = str(path.resolve().relative_to(data_dir.resolve()))
    except ValueError:
        raise ValueError(
            f"write outside data_dir forbidden: {path}"
        ) from None

    # Must match an allowed prefix.
    if not any(rel.startswith(p) for p in _ALLOWED_WRITE_PREFIXES):
        raise ValueError(
            f"write to {rel!r} is outside the triage write set"
        )

    # Must not match a forbidden pattern.
    for pat in _FORBIDDEN_PATTERNS:
        if pat.search(rel):
            raise ValueError(
                f"write to {rel!r} is explicitly forbidden"
            )


# ── plan helpers ─────────────────────────────────────────────────────────────

def _read_plan_frontmatter(plan_path: Path) -> dict[str, str]:
    """Parse a sub-plan file's frontmatter."""
    text = plan_path.read_text(encoding="utf-8")
    return parse_frontmatter(text)


def _write_plan_field(plan_path: Path, key: str, value: str) -> None:
    """Set a scalar frontmatter key in a sub-plan file."""
    text = plan_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if key not in fm:
        return
    old_line = f"{key}: {fm[key]}"
    new_line = f"{key}: {value}"
    text = text.replace(old_line, new_line, 1)
    _assert_write_allowed(plan_path, plan_path.parent.parent)
    plan_path.write_text(text, encoding="utf-8")


def _append_finding(
    plan_path: Path,
    data_dir: Path,
    *,
    run_id: str,
    finding: str,
    basis: str,
    falsifier: str,
) -> None:
    """Append a ``#### Triage <ts> (run <id>)`` block under ``## Findings``."""
    _assert_write_allowed(plan_path, data_dir)
    text = plan_path.read_text(encoding="utf-8")

    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    block = (
        f"\n#### Triage {ts} (run {run_id})\n"
        f"- Finding: {finding}\n"
        f"- Basis: {basis}\n"
        f"- Falsifier: {falsifier}\n"
    )

    if "## Findings" in text:
        text = text.replace("## Findings\n", "## Findings\n" + block, 1)
    else:
        text += f"\n## Findings\n{block}\n"

    plan_path.write_text(text, encoding="utf-8")


def _set_plan_status(
    plan_path: Path,
    data_dir: Path,
    *,
    status: str | None = None,
    current_step: int | None = None,
) -> None:
    """Update status and/or current_step in a sub-plan's frontmatter."""
    _assert_write_allowed(plan_path, data_dir)
    text = plan_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)

    now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    text = text.replace(
        f"last_updated: {fm.get('last_updated', '')}",
        f"last_updated: {now_str}",
        1,
    )

    if status is not None and "status" in fm:
        text = text.replace(f"status: {fm['status']}", f"status: {status}", 1)
    if current_step is not None and "current_step" in fm:
        text = text.replace(
            f"current_step: {fm['current_step']}",
            f"current_step: {current_step}",
            1,
        )
    plan_path.write_text(text, encoding="utf-8")


def _find_plans_dir(data_dir: Path) -> Path:
    """Locate the plans directory under data_dir."""
    plans_dir = data_dir / "plans"
    if plans_dir.is_dir():
        return plans_dir
    # Walk one level down for project-key layouts.
    for child in data_dir.iterdir():
        candidate = child / "plans"
        if candidate.is_dir():
            return candidate
    return plans_dir


def _find_active_subplan(plans_dir: Path) -> Path | None:
    """Return the first non-MASTER plan with ``status: in-progress``."""
    for plan_file in sorted(plans_dir.glob("*.md")):
        if plan_file.name.startswith("MASTER-"):
            continue
        fm = _read_plan_frontmatter(plan_file)
        if fm.get("status") == "in-progress":
            return plan_file
    return None


def _resolve_target_slug(
    decision: dict[str, Any],
    data_dir: Path,
    plans_dir: Path,
) -> tuple[Path | None, str | None]:
    """Resolve the target sub-plan path and slug.

    Returns ``(plan_path, slug)`` — both None when nothing can be resolved.
    """
    slug = decision.get("slug")
    if slug:
        plan_path = plans_dir / f"*{slug}*.md"
        matches = list(plans_dir.glob(f"*{slug}*.md"))
        if matches:
            return matches[0], slug
        return None, slug

    # No slug in decision — try last-exit.json failed_check.
    last_exit_path = data_dir / "runtime" / "launcher" / "last-exit.json"
    if last_exit_path.exists():
        try:
            last_exit = json.loads(last_exit_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            last_exit = {}
        failed_slug = (last_exit.get("failed_check") or {}).get("slug")
        if failed_slug:
            matches = list(plans_dir.glob(f"*{failed_slug}*.md"))
            if matches:
                fm = _read_plan_frontmatter(matches[0])
                if fm.get("status") != "shipped":
                    return matches[0], failed_slug

    # Fallback: active sub-plan.
    active = _find_active_subplan(plans_dir)
    if active:
        fm = _read_plan_frontmatter(active)
        return active, fm.get("plan")
    return None, None


# ── two-strikes ──────────────────────────────────────────────────────────────


def _state_path(data_dir: Path) -> Path:
    return data_dir / "runtime" / "triage" / "state.json"


def _load_state(data_dir: Path) -> dict[str, Any]:
    p = _state_path(data_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(data_dir: Path, state: dict[str, Any]) -> None:
    p = _state_path(data_dir)
    _assert_write_allowed(p, data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")


def _compute_signature(
    data_dir: Path,
    *,
    run_id: str,
    slug: str | None,
    current_step: int,
) -> tuple[int, tuple[str, ...], str | None]:
    """Build the two-strikes signature: (current_step, node_ids, slug)."""
    node_ids: list[str] = []
    gate_path = data_dir / "runtime" / "launcher" / "gate-history.jsonl"
    if gate_path.exists():
        try:
            for line in gate_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("run_id") == run_id:
                    attr = row.get("attribution") or {}
                    for nid in (attr.get("node_ids") or []):
                        if nid not in node_ids:
                            node_ids.append(nid)
        except (json.JSONDecodeError, OSError):
            pass
    return (current_step, tuple(sorted(node_ids)), slug)


def _check_two_strikes(
    data_dir: Path,
    *,
    slug: str | None,
    signature: tuple[int, tuple[str, ...], str | None],
) -> str | None:
    """Return ``"two strikes"`` when the slug+signature match a prior triage."""
    if not slug:
        return None
    state = _load_state(data_dir)
    prev = state.get(slug)
    if not prev:
        return None
    prev_sig = (
        prev.get("current_step"),
        tuple(prev.get("node_ids", [])),
        prev.get("failed_slug"),
    )
    if prev_sig == signature:
        return "two strikes"
    return None


def _record_strike(
    data_dir: Path,
    *,
    run_id: str,
    slug: str | None,
    action: str,
    signature: tuple[int, tuple[str, ...], str | None],
) -> None:
    """Record this triage in the two-strikes state."""
    if not slug:
        return
    state = _load_state(data_dir)
    state[slug] = {
        "run_id": run_id,
        "action": action,
        "current_step": signature[0],
        "node_ids": list(signature[1]),
        "failed_slug": signature[2],
    }
    _save_state(data_dir, state)


# ── idempotency ──────────────────────────────────────────────────────────────


_TERMINAL_KINDS = frozenset({"triage-applied", "escalated", "triage-refused"})


def _already_applied(data_dir: Path, *, run_id: str) -> bool:
    """Check whether any terminal audit row exists for this run in the
    last ``_IDEMPOTENCY_DAYS`` days of audit files."""
    # Same clock as the writer (ilk_audit.audit_day), never UTC: rows are
    # filed under the host's local date.
    today = datetime.strptime(audit_day(), "%Y-%m-%d").date()
    root = data_dir  # audit is at data_dir/audit/
    for offset in range(_IDEMPOTENCY_DAYS):
        day = (today - timedelta(days=offset)).isoformat()
        try:
            rows = read_audit(day=day, root=root)
        except Exception:
            continue
        for row in rows:
            if (row.get("kind") in _TERMINAL_KINDS
                    and row.get("run_id") == run_id):
                return True
    return False


# ── project name ─────────────────────────────────────────────────────────────


def _project_name(data_dir: Path) -> str:
    """Derive a project name from the data directory path."""
    return data_dir.name


# ── validate ─────────────────────────────────────────────────────────────────


def _any_master_auto_planned(plans_dir: Path) -> bool:
    """True when any master in *plans_dir* has ``auto_planned: true``."""
    for master_file in sorted(plans_dir.glob("MASTER-*.md")):
        try:
            text = master_file.read_text(encoding="utf-8-sig")
            fm = parse_frontmatter(text)
            if fm.get("auto_planned") == "true":
                return True
        except Exception:
            continue
    return False


def validate(
    decision: dict[str, Any], plans_dir: Path, *, repo: Path | None = None,
) -> list[str]:
    """Return the list of problems with *decision* against *plans_dir*.

    An empty list means the decision is valid.
    """
    problems: list[str] = []
    action = decision.get("action")

    if action not in VALID_ACTIONS:
        problems.append(f"unknown action: {action!r}")

    if not decision.get("finding", "").strip():
        problems.append("finding is empty")
    if not decision.get("basis", "").strip():
        problems.append("basis is empty")
    if not decision.get("falsifier", "").strip():
        problems.append("falsifier is empty")

    finding = decision.get("finding", "")
    if len(finding) > _MAX_FINDING_LEN:
        problems.append(
            f"finding exceeds {_MAX_FINDING_LEN} characters"
        )

    slug = decision.get("slug")
    step = decision.get("step")

    if action in ("reopen", "amend") and slug:
        matches = list(plans_dir.glob(f"*{slug}*.md"))
        if not matches:
            problems.append(f"slug {slug!r} not found in plans_dir")

    if action == "reopen":
        if slug and step is not None:
            matches = list(plans_dir.glob(f"*{slug}*.md"))
            if matches:
                fm = _read_plan_frontmatter(matches[0])
                current = int(fm.get("current_step", 0))
                if step < 0 or step > current:
                    problems.append(
                        f"step {step} is out of range "
                        f"(current_step: {current})"
                    )

    # ── kernel entry screening ─────────────────────────────────────────
    if repo is not None:
        kernel_file = repo / "skills" / "ilk-loop" / "safety-kernel.json"
        if kernel_file.exists():
            try:
                from safety_kernel import KernelListError, load  # type: ignore[import-untyped]
                kernel = load(repo)
                auto_planned = _any_master_auto_planned(plans_dir)
                finding = decision.get("finding", "")
                basis = decision.get("basis", "")
                text = finding + " " + basis

                # Rules entries: always screened.
                for entry in kernel.get("rules", []):
                    entry_path = entry["path"]
                    if entry_path in text:
                        problems.append(
                            f"steers into rules-tier {entry_path}"
                        )
                    elif not entry_path.endswith("/"):
                        basename = os.path.basename(entry_path.rstrip("/"))
                        if basename and basename in text:
                            problems.append(
                                f"steers into rules-tier {entry_path}"
                            )

                # Kernel entries: screened only under auto-planned master.
                if auto_planned:
                    for entry in kernel.get("kernel", []):
                        entry_path = entry["path"]
                        if entry_path in text:
                            problems.append(
                                f"steers into kernel {entry_path} "
                                f"under an auto-planned master"
                            )
                        elif not entry_path.endswith("/"):
                            basename = os.path.basename(
                                entry_path.rstrip("/")
                            )
                            if basename and basename in text:
                                problems.append(
                                    f"steers into kernel {entry_path} "
                                    f"under an auto-planned master"
                                )
                    for entry in kernel.get("kernel_basenames", []):
                        name = entry.get("name", "")
                        if name and name in text:
                            problems.append(
                                f"steers into kernel basename:{name} "
                                f"under an auto-planned master"
                            )
            except Exception:
                pass  # skip screening on error

    return problems


# ── apply ────────────────────────────────────────────────────────────────────


def apply(
    decision: dict[str, Any],
    data_dir: Path,
    *,
    run_id: str,
    data_root: Path | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Validate and apply a triage decision.

    Returns a dict describing what happened.  Every path writes exactly
    one terminal audit row: ``triage-applied``, ``escalated``, or
    ``triage-refused``.
    """
    plans_dir = _find_plans_dir(data_dir)
    project = _project_name(data_dir)

    # ── two strikes (before idempotency so repeated slugs escalate) ─────
    target_path, target_slug = _resolve_target_slug(
        decision, data_dir, plans_dir,
    )
    if target_path:
        fm = _read_plan_frontmatter(target_path)
        current_step = int(fm.get("current_step", 0))
    else:
        current_step = 0

    signature = _compute_signature(
        data_dir, run_id=run_id,
        slug=target_slug, current_step=current_step,
    )
    strike_reason = _check_two_strikes(
        data_dir, slug=target_slug, signature=signature,
    )
    if strike_reason:
        escalate_decision = {
            "action": "park-and-escalate",
            "slug": target_slug,
            "step": decision.get("step"),
            "finding": decision.get("finding", ""),
            "basis": decision.get("basis", ""),
            "falsifier": decision.get("falsifier", ""),
        }
        return _do_escalate(
            escalate_decision, data_dir, plans_dir,
            project=project, run_id=run_id,
            reason=strike_reason,
        )

    # ── idempotency (catches duplicate processing) ──────────────────────
    if _already_applied(data_dir, run_id=run_id):
        write_audit(
            "triage-refused", project,
            root=data_dir, run_id=run_id, reason="already_applied",
        )
        return {"action": "refused", "reason": "already_applied"}

    # ── kill switch ─────────────────────────────────────────────────────
    effective_root = data_root or data_dir.parent
    if (effective_root / "triage.disabled").exists():
        write_audit(
            "triage-refused", project,
            root=data_dir, run_id=run_id, reason="kill_switch",
        )
        return {"action": "refused", "reason": "kill_switch"}

    # ── validate ────────────────────────────────────────────────────────
    problems = validate(decision, plans_dir, repo=repo)
    if problems:
        reason = "; ".join(problems)
        escalate_decision = {
            "action": "park-and-escalate",
            "slug": decision.get("slug"),
            "step": decision.get("step"),
            "finding": f"validation failed: {reason}",
            "basis": reason,
            "falsifier": "decision passes validation",
        }
        return _do_escalate(
            escalate_decision, data_dir, plans_dir,
            project=project, run_id=run_id, reason=reason,
        )

    # ── dispatch ────────────────────────────────────────────────────────
    action = decision["action"]

    if action == "park-and-escalate":
        result = _do_escalate(
            decision, data_dir, plans_dir,
            project=project, run_id=run_id,
        )
        return result

    # All non-escalate actions: record the strike, then execute.
    _record_strike(
        data_dir, run_id=run_id,
        slug=target_slug, action=action, signature=signature,
    )

    if action == "ack-and-relaunch":
        return _do_ack_and_relaunch(
            decision, data_dir, plans_dir,
            target_path=target_path,
            project=project, run_id=run_id,
        )
    elif action == "amend":
        return _do_amend(
            decision, data_dir, plans_dir,
            target_path=target_path,
            project=project, run_id=run_id,
        )
    elif action == "reopen":
        return _do_reopen(
            decision, data_dir, plans_dir,
            target_path=target_path,
            project=project, run_id=run_id,
        )
    else:
        # Should not reach here after validation.
        return _do_escalate(
            decision, data_dir, plans_dir,
            project=project, run_id=run_id,
            reason=f"unhandled action: {action}",
        )


# ── action implementations ───────────────────────────────────────────────────


def _do_ack_and_relaunch(
    decision: dict[str, Any],
    data_dir: Path,
    plans_dir: Path,
    *,
    target_path: Path | None,
    project: str,
    run_id: str,
) -> dict[str, Any]:
    """Ack-and-relaunch: append finding, ack blacklist, write audit."""
    if target_path:
        _append_finding(
            target_path, data_dir,
            run_id=run_id,
            finding=decision.get("finding", ""),
            basis=decision.get("basis", ""),
            falsifier=decision.get("falsifier", ""),
        )

    write_resume_ack(data_dir)

    write_audit(
        "triage-applied", project,
        root=data_dir,
        run_id=run_id,
        action="ack-and-relaunch",
        slug=target_path.stem if target_path else None,
    )

    return {
        "action": "ack-and-relaunch",
        "audit_kind": "triage-applied",
    }


def _do_amend(
    decision: dict[str, Any],
    data_dir: Path,
    plans_dir: Path,
    *,
    target_path: Path | None,
    project: str,
    run_id: str,
) -> dict[str, Any]:
    """Amend: append finding; if blocked set to pending; ack."""
    if target_path:
        _append_finding(
            target_path, data_dir,
            run_id=run_id,
            finding=decision.get("finding", ""),
            basis=decision.get("basis", ""),
            falsifier=decision.get("falsifier", ""),
        )
        fm = _read_plan_frontmatter(target_path)
        if fm.get("status") == "blocked":
            _set_plan_status(
                target_path, data_dir,
                status="pending",
            )

    write_resume_ack(data_dir)

    write_audit(
        "triage-applied", project,
        root=data_dir,
        run_id=run_id,
        action="amend",
        slug=target_path.stem if target_path else None,
    )

    return {
        "action": "amend",
        "audit_kind": "triage-applied",
    }


def _do_reopen(
    decision: dict[str, Any],
    data_dir: Path,
    plans_dir: Path,
    *,
    target_path: Path | None,
    project: str,
    run_id: str,
) -> dict[str, Any]:
    """Reopen: set in-progress + step, append finding, ack."""
    if target_path:
        _set_plan_status(
            target_path, data_dir,
            status="in-progress",
            current_step=decision.get("step", 0),
        )
        _append_finding(
            target_path, data_dir,
            run_id=run_id,
            finding=decision.get("finding", ""),
            basis=decision.get("basis", ""),
            falsifier=decision.get("falsifier", ""),
        )

    write_resume_ack(data_dir)

    write_audit(
        "triage-applied", project,
        root=data_dir,
        run_id=run_id,
        action="reopen",
        slug=target_path.stem if target_path else None,
    )

    return {
        "action": "reopen",
        "audit_kind": "triage-applied",
    }


def _do_escalate(
    decision: dict[str, Any],
    data_dir: Path,
    plans_dir: Path,
    *,
    project: str,
    run_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Park-and-escalate: no plan edit, no ack; notify + audit + triage log."""
    effective_reason = reason or decision.get("reason") or "escalated"

    write_audit(
        "escalated", project,
        root=data_dir,
        run_id=run_id,
        action="park-and-escalate",
        reason=effective_reason,
        finding=decision.get("finding", ""),
    )

    # Fire-and-forget notification (patchable by tests via triage_apply.ilk_notify).
    slug = decision.get("slug") or ""
    detail = f"{project}/{slug}: {effective_reason}" if slug else effective_reason
    try:
        ilk_notify(event="triage-escalated", project=project, detail=detail)
    except Exception:
        pass

    # Write triage log with reason and finding.
    try:
        log_dir = data_dir / "runtime" / "triage"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{run_id}.log"
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        entry = (
            f"[{ts}] park-and-escalate\n"
            f"  Reason: {effective_reason}\n"
            f"  Finding: {decision.get('finding', '')}\n"
        )
        log_path.write_text(entry, encoding="utf-8")
    except Exception:
        pass

    return {
        "action": "park-and-escalate",
        "reason": effective_reason,
    }