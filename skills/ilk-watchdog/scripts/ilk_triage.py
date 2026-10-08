#!/usr/bin/env python3
"""Triage agent for stopped runs — builds evidence and asks the manager home.

Part of sub-plan a-stopped-run-gets-a-diagnosis.  Stdlib only; imports
``ilk_paths`` and ``ilk_audit`` via ``sys.path`` from
``skills/ilk-loop/scripts``, resolved relative to ``__file__``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the scripts dirs are importable.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "ilk-loop" / "scripts"))

from ilk_audit import write_audit
from ilk_paths import ilk_data_root
from plan_status import parse_frontmatter

try:
    from integrity_violation import parse_file as _parse_integrity_violations
except ImportError:
    _parse_integrity_violations = None


# ── constants ────────────────────────────────────────────────────────────────

VALID_ACTIONS = frozenset({"reopen", "amend", "ack-and-relaunch", "park-and-escalate"})
WORKER_MODEL_PATTERN = re.compile(r"mimo|glm", re.IGNORECASE)
DRIVER_LOG_PATTERN = re.compile(
    r"local_checks|amended|DBG|Loop ended|red-owner|ship-integrity.*FAIL|ship-integrity VIOLATION|\[gates\]"
)
WORKER_HOME_PATTERN = re.compile(r"\.claude-worker")


# ── evidence builder ─────────────────────────────────────────────────────────


def build_evidence(data_dir: Path, run_id: str) -> dict[str, Any]:
    """Build an evidence pack from the run's records.

    Returns a dict with keys: last_exit, gate_history, postmortem_classification,
    iter_tail, driver_log, git_status, git_log, subplan_frontmatter,
    subplan_findings, subplan_slug, subplan_slug_source, subplan_candidates,
    missing_sources.
    """
    missing: list[str] = []
    evidence: dict[str, Any] = {"missing_sources": missing}

    # 1. last-exit.json — matched by run_id (contract A)
    last_exit_path = data_dir / "runtime" / "launcher" / "last-exit.json"
    if last_exit_path.exists():
        sentinel = json.loads(last_exit_path.read_text(encoding="utf-8"))
        sentinel_run_id = sentinel.get("run_id")
        if sentinel_run_id == run_id:
            evidence["last_exit"] = sentinel
        else:
            # Superseded: another run wrote this sentinel after ours stopped.
            evidence["last_exit"] = {
                "superseded": True,
                "sentinel_run_id": sentinel_run_id,
                "project_path": sentinel.get("project_path"),
            }
            missing.append(f"last-exit.json superseded (belongs to {sentinel_run_id})")
    else:
        missing.append(str(last_exit_path))
        evidence["last_exit"] = {"missing": str(last_exit_path)}

    # 2. gate-history rows for this run_id
    gate_history_path = data_dir / "runtime" / "launcher" / "gate-history.jsonl"
    if gate_history_path.exists():
        rows = []
        for line in gate_history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if row.get("run_id") == run_id:
                    rows.append(row)
            except json.JSONDecodeError:
                pass
        evidence["gate_history"] = rows
    else:
        missing.append(str(gate_history_path))
        evidence["gate_history"] = []

    # 3. postmortem classification
    postmortem_path = data_dir / "runtime" / "launcher" / "postmortems" / f"{run_id}.md"
    if postmortem_path.exists():
        text = postmortem_path.read_text(encoding="utf-8")
        # Parse frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                frontmatter = text[3:end]
                for line in frontmatter.splitlines():
                    if line.startswith("classification:"):
                        evidence["postmortem_classification"] = line.split(":", 1)[1].strip().strip('"')
                        break
        if "postmortem_classification" not in evidence:
            evidence["postmortem_classification"] = None
    else:
        missing.append(str(postmortem_path))
        evidence["postmortem_classification"] = None

    # 4. last 120 lines of the last iter-*.log
    iter_logs = sorted((data_dir / "logs" / "runs" / run_id).glob("iter-*.log")) if (data_dir / "logs" / "runs" / run_id).exists() else []
    if iter_logs:
        last_log = iter_logs[-1]
        lines = last_log.read_text(encoding="utf-8").splitlines()
        evidence["iter_tail"] = lines[-120:]
    else:
        missing.append(f"{data_dir}/logs/runs/{run_id}/iter-*.log")
        evidence["iter_tail"] = []

    # 5. driver-log lines matching pattern (cap 200)
    launcher_logs = list((data_dir / "logs" / "launcher").glob(f"*-{run_id}.log")) if (data_dir / "logs" / "launcher").exists() else []
    if launcher_logs:
        driver_log = launcher_logs[0]
        matching_lines = []
        for line in driver_log.read_text(encoding="utf-8").splitlines():
            if DRIVER_LOG_PATTERN.search(line):
                matching_lines.append(line)
                if len(matching_lines) >= 200:
                    break
        evidence["driver_log"] = matching_lines
        driver_log_path = driver_log
    else:
        missing.append(f"{data_dir}/logs/launcher/*-{run_id}.log")
        evidence["driver_log"] = []
        driver_log_path = None

    # 5b. integrity violations from the runner's VIOLATION lines
    if _parse_integrity_violations is not None and driver_log_path is not None:
        evidence["integrity_violations"] = _parse_integrity_violations(driver_log_path)
    else:
        evidence["integrity_violations"] = []

    # 6. git status and log (requires project_path from last_exit)
    project_path = evidence.get("last_exit", {}).get("project_path")
    if project_path and Path(project_path).exists():
        try:
            result = subprocess.run(
                ["git", "-C", project_path, "status", "--porcelain"],
                capture_output=True, text=True, timeout=10
            )
            evidence["git_status"] = result.stdout.splitlines()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            evidence["git_status"] = []

        try:
            result = subprocess.run(
                ["git", "-C", project_path, "log", "--oneline", "-8"],
                capture_output=True, text=True, timeout=10
            )
            evidence["git_log"] = result.stdout.splitlines()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            evidence["git_log"] = []
    else:
        evidence["git_status"] = []
        evidence["git_log"] = []

    # 7. slug from the run's own records (contract B), first hit wins
    slug: str | None = None
    slug_source: str | None = None
    sentinel_matched = evidence.get("last_exit", {}).get("run_id") == run_id

    # B1: failed_check.slug from the sentinel, only when sentinel matched this run
    if sentinel_matched:
        fc_slug = evidence.get("last_exit", {}).get("failed_check", {}).get("slug")
        if fc_slug:
            slug = fc_slug
            slug_source = "last_exit"

    # B2/B3: last gate-history row for this run with a non-pass outcome, then any outcome
    if slug is None:
        gh_rows = evidence.get("gate_history", [])
        fail_row = None
        any_row = None
        for row in gh_rows:
            if row.get("run_id") == run_id:
                any_row = row
                if row.get("outcome") != "pass":
                    fail_row = row
        chosen = fail_row or any_row
        if chosen and chosen.get("slug"):
            slug = chosen["slug"]
            slug_source = "gate_history_fail" if fail_row else "gate_history"

    # B4: nothing — no sub-plan attached
    if slug is None:
        evidence["subplan_frontmatter"] = ""
        evidence["subplan_findings"] = ""
        evidence["subplan_slug"] = None
        evidence["subplan_slug_source"] = None
        missing.append(f"sub-plan for run_id {run_id}")
    else:
        evidence["subplan_slug"] = slug
        evidence["subplan_slug_source"] = slug_source

        # C. Locate the file by exact slug match on plan: frontmatter
        plans_dir = data_dir / "plans"
        found_text: str | None = None
        candidates: list[str] = []
        if plans_dir.exists():
            for plan_file in sorted(plans_dir.glob("*.md")):
                if plan_file.name.startswith("MASTER-"):
                    continue
                text = plan_file.read_text(encoding="utf-8")
                if text.startswith("---"):
                    fm = parse_frontmatter(text)
                    if fm.get("plan") == slug:
                        candidates.append(plan_file.name)
                        found_text = text  # last match wins (sorted ⇒ newest)
        if candidates:
            evidence["subplan_candidates"] = candidates
            if found_text:
                evidence["subplan_frontmatter"] = found_text.split("---", 2)[1] if found_text.startswith("---") else ""
                findings_match = re.search(r"## Findings\n(.*?)(?=\n## |\Z)", found_text, re.DOTALL)
                evidence["subplan_findings"] = findings_match.group(1).strip() if findings_match else ""
        else:
            evidence["subplan_frontmatter"] = ""
            evidence["subplan_findings"] = ""
            missing.append(f"sub-plan file for slug {slug}")

    return evidence


# ── decision maker ───────────────────────────────────────────────────────────


def decide(
    evidence: dict[str, Any],
    *,
    home: Path,
    timeout_s: int = 600,
    skip_audit: bool = False,
) -> dict[str, Any]:
    """Run claude -p on the triage home and parse the decision.

    Returns a dict with keys: action, slug, step, finding, basis, falsifier,
    model, reason (for park-and-escalate).

    When *skip_audit* is True the ``triage-decided`` audit row is suppressed
    (the caller is responsible for writing the terminal row).
    """
    # Check for worker home
    if WORKER_HOME_PATTERN.search(str(home)):
        return {
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "worker home detected",
            "basis": f"home path {home} matches worker pattern",
            "falsifier": "home is a manager home",
            "model": None,
            "reason": "worker_home",
        }

    # Build the prompt
    prompt = f"""You are a triage agent. Analyze the evidence from a stopped run and decide one action.

