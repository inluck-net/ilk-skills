"""Triage backlog emitter — turns triage outcomes into improvement candidates.

Part of sub-plan a-triaged-failure-becomes-an-improvement-candidate.

Stdlib only; imports ``improvement_backlog`` by ``sys.path`` relative to
``__file__``, as ``build_task.py:18-28`` does.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# Ensure the feedback scripts dir is importable for improvement_backlog.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "ilk-feedback" / "scripts"))

from improvement_backlog import add_candidate

# Ensure the loop scripts dir is importable for ilk_audit and ilk_paths.
sys.path.insert(0, str(_HERE.parent.parent / "ilk-loop" / "scripts"))

from ilk_audit import write_audit
from ilk_paths import ilk_data_root


# ── errors ───────────────────────────────────────────────────────────────────


class BacklogReadError(Exception):
    """Raised when the candidates file exists but is not a valid JSON list."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"backlog file is not a valid JSON list: {path}")


# ── signature ────────────────────────────────────────────────────────────────

_PY_TOKEN = re.compile(r"([\w/]+\.py)")


def signature(evidence: dict[str, Any], decision: dict[str, Any]) -> str:
    """Compute a 16-char hex signature from evidence + decision.

    Format: sha256 hex, first 16 chars, of ``"<cls>|<action>|<target>"``
    where:
    - ``cls`` = the last-exit ``state`` (else the postmortem classification,
      else ``"-"``)
    - ``action`` = the applied action (``"park-and-escalate"`` for every
      escalation)
    - ``target`` = the first failing node id from the run's gate-history
      attribution, else the first ``*.py`` token of ``failed_check.command``,
      else ``failed_check.slug``, else ``"-"``
    """
    # Classification
    last_exit = evidence.get("last_exit", {})
    cls = (
        last_exit.get("state")
        or evidence.get("postmortem_classification")
        or "-"
    )

    # Action
    action = decision.get("action", "-")

    # Target
    target = "-"
    gate_history = evidence.get("gate_history", [])
    for row in gate_history:
        attribution = row.get("attribution", {})
        nodes = attribution.get("nodes", [])
        if nodes and nodes[0].get("id"):
            target = nodes[0]["id"]
            break
    if target == "-":
        failed_check = last_exit.get("failed_check", {})
        command = failed_check.get("command", "")
        m = _PY_TOKEN.search(command)
        if m:
            target = m.group(1)
        elif failed_check.get("slug"):
            target = failed_check["slug"]

    raw = f"{cls}|{action}|{target}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── backlog I/O ──────────────────────────────────────────────────────────────


def read_backlog_strict(backlog_dir: Path) -> list[dict[str, Any]]:
    """Read the candidates file strictly.

    A missing file returns ``[]``.  A file that is not a JSON list raises
    :class:`BacklogReadError`.
    """
    p = backlog_dir / "candidates.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    raise BacklogReadError(p)


# ── emit ─────────────────────────────────────────────────────────────────────


