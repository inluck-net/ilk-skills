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
from contextvars import ContextVar

_triage_record=ContextVar("triage_record",default=None)

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


def _append_finding(
    plan_path: Path,
    data_dir: Path,
    *,
    run_id: str,
    finding: str,
    basis: str,
    falsifier: str,
    rule_based: bool = False,
) -> None:
    """Append a ``#### Triage <ts> (run <id>)`` block under ``## Findings``."""
    from plan_state import append_findings,TransitionError
    _assert_write_allowed(plan_path, data_dir)
    raw=plan_path.read_bytes()
    fm=parse_frontmatter(raw.decode('utf-8-sig'))
    if not fm.get('status'):raise TransitionError('triage target status is missing')
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    block = (
        f"#### Triage {ts} (run {run_id}){' (rule)' if rule_based else ''}\n"
        f"- Finding: {finding}\n"
        f"- Basis: {basis}\n"
        f"- Falsifier: {falsifier}\n"
    )
    append_findings(plan_path,block,
               actor='triage',reason='stopped-run triage findings',
               expected_digest=hashlib.sha256(raw).hexdigest(),
               **_record_transition_kwargs(plan_path,raw))


def _set_plan_status(
    plan_path: Path,
    data_dir: Path,
    *,
    status: str | None = None,
    current_step: int | None = None,
) -> None:
    """Compare-and-set triage state through the shared durable transition API."""
    from plan_state import transition
    _assert_write_allowed(plan_path, data_dir)
    raw=plan_path.read_bytes()
    fm=parse_frontmatter(raw.decode('utf-8-sig'))
    wanted={}
    if status is not None and 'status' in fm:wanted['status']=status
    if current_step is not None and 'current_step' in fm:wanted['current_step']=current_step
    if not wanted:return
    if 'last_updated' in fm:
        wanted['last_updated']=datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d')
    transition(plan_path,{key:fm[key] for key in wanted},wanted,
               actor='triage',reason='validated stopped-run triage state correction',
               expected_digest=hashlib.sha256(raw).hexdigest(),
               **_record_transition_kwargs(plan_path,raw))


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


def _target_from_records(data_dir,plans_dir,run_id):
    """Consume the run's latest frozen dispatch; open rows are diagnostic only."""
    from dispatch_target import TargetError
    from iteration_context import read_dispatch,ContextError,plan_snapshot
    path=data_dir/'logs/.ilk-loop.log'
    if not path.exists():return None
    records=[]
    try:lines=path.read_text(encoding='utf-8-sig').splitlines()
    except OSError as exc:raise TargetError('triage loop record is unreadable') from exc
    for line in lines:
        if not line.strip():continue
        try:row=json.loads(line)
        except ValueError as exc:raise TargetError('triage loop record is corrupt') from exc
        if not isinstance(row,dict):raise TargetError('triage loop record is invalid')
        if row.get('run_id')!=run_id or row.get('record_phase') not in {'open','close'}:continue
        context=row.get('context')
        if context is None:raise TargetError('triage typed record has no frozen context')
        if (not isinstance(context,dict) or type(context.get('schema_version')) is not int
                or context['schema_version']!=1 or context.get('run_id')!=run_id
                or type(context.get('iteration')) is not int or context['iteration']<0
                or type(row.get('iteration')) is not int or context['iteration']!=row.get('iteration')
                or context.get('plans_dir')!=str(plans_dir.resolve())):
            raise TargetError('triage frozen record identity contradicts requested run/project')
        try:selected=read_dispatch(context)
        except (ContextError,KeyError,TypeError) as exc:raise TargetError('triage frozen dispatch is invalid') from exc
        if not isinstance(selected.get('slug'),str) or not selected['slug']:
            raise TargetError('triage frozen slug is invalid')
        if selected.get('master_path') is not None and (not isinstance(selected['master_path'],str) or not Path(selected['master_path']).is_absolute()):
            raise TargetError('triage frozen master identity is invalid')
        if context.get('master_snapshot') is not None and not isinstance(context['master_snapshot'],dict):
            raise TargetError('triage frozen master snapshot is invalid')
        records.append((context,selected,row['record_phase']))
    if not records:return None
    latest=max(context['iteration'] for context,_,_ in records)
    records=[row for row in records if row[0]['iteration']==latest]
    identities={(selected.get('slug'),selected.get('step'),selected.get('subplan_path'),selected.get('master_path'),
                 (context.get('plans',{}).get(Path(selected['subplan_path']).name) or {}).get('fingerprint'),
                 (context.get('master_snapshot') or {}).get('fingerprint'))
                for context,selected,_ in records}
    if len(identities)!=1:raise TargetError('triage frozen dispatch records contradict one another')
    context,selected,phase=next((row for row in reversed(records) if row[2]=='close'),records[-1])
    path=Path(selected['subplan_path']).resolve()
    fingerprint=context['plans'][path.name].get('fingerprint')
    if (not isinstance(fingerprint,str) or not re.fullmatch('[0-9a-f]{64}',fingerprint)
            or plan_snapshot(path).get('fingerprint')!=fingerprint):
        raise TargetError('triage frozen plan contract changed or disappeared')
    guards={}
    if selected.get('master_path'):
        master=Path(selected['master_path']).resolve()
        snapshot=context.get('master_snapshot') or {}
        if (master.parent!=plans_dir.resolve() or snapshot.get('path')!=str(master)
                or not isinstance(snapshot.get('fingerprint'),str)
                or not re.fullmatch('[0-9a-f]{64}',snapshot['fingerprint'])
                or plan_snapshot(master).get('fingerprint')!=snapshot['fingerprint']):
            raise TargetError('triage frozen master contract changed or disappeared')
        state=parse_frontmatter(master.read_text(encoding='utf-8-sig'))
        guards[master]={'status':state.get('status'),'hold':state.get('hold')}
    return {'context':context,'selected':selected,'guards':guards,'phase':phase}