Vocabulary (choose exactly one):
- reopen: the run failed due to a transient issue; reopen and retry
- amend: the plan needs a small fix; amend it
- ack-and-relaunch: acknowledge the failure and relaunch
- park-and-escalate: cannot resolve; park and escalate to human

Hard limits:
- You must return exactly one JSON object
- The JSON must have: action, slug, step, finding, basis, falsifier
- finding, basis, falsifier must be non-empty
- reopen requires slug and step
- amend requires slug

Evidence:
{json.dumps(evidence, indent=2)}

Return ONLY a JSON object, nothing else."""

    # Run claude -p
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(home)

    try:
        result = subprocess.run(
            [
                # The prompt goes right after -p: --allowedTools is variadic
                # and would swallow a trailing prompt as a tool name.
                "claude", "-p", prompt,
                "--output-format", "stream-json",
                "--verbose",
                "--allowedTools", "Read", "Grep", "Glob",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=evidence.get("last_exit", {}).get("project_path", "."),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "claude timed out",
            "basis": f"timeout after {timeout_s}s",
            "falsifier": "claude responds within timeout",
            "model": None,
            "reason": "timeout",
        }

    if result.returncode != 0:
        return {
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "claude exited non-zero",
            "basis": f"exit code {result.returncode}",
            "falsifier": "claude exits 0",
            "model": None,
            "reason": "non_zero_exit",
        }

    # Parse stream-json output
    model = None
    result_text = None

    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "system" and event.get("subtype") == "init":
            model = event.get("model")
        elif event.get("type") == "result":
            result_text = event.get("result")

    # Check for mimo/glm model
    if model and WORKER_MODEL_PATTERN.search(model):
        return {
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "worker model detected",
            "basis": f"init model {model} matches worker pattern",
            "falsifier": "model is an official provider",
            "model": model,
            "reason": "mimo_model",
        }

    if not result_text:
        return {
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "no result from claude",
            "basis": "result text is empty",
            "falsifier": "claude returns a result",
            "model": model,
            "reason": "no_json",
        }

    # Try to parse exactly one JSON object from the result text
    # First, try direct JSON parse
    try:
        decision = json.loads(result_text)
        if not isinstance(decision, dict):
            return {
                "action": "park-and-escalate",
                "slug": None,
                "step": None,
                "finding": "result is not a JSON object",
                "basis": f"result type: {type(decision).__name__}",
                "falsifier": "result is a JSON object",
                "model": model,
                "reason": "no_json",
            }
    except json.JSONDecodeError:
        # Try to extract JSON from the text
        json_matches = re.findall(r'\{[^{}]+\}', result_text)
        if len(json_matches) != 1:
            return {
                "action": "park-and-escalate",
                "slug": None,
                "step": None,
                "finding": f"expected 1 JSON object, found {len(json_matches)}",
                "basis": f"result contains {len(json_matches)} JSON objects",
                "falsifier": "result contains exactly 1 JSON object",
                "model": model,
                "reason": "two_json" if len(json_matches) > 1 else "no_json",
            }
        try:
            decision = json.loads(json_matches[0])
        except json.JSONDecodeError:
            return {
                "action": "park-and-escalate",
                "slug": None,
                "step": None,
                "finding": "could not parse JSON from result",
                "basis": "JSON parse failed",
                "falsifier": "result contains valid JSON",
                "model": model,
                "reason": "no_json",
            }

    # Validate the decision
    action = decision.get("action")
    if action not in VALID_ACTIONS:
        return {
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": f"unknown action: {action}",
            "basis": f"action {action!r} not in vocabulary",
            "falsifier": "action is in vocabulary",
            "model": model,
            "reason": "unknown_action",
        }

    # Validate required fields
    finding = decision.get("finding", "")
    basis = decision.get("basis", "")
    falsifier = decision.get("falsifier", "")

    if not finding or not basis or not falsifier:
        return {
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "missing required field",
            "basis": f"finding={finding!r}, basis={basis!r}, falsifier={falsifier!r}",
            "falsifier": "all required fields are non-empty",
            "model": model,
            "reason": "empty_finding" if not finding else "no_json",
        }

    # Validate action-specific fields
    slug = decision.get("slug")
    step = decision.get("step")

    if action == "reopen" and (slug is None or step is None):
        return {
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "reopen requires slug and step",
            "basis": f"slug={slug!r}, step={step!r}",
            "falsifier": "reopen has slug and step",
            "model": model,
            "reason": "no_json",
        }

    if action == "amend" and slug is None:
        return {
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "amend requires slug",
            "basis": f"slug={slug!r}",
            "falsifier": "amend has slug",
            "model": model,
            "reason": "no_json",
        }

    # Valid decision
    return {
        "action": action,
        "slug": slug,
        "step": step,
        "finding": finding,
        "basis": basis,
        "falsifier": falsifier,
        "model": model,
    }


# ── run (diagnose → validate → apply) ────────────────────────────────────────


def resolve_triage_home(home: Path | None) -> Path:
    """The Claude home triage decides on: --home, else $ILK_TRIAGE_HOME, else
    ~/.claude-triage.

    Never the manager home: ~/.claude-manager runs glm-5.3 by design
    (docs/architecture/model-worker-framework.md:54) and ``decide`` refuses
    worker-pattern models, so a manager-home triage can only escalate
    (measured 2026-10-03). The triage home is a dedicated official-provider
    login, one per host.
    """
    if home:
        return Path(home)
    env_home = os.environ.get("ILK_TRIAGE_HOME", "").strip()
    if env_home:
        return Path(env_home)
    return Path.home() / ".claude-triage"


def _missing_home_decision(home: Path) -> dict[str, Any]:
    """Escalate rather than fall back to some other home."""
    return {
        "action": "park-and-escalate",
        "slug": None,
        "step": None,
        "finding": f"triage home {home} does not exist",
        "basis": "no official-provider Claude home to decide on",
        "falsifier": f"{home} exists with an official-provider login",
        "model": None,
        "reason": "no_triage_home",
    }


def _try_rule_first(
    data_dir: Path,
    *,
    run_id: str,
    plans_dir: Path,
    data_root: Path,
) -> dict[str, Any] | None:
    """Rule-first ack-and-relaunch for a plain owned red.

    When the stopped run's stop reason is ``local_checks_failed`` and
    ``gate-history.jsonl``'s last red row for the run names the (slug, step)
    and at least one failing node id, apply ``ack-and-relaunch`` WITHOUT a
    model call.

    Bound: fires at most twice in a row for the same (slug, step).  A third
    consecutive red returns ``None`` so the caller falls through to
    ``decide()``.
    """
    sentinel_path = data_dir / "runtime" / "launcher" / "last-exit.json"
    if not sentinel_path.exists():
        return None
    try:
        sentinel = json.loads(sentinel_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if sentinel.get("state") != "local_checks_failed":
        return None
    if sentinel.get("run_id") != run_id:
        return None

    fc = sentinel.get("failed_check") or {}
    slug = fc.get("slug")
    step = fc.get("step")
    if not slug or step is None:
        return None

    gate_path = data_dir / "runtime" / "launcher" / "gate-history.jsonl"
    if not gate_path.exists():
        return None

    last_red = None
    try:
        for line in gate_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if (row.get("run_id") == run_id
                    and row.get("outcome") == "fail"
                    and row.get("slug")):
                last_red = row
    except (json.JSONDecodeError, OSError):
        return None

    if last_red is None:
        return None

    node_ids = (last_red.get("attribution") or {}).get("node_ids") or []
    if not node_ids:
        return None

    # ── bound: at most twice for the same (slug, step) ──
    # Stored at data_root level so the counter is visible across project keys
    # that share the same data root (different runs of the same project).
    rule_state_path = data_root / "runtime" / "triage" / "rule-state.json"
    rule_state = {}
    if rule_state_path.exists():
        try:
            rule_state = json.loads(
                rule_state_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            pass

    rule_key = f"rule:{slug}:{step}"
    prev = rule_state.get(rule_key)
    consecutive_reds = prev.get("consecutive_reds", 0) if prev else 0
    if consecutive_reds >= 2:
        return None

    rule_state[rule_key] = {
        "consecutive_reds": consecutive_reds + 1,
        "run_id": run_id,
    }
    rule_state_path.parent.mkdir(parents=True, exist_ok=True)
    rule_state_path.write_text(
        json.dumps(rule_state, indent=2) + "\n", encoding="utf-8",
    )

    red_owner = last_red.get("red_owner")
    _write_rule_finding(
        plans_dir, data_dir,
        slug=slug, run_id=run_id,
        node_ids=node_ids, red_owner=red_owner,
    )

    return {
        "action": "ack-and-relaunch",
        "slug": slug,
        "step": step,
        "finding": (
            f"gate red — failing ids: {', '.join(node_ids)}"
            + (f"\nred-owner: {red_owner}" if red_owner else "")
        ),
        "basis": "local_checks_failed with known failing node ids",
        "falsifier": "gate green",
        "model": None,
    }


def _write_rule_finding(
    plans_dir: Path,
    data_dir: Path,
    *,
    slug: str,
    run_id: str,
    node_ids: list[str],
    red_owner: str | None,
) -> None:
    """Append a ``#### Triage <ts> (rule)`` block to the sub-plan's Findings."""
    matches = list(plans_dir.glob(f"*{slug}*.md"))
    if not matches:
        return
    plan_path = matches[0]
    text = plan_path.read_text(encoding="utf-8")

    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    ids_block = "\n".join(f"  - {nid}" for nid in node_ids)
    block = (
        f"\n#### Triage {ts} (rule)\n"
        f"- Failing ids:\n{ids_block}\n"
    )
    if red_owner:
        block += f"- Red-owner: {red_owner}\n"
    block += "- Fix the code, never the assertion.\n"

    if "## Findings" in text:
        text = text.replace("## Findings\n", "## Findings\n" + block, 1)
    else:
        text += f"\n## Findings\n{block}\n"

    plan_path.write_text(text, encoding="utf-8")