def emit(
    *,
    project_key: str,
    run_id: str,
    outcome: str,
    decision: dict[str, Any],
    evidence: dict[str, Any],
    master: str | None = None,
    backlog_dir: Path | None = None,
) -> dict[str, Any]:
    """Emit a triage outcome as an improvement candidate.

    ``outcome`` must be ``"applied"`` or ``"escalated"``.

    Calls ``read_backlog_strict`` first (on ``BacklogReadError``: writes no
    candidate, re-raises).  Then calls ``add_candidate`` with the computed
    signature as ``source_id``.

    Holds an exclusive ``fcntl.flock`` on
    ``<backlog_dir>/candidates.json.lock`` around the read and the add.

    Writes a ``candidate-emitted`` audit row and returns the entry dict.
    """
    if outcome not in ("applied", "escalated"):
        raise ValueError(f"outcome must be 'applied' or 'escalated', got {outcome!r}")

    if backlog_dir is None:
        backlog_dir = ilk_data_root() / "ilk-skills-improvements"
    else:
        backlog_dir = Path(backlog_dir)

    sig = signature(evidence, decision)

    # Acquire exclusive lock
    lock_path = backlog_dir / "candidates.json.lock"
    backlog_dir.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Strict read first — refuse corrupt files
        existing_entries = read_backlog_strict(backlog_dir)

        # Find existing entry with source="triage" and this source_id
        existing_entry: dict[str, Any] | None = None
        for e in existing_entries:
            if e.get("source") == "triage" and e.get("source_id") == sig:
                existing_entry = e
                break

        # Compute counters from existing entry
        escalations = 0
        applied = 0
        applied_in_master = 0
        urgent = False
        if existing_entry:
            rel = existing_entry.get("relations", {})
            escalations = rel.get("escalations", 0)
            applied = rel.get("applied", 0)
            applied_in_master = rel.get("applied_in_master", 0)

        if outcome == "escalated":
            escalations += 1
        else:
            applied += 1
            if master:
                if existing_entry and existing_entry.get("relations", {}).get("master") == master:
                    applied_in_master += 1
                else:
                    applied_in_master = 1
            if applied_in_master >= 2:
                urgent = True

        # Build title
        last_exit = evidence.get("last_exit", {})
        cls = (
            last_exit.get("state")
            or evidence.get("postmortem_classification")
            or "-"
        )
        action = decision.get("action", "-")
        target = "-"
        gate_history = evidence.get("gate_history", [])
        for row in gate_history:
            attribution = row.get("attribution", {})
            nodes = attribution.get("nodes", [])
            if nodes and nodes[0].get("id"):
                target = nodes[0]["id"]
                break
        if target == "-":
            failed_check = last_exit.get("failed_check", {})
            command = failed_check.get("command", "")
            m = _PY_TOKEN.search(command)
            if m:
                target = m.group(1)
            elif failed_check.get("slug"):
                target = failed_check["slug"]

        title = f"triage {action}: {cls} at {target}"[:120]

        # Build evidence dict
        failed_check = last_exit.get("failed_check", {})
        head_sha = evidence.get("head_sha", "")
        basis = decision.get("basis", "")
        falsifier = decision.get("falsifier", "")
        ev = {
            "project": project_key,
            "run_id": run_id,
            "state": last_exit.get("state", ""),
            "failed_check_slug": failed_check.get("slug", ""),
            "failed_check_step": failed_check.get("step", ""),
            "failed_check_command": failed_check.get("command", "")[:600],
            "head_sha": head_sha,
            "basis": str(basis)[:600],
            "falsifier": str(falsifier)[:600],
        }

        # Build relations
        rels: dict[str, Any] = {
            "run_id": run_id,
            "slug": decision.get("slug", ""),
            "commit": head_sha,
            "master": master or "",
            "action": action,
            "escalations": escalations,
            "applied": applied,
            "applied_in_master": applied_in_master,
        }
        rels["urgent"] = urgent

        # Gap text (finding, cut to 1500 chars)
        gap = str(decision.get("finding", ""))[:1500]

        # Proposed fix (first line of finding)
        proposed_fix = str(decision.get("finding", "")).split("\n")[0][:200]

        # Severity and leverage
        severity = "high" if outcome == "escalated" else "medium"
        leverage = "medium"

        # Call add_candidate
        entry = add_candidate(
            title=title,
            kind="toolkit",
            gap=gap,
            evidence=ev,
            proposed_fix=proposed_fix,
            severity=severity,
            leverage=leverage,
            source="triage",
            source_id=sig,
            relations=rels,
            backlog_dir=backlog_dir,
        )

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

    # Write audit row
    data_root = ilk_data_root()
    data_dir = data_root / "projects" / project_key
    write_audit(
        "candidate-emitted",
        project_key,
        root=data_dir,
        id=entry.id,
        signature=sig,
        outcome=outcome,
        seen_count=entry.seen_count,
    )

    return entry.to_dict()