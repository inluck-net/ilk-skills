"""Enumerate projects with runnable masters, FIFO-annotated.

Resolves the projects root via ``ilk_paths.ilk_data_root() / "projects"``
(honors ``$ILK_DATA_HOME``). For each project directory, parses every
MASTER-*.md to find sub-plan references, reads their front-matter, and
emits a JSON array of projects that have a **runnable master** — an
``active`` master with ≥1 non-shipped sub-plan, or a ``queued`` master
(with ≥1 non-shipped sub-plan) that promotion can activate.

Each entry::

    {
      "key": "<project-key>",
      "path": "<absolute project data dir under ~/.ilk-data>",
      "repo_path": "<absolute SOURCE repo path, or null if unresolved>",
      "oldest_queued_ts": "<ISO 8601 timestamp>"
    }

``path`` is the data dir (used for the per-project sentinel + postmortems).
``repo_path`` is the real source repo the loop must ``cd`` into — this is
what the scheduler passes to ``launch.* -ProjectPath``. It is resolved from
the project's ``runtime/launcher/last-launch.json`` (``project_path``, written
by every launch), falling back to the ``ilk-launcher/projects.json`` registry.
``repo_path`` is ``null`` when the project has neither — the scheduler then
skips it (``skip-unresolved``) rather than dispatching a wrong path.

Sorted by ``oldest_queued_ts`` ascending (oldest first = FIFO).

A project is excluded only when EVERY master is ``shipped`` (no runnable
master). A project whose only remaining master is ``queued`` is included
(promotion can activate it).

Exit code 0 always (empty list is valid — means "nothing to do").
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_log = logging.getLogger(__name__)

# --- import ilk_paths from sibling ilk-loop/scripts/ ---
# The scan's OWN location wins. A scan run from a repo checkout must
# dispatch through that checkout's launcher; the home-dir fallbacks serve
# only installs that genuinely live in a skills dir. Under launchd the
# scheduler runs with no ILK_SKILL_HOME, and this list used to put
# ~/.codex/skills first — so the repo scheduler dispatched verification
# sessions through a months-old codex install (2026-09-20: stale
# ``running`` sentinels across six projects, double-driven repos).
_HERE_SKILLS = Path(__file__).resolve().parent.parent.parent
_SKILL_ROOT_CANDIDATES = [
    _HERE_SKILLS,
    Path.home() / ".codex" / "skills",
    Path.home() / ".cursor" / "skills",
    Path.home() / ".claude" / "skills",
]


def _resolve_skill_root() -> Path:
    env = os.environ.get("ILK_SKILL_HOME")
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return p
    for candidate in _SKILL_ROOT_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Cannot resolve ilk skill root.")


_SKILL_ROOT = _resolve_skill_root()
_ILK_LOOP_SCRIPTS = _SKILL_ROOT / "ilk-loop" / "scripts"
if _ILK_LOOP_SCRIPTS.is_dir():
    sys.path.insert(0, str(_ILK_LOOP_SCRIPTS))

from ilk_paths import ilk_data_root, project_key  # noqa: E402
from plan_status import (  # noqa: E402
    extract_subplan_files,
    is_master_all_shipped,
    master_has_nonshipped,
    master_has_runnable,
    normalize_master_status,
    parse_frontmatter,
    reconcile_master_registry,
    reconcile_master_status,
)


def resolve_repo_path(project_dir: Path, key: str) -> str | None:
    """Resolve the real SOURCE repo path for a project data dir.

    1. ``<data>/runtime/launcher/last-launch.json`` → ``project_path``
       (written by every launch — the reliable primary source).
    2. ``<skill-root>/ilk-launcher/projects.json`` registry — match an
       entry whose path hashes to the same key (covers registered but
       never-launched projects).
    3. ``None`` if neither resolves.
    """
    last_launch = project_dir / "runtime" / "launcher" / "last-launch.json"
    if last_launch.is_file():
        try:
            data = json.loads(last_launch.read_text(encoding="utf-8-sig"))
            p = data.get("project_path")
            if p:
                return str(p)
        except (OSError, ValueError):
            pass

    registry = _SKILL_ROOT / "ilk-launcher" / "projects.json"
    if registry.is_file():
        try:
            data = json.loads(registry.read_text(encoding="utf-8-sig"))
            for entry in data.get("projects", []):
                ep = entry.get("path")
                if not ep:
                    continue
                try:
                    if project_key(Path(ep)) == key:
                        return str(ep)
                except (OSError, ValueError):
                    continue
        except (OSError, ValueError):
            pass

    return None


def _parse_ts(raw: str) -> datetime | None:
    """Parse an ISO-ish timestamp string as **naive local**. None on failure.

    Always naive, never aware. The two fallbacks used when this returns None —
    ``datetime.fromtimestamp(st_mtime)`` and ``datetime.min`` — are both naive
    local, and ``_scan_one_project`` mixes all three sources in a single
    ``min()``. A ``last_updated: 2026-08-03`` parses naive while a
    ``last_updated: 2026-08-03T17:30:00+08:00`` parses aware, so one master
    carrying both formats raised

        TypeError: can't compare offset-naive and offset-aware datetimes

    which the per-project guard in ``scan_projects`` swallowed — silently
    retiring the whole project from the scheduler's view (2026-08-20; it hit
    two of nine projects, including the toolkit's own).

    Naive-local rather than aware-local because the fallbacks are already
    naive: coercing here is one conversion, whereas going aware would mean
    changing both fallbacks plus ``datetime.min``, which has no meaningful tz.
    ``oldest_queued_ts`` is only ever compared against other values produced
    here, so dropping the offset costs nothing downstream.
    """
    if not raw:
        return None
    try:
        # Handle common formats: 2026-06-06, 2026-06-06T13:40:00+08:00
        ts = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is not None:
        # Convert to local wall-clock, then drop the offset.
        ts = ts.astimezone().replace(tzinfo=None)
    return ts




# Per-project scan failures from the most recent ``scan_projects()`` call.
# Recorded instead of swallowed silently; read by ``main(--scan-errors)``, by
# the scheduler's idle branch, and by ilk-doctor's scheduler-visibility gate.
SCAN_ERRORS: list[dict] = []


def _exc_origin(exc: BaseException) -> str:
    """``file:line`` of the innermost frame of *exc*, or ``"?"``."""
    tb = exc.__traceback__
    if tb is None:
        return "?"
    last = tb
    while last.tb_next is not None:
        last = last.tb_next
    return f"{Path(last.tb_frame.f_code.co_filename).name}:{last.tb_lineno}"


def scan_projects() -> list[dict]:
    """Scan all projects and return those with a runnable master, FIFO-sorted.

    A project has a **runnable master** if:
    - An ``active`` master has ≥1 non-shipped sub-plan, OR
    - A ``queued`` master (with ≥1 non-shipped sub-plan) exists that
      promotion can activate.

    Projects where every master is ``shipped`` are excluded.

    ``oldest_queued_ts`` comes from the runnable master: active first
    (oldest non-shipped sub-plan timestamp), else the next-to-promote
    queued master.
    """
    SCAN_ERRORS.clear()

    root = ilk_data_root() / "projects"
    if not root.is_dir():
        return []

    results: list[dict] = []

    for project_dir in sorted(root.iterdir()):
        try:
            entry = _scan_one_project(project_dir)
        except Exception as exc:
            # Resilience: this scanner runs unattended while other ilk loops
            # concurrently WRITE plan files in these same dirs. A read that
            # races a half-written master/sub-plan (or any other per-project
            # surprise) must skip just that project, never abort the whole
            # scan — an aborted scan emits a traceback that, under the
            # scheduler's `$ErrorActionPreference='Stop'` + `2>&1` merge,
            # killed the daemon outright (the 2026-06-30 three-project crash).
            #
            # Keep the `continue` — but never let it be silent. Before
            # 2026-08-20 it was: a TypeError in the FIFO timestamp comparison
            # (line 394, mixed naive/aware `last_updated`) removed a project
            # from every scan for over an hour while the scheduler logged the
            # generic `all-queues-empty` and ilk-doctor reported `pass: all
            # gates clear`. A project that CANNOT BE SCANNED is a different
            # condition from a project with NO WORK, and only one of the two
            # needs a human.
            SCAN_ERRORS.append({
                "key": project_dir.name,
                "path": str(project_dir),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "where": _exc_origin(exc),
            })
            print(
                f"[scan-error] {project_dir.name}: {type(exc).__name__}: "
                f"{exc} @ {_exc_origin(exc)}",
                file=sys.stderr,
            )
            continue
        if entry is not None:
            results.append(entry)

    # FIFO: oldest first; ties broken by project key for determinism
    results.sort(key=lambda r: (r["oldest_queued_ts"], r["key"]))
    return results


_VERIFICATION_DISPATCH_MARKER = "verification-dispatched.json"


def _dispatch_verification_on_drain(
    project_dir: Path,
    master_path: Path,
    plans_dir: Path,
    *,
    _launch_fn=None,
) -> None:
    """Dispatch a planner-tier verification session when a master drains.

    Called after ``reconcile_master_status`` flips a master to ``shipped``
    (all sub-plans shipped).  Dispatches a planner session running the
    verification entrypoint so that ``verified: true`` can be set
    automatically, unblocking ``promote_next_master``.

    **Idempotency:** a marker file prevents double-dispatch.  Two
    consecutive scan passes over the same shipped master must produce
    exactly one dispatch (AC-1).

    **Skips:**
    - Blacklisted projects (AC-4) — a blocked project must not spawn work.

    Errors are logged and non-fatal (AC-7 — the scheduler continues
    draining other projects).
    """
    # --- idempotency guard (AC-1, per-master) ---
    # The marker records EVERY master dispatched for (``masters`` map);
    # ``master`` keeps the legacy single-master field for back-compat.
    # A master not in the record means "not yet dispatched for this master",
    # not "already handled".  Fail closed on unreadable or malformed markers:
    # treat as "unknown" and dispatch — the caller writes a fresh record on
    # success.  (The single-master marker was the 2026-09-20 respawn engine:
    # a project with N shipped masters re-dispatched on every scan pass,
    # each pass mismatching the one remembered name — ilk-skills bounced
    # every 5 minutes, each launch burning primary-account quota.)
    marker_path = project_dir / "runtime" / _VERIFICATION_DISPATCH_MARKER
    if marker_path.exists():
        try:
            marker_data = json.loads(
                marker_path.read_text(encoding="utf-8-sig"),
            )
            dispatched = set(marker_data.get("masters", {}))
            dispatched.add(marker_data.get("master"))
            dispatched.discard(None)
            if master_path.name in dispatched:
                return  # this master — already dispatched
        except (OSError, json.JSONDecodeError):
            pass  # unreadable → dispatch

    # --- active-work skip (2026-09-20) ---
    # A project whose current master is active or queued is being driven by
    # the loop; its batch-verification sub-plan is the sanctioned verifier.
    # Verify-dispatching alongside it double-drives the repo and, with
    # --engine claude (pre-worker-home), burned the primary account's quota
    # in 4-second bounces.  Skip until the queue is empty.
    try:
        for other in sorted(plans_dir.glob("MASTER-*.md")):
            try:
                other_fm = parse_frontmatter(
                    other.read_text(encoding="utf-8-sig"))
            except OSError:
                continue
            if normalize_master_status(
                    other_fm.get("status", "")) in ("active", "queued"):
                _log.info(
                    "[verify-dispatch] %s still has active/queued work "
                    "(%s) — verification belongs to its batch sub-plan",
                    project_dir.name, other.name,
                )
                return
    except OSError:
        pass

    # --- read master frontmatter ---
    try:
        master_text = master_path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    fm = parse_frontmatter(master_text)

    # --- blacklist skip (AC-4) ---
    try:
        from blacklist_status import is_blacklisted
        bl = is_blacklisted(project_dir)
        if bl.get("blacklisted"):
            return
    except Exception:
        # Blacklist check failure is non-fatal — treat as not blacklisted.
        _log.debug("blacklist check failed for %s: continuing as not blacklisted",
                    project_dir.name)

    # --- a project mid-run must not get a second engine ---
    # The normal dispatch path refuses to double-dispatch (scheduler.sh's
    # skip-busy); this function used to Popen blind, which is how a busy
    # project received extra verification engines (kira: five launches
    # inside five seconds, 2026-09-20 10:10:35-40). A live-PID ``running``
    # sentinel means busy; a dead-PID one is stale and safe to replace.
    try:
        _sentinel = json.loads(
            (project_dir / "runtime" / "launcher" / "last-exit.json")
            .read_text(encoding="utf-8-sig")
        )
        if _sentinel.get("state") == "running":
            _spid = _sentinel.get("pid") or 0
            if _spid:
                try:
                    os.kill(_spid, 0)
                except ProcessLookupError:
                    pass  # dead pid: stale sentinel, safe to dispatch
                except PermissionError:
                    _spid = 0  # alive but not ours: fall through to busy
                else:
                    _log.info(
                        "[verify-dispatch] %s busy (pid %s) — skipping",
                        project_dir.name, _spid,
                    )
                    return
    except (OSError, ValueError):
        pass  # no/unreadable sentinel: nothing to be busy with

    # --- dispatch the planner verification ---
    skill_root = _SKILL_ROOT
    launcher = skill_root / "ilk-launcher" / "scripts" / "launch.sh"
    if not launcher.exists():
        _log.warning(
            "[verify-dispatch] launcher not found at %s — "
            "degrading to manual verification for %s",
            launcher, project_dir.name,
        )
        return

    repo_path = resolve_repo_path(project_dir, project_dir.name)
    if not repo_path:
        _log.warning(
            "[verify-dispatch] no repo_path for %s — "
            "degrading to manual verification",
            project_dir.name,
        )
        return

    cmd = [
        "bash", str(launcher),
        "--project-path", repo_path,
        # claude-manager (2026-09-20, operator): verification sessions run
        # under the manager home — the designated replacement for paths
        # that previously launched as the PRIMARY account (--engine claude)
        # and burned the official quota window. Loops proper stay on the
        # claude-worker engine; the manager engine shares the runner but
        # swaps the session identity only.
        "--engine", "claude-manager",
        "--max-iterations", "1",
    ]

    try:
        if _launch_fn is not None:
            _launch_fn(cmd)
        else:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception as exc:
        # Dispatch failure is non-fatal (AC-7).
        _log.warning(
            "[verify-dispatch] dispatch failed for %s: %s — "
            "degrading to manual verification",
            project_dir.name, exc,
        )
        return

    # --- write idempotency marker (per-master, merged) ---
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if marker_path.exists():
            try:
                existing = json.loads(
                    marker_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        masters_map = dict(existing.get("masters", {}))
        if existing.get("master"):
            masters_map.setdefault(existing["master"], existing.get(
                "dispatched_at", datetime.now().isoformat()))
        now = datetime.now().isoformat()
        masters_map[master_path.name] = now
        marker_data = {
            "master": master_path.name,
            "masters": masters_map,
            "dispatched_at": now,
            "project_key": project_dir.name,
        }
        tmp = marker_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(marker_data, indent=2), encoding="utf-8")
        os.replace(tmp, marker_path)
    except OSError:
        # Marker write failure is non-fatal — worst case we double-dispatch
        # on the next scan (the launcher itself is idempotent).
        pass


def _scan_one_project(project_dir: Path) -> dict | None:
    """Classify a single project; return its scan entry or None if not runnable.

    Split out of ``scan_projects`` so its caller can isolate per-project
    failures: one locked / partially-written plan file skips that project,
    it does not abort (and crash) the whole unattended scan.
    """
    if not project_dir.is_dir():
        return None
    plans_dir = project_dir / "plans"
    if not plans_dir.is_dir():
        return None

    masters = sorted(plans_dir.glob("MASTER-*.md"))
    if not masters:
        return None

    # Reconcile pass: auto-flip any all-shipped master to status: shipped,
    # and keep registry rows honest (a stale row is what an external
    # consumer reads to decide whether work is finished).
    # Idempotent + best-effort (tolerates concurrent writes).
    for m in masters:
        try:
            reconcile_master_status(m, plans_dir)
            reconcile_master_registry(m, plans_dir)
        except OSError:
            pass

    # Dispatch verification for any shipped master that has not yet been
    # dispatched.  State-driven (marker file), not event-driven
    # (just_reconciled): a read that consumed the reconcile trigger (step 1's
    # defect) no longer blocks dispatch, and a failed dispatch is retried on
    # the next scan until the marker is written.
    for m in masters:
        try:
            m_text = m.read_text(encoding="utf-8-sig")
            m_fm = parse_frontmatter(m_text)
            if normalize_master_status(m_fm.get("status", "")) == "shipped":
                _dispatch_verification_on_drain(project_dir, m, plans_dir)
        except Exception:
            pass

    # --- pass 1: classify masters by status + runnable check ---
    active_ts: list[datetime] = []
    queued_ts: list[datetime] = []

    for master_path in masters:
        try:
            master_text = master_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue

        fm = parse_frontmatter(master_text)
        master_status = normalize_master_status(fm.get("status") or "")

        # Only masters with at least one runnable sub-plan are dispatched.
        # master_has_runnable (not master_has_nonshipped) prevents a master
        # whose only outstanding sub-plan is `blocked` from being dispatched
        # every poll — the blocked sub-plan is outstanding work, but nothing
        # the loop does will advance it until a human unblocks it.
        if not master_has_runnable(master_path, plans_dir):
            continue

        # Collect per-sub-plan timestamps for FIFO ordering.
        master_sub_ts: list[datetime] = []
        for fname in extract_subplan_files(master_text):
            sub_path = plans_dir / fname
            if not sub_path.exists():
                continue
            try:
                sub_text = sub_path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            sub_fm = parse_frontmatter(sub_text)
            # Skip non-runnable sub-plans (shipped, blocked, skipped) so
            # oldest_queued_ts reflects only real work. Without this, a
            # blocked sub-plan's timestamp would anchor the FIFO slot and
            # the project would appear to have queued work when it doesn't.
            if sub_fm.get("status", "pending") not in ("pending", "in-progress"):
                continue
            ts = _parse_ts(sub_fm.get("last_updated", ""))
            if ts is None:
                try:
                    ts = datetime.fromtimestamp(sub_path.stat().st_mtime)
                except OSError:
                    ts = datetime.min
            master_sub_ts.append(ts)

        if not master_sub_ts:
            continue

        oldest_sub = min(master_sub_ts)

        if master_status == "active":
            active_ts.append(oldest_sub)
        elif master_status == "queued":
            queued_ts.append(oldest_sub)
        # Masters with other statuses (e.g. draft, paused) are
        # not runnable by the queue model — skip them. `draft` =
        # authored-but-not-yet-released (the /ilk-plan authoring gate); it
        # is intentionally invisible to the autonomous scheduler.
        # Note: legacy `pending` is normalized to `queued` above.

    # --- pass 2: decide inclusion + FIFO timestamp ---
    # A project is included iff it has a runnable master.
    if not active_ts and not queued_ts:
        return None

    # Active master wins for FIFO timestamp; else next-to-promote queued.
    has_active = bool(active_ts)
    if has_active:
        oldest = min(active_ts)
    else:
        oldest = min(queued_ts)

    # Find the active master's filename for the ILK_MASTER pin.
    active_master_name = None
    if has_active:
        for master_path in masters:
            try:
                master_text = master_path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            fm = parse_frontmatter(master_text)
            if normalize_master_status(fm.get("status") or "") == "active":
                active_master_name = master_path.name
                break

    return {
        "key": project_dir.name,
        "path": str(project_dir),
        "repo_path": resolve_repo_path(project_dir, project_dir.name),
        "oldest_queued_ts": oldest.isoformat(),
        "has_active_master": has_active,
        "active_master_name": active_master_name,
    }


def main() -> int:
    # Force UTF-8 on stdout/stderr. The scheduler captures our stdout (the JSON
    # project list) and reads it back as UTF-8. On a zh-CN console Python
    # defaults stdout to GBK (cp936); a non-ASCII byte in any field then makes
    # `print(json.dumps(..., ensure_ascii=False))` die with UnicodeEncodeError,
    # whose traceback — under the scheduler's `$ErrorActionPreference='Stop'`
    # — killed the daemon. Reconfiguring to UTF-8 makes the JSON survive any
    # non-ASCII path/title. Mirrors run_local_checks.main().
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    scan_errors_only = "--scan-errors" in sys.argv[1:]

    projects = scan_projects()
    if scan_errors_only:
        # Print ONLY the per-project scan failures. Separate mode rather than a
        # changed default payload: scheduler.sh, scheduler.ps1 and both
        # test_scheduler suites parse stdout as a bare project array, and a
        # wrapped object would break all four.
        print(json.dumps(SCAN_ERRORS, indent=2, ensure_ascii=False))
        return 0

    print(json.dumps(projects, indent=2, ensure_ascii=False))
    # A scan that could not look at every project is not the same as a scan
    # that found no work. Say so on stderr in one summary line; callers that
    # need the detail re-invoke with --scan-errors.
    if SCAN_ERRORS:
        keys = ", ".join(e["key"] for e in SCAN_ERRORS)
        print(
            f"[scan-error] {len(SCAN_ERRORS)} project(s) could not be scanned: "
            f"{keys}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