def run_triage(
    *,
    project_key: str,
    run_id: str,
    home: Path | None = None,
    timeout_s: int = 600,
) -> dict[str, Any]:
    """Full triage pipeline: diagnose, validate, apply.

    Every path writes exactly one terminal audit row:
    ``triage-applied``, ``escalated``, or ``triage-refused``.
    """
    from triage_apply import apply, validate

    data_root = ilk_data_root()
    data_dir = data_root / "projects" / project_key

    # Resolve home; a missing one escalates instead of falling back.
    resolved_home = resolve_triage_home(home)

    # ── rule-first: ack-and-relaunch for a plain owned red ──
    plans_dir = data_dir / "plans"
    rule_decision = _try_rule_first(
        data_dir, run_id=run_id, plans_dir=plans_dir, data_root=data_root,
    )
    if rule_decision is not None:
        result = apply(rule_decision, data_dir, run_id=run_id)
        return result

    # Build evidence and decide (skip the triage-decided audit row).
    evidence = build_evidence(data_dir, run_id)
    if not resolved_home.is_dir():
        decision = _missing_home_decision(resolved_home)
    else:
        decision = decide(evidence, home=resolved_home, timeout_s=timeout_s,
                          skip_audit=True)

    # Validate.
    plans_dir = data_dir / "plans"
    problems = validate(decision, plans_dir)
    if problems:
        reason = "; ".join(problems)
        decision = {
            "action": "park-and-escalate",
            "slug": decision.get("slug"),
            "step": decision.get("step"),
            "finding": f"validation failed: {reason}",
            "basis": reason,
            "falsifier": "decision passes validation",
        }

    # Apply (writes the terminal audit row).
    result = apply(decision, data_dir, run_id=run_id)

    # Emit improvement candidate when the outcome is applied or escalated.
    # An exception from emit is caught: write a candidate-emitted row with
    # ok: false, and leave the triage outcome and exit code unchanged.
    audit_kind = result.get("audit_kind")
    action = result.get("action")
    if audit_kind == "triage-applied" or action == "park-and-escalate":
        outcome = "escalated" if action == "park-and-escalate" else "applied"
        try:
            from triage_backlog import emit
            emit(
                project_key=project_key,
                run_id=run_id,
                outcome=outcome,
                decision=decision,
                evidence=evidence,
                backlog_dir=data_root / "ilk-skills-improvements",
            )
        except Exception as exc:
            try:
                write_audit(
                    "candidate-emitted", project_key,
                    root=data_dir,
                    ok=False,
                    error=str(exc)[:600],
                )
            except Exception:
                pass

    return result


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    """Entry point for the triage CLI."""
    parser = argparse.ArgumentParser(description="Triage agent for stopped runs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    diagnose_parser = subparsers.add_parser("diagnose", help="Diagnose a stopped run")
    diagnose_parser.add_argument("--project-key", required=True, help="Project key")
    diagnose_parser.add_argument("--run-id", required=True, help="Run ID")
    diagnose_parser.add_argument("--home", type=Path, help="Triage home (default: $ILK_TRIAGE_HOME, else ~/.claude-triage)")
    diagnose_parser.add_argument("--dry-run", action="store_true", help="Don't write audit row")
    diagnose_parser.add_argument("--json", action="store_true", help="Output JSON only")

    run_parser = subparsers.add_parser("run", help="Full triage: diagnose → validate → apply")
    run_parser.add_argument("--project-key", required=True, help="Project key")
    run_parser.add_argument("--run-id", required=True, help="Run ID")
    run_parser.add_argument("--home", type=Path, help="Triage home (default: $ILK_TRIAGE_HOME, else ~/.claude-triage)")
    run_parser.add_argument("--timeout-s", type=int, default=600, help="Timeout for claude -p")
    run_parser.add_argument("--json", action="store_true", help="Output JSON only")

    args = parser.parse_args(argv)

    if args.command == "diagnose":
        # Resolve data directory
        data_root = ilk_data_root()
        data_dir = data_root / "projects" / args.project_key

        # Check kill switch
        if (data_root / "triage.disabled").exists():
            write_audit("triage-refused", args.project_key, reason="kill_switch")
            if not args.json:
                print("Triage disabled (kill switch present)", file=sys.stderr)
            sys.exit(3)

        # Resolve home; a missing one escalates instead of falling back.
        home = resolve_triage_home(args.home)

        # Build evidence and decide
        start_time = time.time()
        if not home.is_dir():
            decision = _missing_home_decision(home)
        else:
            evidence = build_evidence(data_dir, args.run_id)
            decision = decide(evidence, home=home)
        elapsed = time.time() - start_time

        # Add model to decision for audit
        decision["elapsed_s"] = round(elapsed, 2)
        decision["missing_sources"] = evidence.get("missing_sources", [])

        # Write audit row unless dry-run
        if not args.dry_run:
            write_audit(
                "triage-decided",
                args.project_key,
                action=decision["action"],
                model=decision.get("model"),
                elapsed_s=decision["elapsed_s"],
                missing_sources=decision["missing_sources"],
            )

        # Output
        if args.json:
            print(json.dumps(decision, indent=2))
        else:
            print(f"Decision: {decision['action']}")
            if decision.get("slug"):
                print(f"Slug: {decision['slug']}")
            if decision.get("step") is not None:
                print(f"Step: {decision['step']}")
            print(f"Finding: {decision['finding']}")
            print(f"Basis: {decision['basis']}")
            print(f"Falsifier: {decision['falsifier']}")
            if decision.get("model"):
                print(f"Model: {decision['model']}")
            if decision.get("reason"):
                print(f"Reason: {decision['reason']}")

    elif args.command == "run":
        result = run_triage(
            project_key=args.project_key,
            run_id=args.run_id,
            home=args.home,
            timeout_s=args.timeout_s,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Action: {result.get('action')}")
            if result.get("reason"):
                print(f"Reason: {result['reason']}")
            if result.get("audit_kind"):
                print(f"Audit: {result['audit_kind']}")


if __name__ == "__main__":
    main()