def _record_transition_kwargs(plan_path,raw):
    """Check the recorded contract and guard its master under the shared lock."""
    record=_triage_record.get()
    if record is None:return {}
    from plan_fingerprint import surface
    from plan_state import TransitionError
    expected=record['context']['plans'].get(plan_path.name) or {}
    actual=hashlib.sha256(surface(raw.decode('utf-8-sig'),master=False).encode()).hexdigest()
    if expected.get('path')!=str(plan_path.resolve()) or expected.get('fingerprint')!=actual:
        raise TransitionError('triage recorded plan contract changed')
    return {'guards':record['guards']}


def _exact_target(plans_dir,slug):
    from dispatch_target import identities,TargetError
    if not isinstance(slug,str) or not slug.strip():raise TargetError('triage slug is invalid')
    matches=[]
    for path in sorted(plans_dir.glob('*.md')):
        if path.name.startswith('MASTER-'):continue
        if path.resolve().parent!=plans_dir.resolve():raise TargetError('triage plan escapes plans root')
        if slug in identities(path):matches.append(path)
    if len(matches)>1:raise TargetError('triage exact identity is ambiguous')
    return matches[0] if matches else None


def _find_active_subplan(plans_dir: Path) -> Path | None:
    """Legacy fallback may attach a diagnostic only to a unique active plan."""
    from dispatch_target import TargetError
    matches=[path for path in sorted(plans_dir.glob('*.md'))
             if not path.name.startswith('MASTER-') and _read_plan_frontmatter(path).get('status')=='in-progress']
    if len(matches)>1:raise TargetError('legacy triage active target is ambiguous')
    return matches[0] if matches else None


def _resolve_target_slug(
    decision: dict[str, Any],
    data_dir: Path,
    plans_dir: Path,
) -> tuple[Path | None, str | None]:
    """Resolve the target sub-plan path and slug.

    Returns ``(plan_path, slug)`` — both None when nothing can be resolved.
    """
    slug = decision.get("slug")
    record=_triage_record.get()
    if record is not None:
        from dispatch_target import TargetError,identities
        selected=record['selected'];path=Path(selected['subplan_path'])
        if slug and slug not in identities(path):raise TargetError('triage decision contradicts frozen dispatch')
        return path,selected['slug']
    if slug:
        selected=_exact_target(plans_dir,slug)
        if selected:return selected,slug
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
            selected=_exact_target(plans_dir,failed_slug)
            if selected and _read_plan_frontmatter(selected).get('status')!='shipped':
                return selected,failed_slug

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


def validate(decision,plans_dir,*,repo=None,target_path=None):
    """Expose ambiguous target failures as validation problems, never guesses."""
    from dispatch_target import TargetError
    try:return _validate(decision,plans_dir,repo=repo,target_path=target_path)
    except TargetError as exc:return ['triage target refused: '+str(exc)]


def _validate(
    decision: dict[str, Any], plans_dir: Path, *, repo: Path | None = None, target_path: Path | None = None,
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
        selected=target_path or _exact_target(plans_dir,slug)
        if selected is None:
            problems.append(f"slug {slug!r} not found in plans_dir")

    if action == "reopen":
        if slug and step is not None:
            selected=target_path or _exact_target(plans_dir,slug)
            if selected:
                fm = _read_plan_frontmatter(selected)
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


def apply(decision, data_dir, *, run_id, data_root=None, repo=None):
    """Return a recorded refusal if a shared state transition loses its CAS."""
    from plan_state import TransitionError
    from dispatch_target import TargetError
    if os.environ.get('ILK_WORKER_SESSION','').strip():
        raise TransitionError('worker cannot apply control-plane triage')
    token=None
    try:
        from run_registry import project_status,RegistryError
        try:live=project_status(data_dir/'runtime/launcher')
        except (RegistryError,OSError,ValueError) as exc:raise TargetError('triage project ownership is unverified') from exc
        if live['busy']:raise TargetError('triage project already has a live or launching run')
        token=_triage_record.set(_target_from_records(data_dir,_find_plans_dir(data_dir),run_id))
        return _apply(decision,data_dir,run_id=run_id,data_root=data_root,repo=repo)
    except (TransitionError,TargetError) as exc:
        reason=('state_transition_refused: ' if isinstance(exc,TransitionError) else 'triage_target_refused: ')+str(exc)
        write_audit('triage-refused',_project_name(data_dir),root=data_dir,
                    run_id=run_id,reason=reason)
        return {'action':'refused','reason':reason,'audit_kind':'triage-refused'}
    finally:
        if token is not None:_triage_record.reset(token)


def _apply(
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
    problems = validate(decision, plans_dir, repo=repo,target_path=target_path)
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
            rule_based=decision.get("source")=="rule",
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
        fm = _read_plan_frontmatter(target_path)
        if fm.get("status") == "blocked":
            _set_plan_status(target_path,data_dir,status="pending")
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