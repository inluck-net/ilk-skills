#!/usr/bin/env python3
"""Mechanical audit of a shipped batch before a release train.

Part of sub-plan: a-shipped-batch-is-audited.

Usage:
    python3 batch_audit.py --project <repo> --run-id <id> [--json]

Exit codes:
    0  pass
    1  fail
    2  unmeasured (a required input is missing)

The audit checks four things, each producing {"name", "ok", "detail"}:
  1. scope:   git diff ⊆ scope_paths
  2. gate:    batch-gate.json verdict == pass and attributed == 0
  3. tamper:  no tamper patterns in iter logs
  4. masters: no foreign master mtimes inside the run window
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────────────
# The scheduler runs this file as a script (scheduler.sh:596), so sys.path[0]
# is ilk-ship/scripts and ilk_paths is not importable without this.  The tests
# never noticed: the root conftest puts every scripts dir on sys.path.

_HERE = Path(__file__).resolve().parent
_LOOP_SCRIPTS = _HERE.parent.parent / "ilk-loop" / "scripts"
if str(_LOOP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LOOP_SCRIPTS))

# ── Public API ──────────────────────────────────────────────────────────────


def audit(*, project: Path, data_dir: Path, run_id: str) -> dict:
    """Run the four-part audit and return a result dict.

    Returns {"verdict": "pass"|"fail"|"unmeasured", "checks": [...]}.
    Also writes the result to <data_dir>/logs/audits/<run_id>.json.
    """
    checks = []

    # Find the master and sub-plan
    master_file, sub_plans = _find_master_and_subplans(data_dir, run_id)
    if master_file is None:
        result = {
            "verdict": "unmeasured",
            "checks": [{"name": "master", "ok": False, "detail": "no shipped master found"}],
        }
        _write_audit(result, data_dir, run_id)
        return result

    # Extract base_sha from master front-matter
    master_content = master_file.read_text(encoding="utf-8")
    base_sha = _extract_frontmatter_field(master_content, "base_sha")

    # 1. Scope check
    checks.append(_check_scope(project, base_sha, sub_plans))

    # 2. Gate check
    checks.append(_check_gate(data_dir))

    # 3. Tamper check
    checks.append(_check_tamper(data_dir, run_id))

    # 4. Masters check
    checks.append(_check_masters(data_dir, master_file, run_id))

    # Determine verdict
    any_unmeasured = any(not c["ok"] and c["detail"] == "unmeasured" for c in checks)
    any_fail = any(not c["ok"] and c["detail"] != "unmeasured" for c in checks)

    if any_unmeasured and not any_fail:
        verdict = "unmeasured"
    elif any_fail:
        verdict = "fail"
    else:
        verdict = "pass"

    result = {"verdict": verdict, "checks": checks}
    _write_audit(result, data_dir, run_id)
    return result


# ── Scope check ─────────────────────────────────────────────────────────────


def _check_scope(project: Path, base_sha: str | None, sub_plans: list[dict]) -> dict:
    """Check that git diff is within scope_paths."""
    if not base_sha:
        return {"name": "scope", "ok": False, "detail": "unmeasured"}

    # Collect all scope_paths from sub-plans
    scope_paths = []
    for sp in sub_plans:
        scope_paths.extend(sp.get("scope_paths", []))

    if not scope_paths:
        return {"name": "scope", "ok": True, "detail": "no scope_paths defined"}

    # The batch's range ends at its LAST commit carrying one of its own
    # [plan:<slug>#...] trailers (normally the #ship marker), not at HEAD.
    # Judging base..HEAD failed every batch that another commit followed:
    # a direct release's changelog and fix (2026-10-08, backlog a47ea3a1).
    # Bounding the range, rather than keeping only trailered commits, still
    # judges an untrailered commit made INSIDE the batch.  With no trailered
    # commit in base..HEAD the end is unknown, so fall back to HEAD.
    end = _batch_end_sha(project, base_sha, [sp.get("slug", "") for sp in sub_plans]) or "HEAD"

    # Get changed files
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}..{end}"],
            cwd=project, capture_output=True, text=True, check=True,
        )
        changed_files = [f for f in result.stdout.strip().split("\n") if f]
    except subprocess.CalledProcessError:
        return {"name": "scope", "ok": False, "detail": "git diff failed"}

    # Check each changed file against scope_paths
    violations = []
    for f in changed_files:
        in_scope = False
        for sp in scope_paths:
            if sp.endswith("/"):
                if f.startswith(sp):
                    in_scope = True
                    break
            else:
                if f == sp or f.startswith(sp + "/"):
                    in_scope = True
                    break
        if not in_scope:
            violations.append(f)

    if violations:
        return {
            "name": "scope",
            "ok": False,
            "detail": f"{len(violations)} file(s) outside scope: {', '.join(violations[:5])}",
        }
    return {"name": "scope", "ok": True, "detail": f"{len(changed_files)} files in scope"}


def _batch_end_sha(project: Path, base_sha: str, slugs: list[str]) -> str | None:
    """Newest commit in base..HEAD whose message carries [plan:<slug>#...]
    for one of *slugs*, or None when there is none (or git fails)."""
    slugs = [s for s in slugs if s]
    if not slugs:
        return None
    try:
        out = subprocess.run(
            ["git", "log", "--format=%H%x1f%B%x1e", f"{base_sha}..HEAD"],
            cwd=project, capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None
    pat = re.compile(r"\[plan:(" + "|".join(re.escape(s) for s in slugs) + r")#")
    for rec in out.split("\x1e"):  # newest first
        sha, _, body = rec.strip().partition("\x1f")
        if sha and pat.search(body):
            return sha
    return None


# ── Gate check ──────────────────────────────────────────────────────────────


def _check_gate(data_dir: Path) -> dict:
    """Check batch-gate.json verdict and attributed count."""
    gate_file = data_dir / "runtime" / "batch-gate.json"
    if not gate_file.exists():
        return {"name": "gate", "ok": False, "detail": "unmeasured"}

    try:
        record = json.loads(gate_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"name": "gate", "ok": False, "detail": "unmeasured"}

    verdict = record.get("verdict")
    counts = record.get("counts", {})
    attributed = counts.get("attributed", 0) if isinstance(counts, dict) else 0

    if verdict != "pass":
        return {"name": "gate", "ok": False, "detail": f"verdict={verdict}"}
    if attributed > 0:
        return {"name": "gate", "ok": False, "detail": f"attributed={attributed}"}
    return {"name": "gate", "ok": True, "detail": "pass"}


# ── Tamper check ────────────────────────────────────────────────────────────

# Patterns that indicate tampering in tool_use inputs (Bash commands)
_TAMPER_PATTERNS = [
    re.compile(r"rm\s+.*logs/verification"),
    re.compile(r"batch-gate\.json.*(?:>|write|echo)"),
    re.compile(r"ILK_WORKER_SESSION"),
]


def _check_tamper(data_dir: Path, run_id: str) -> dict:
    """Check iter logs for tamper patterns in tool_use inputs."""
    runs_dir = data_dir / "logs" / "runs" / run_id
    if not runs_dir.exists():
        return {"name": "tamper", "ok": True, "detail": "no iter logs"}

    hits = []
    for log_file in sorted(runs_dir.glob("iter-*.log.jsonl")):
        try:
            with open(log_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Only check tool_use inputs, NOT tool_results
                    if rec.get("type") != "tool_use":
                        continue
                    if rec.get("name") != "Bash":
                        continue

                    command = rec.get("input", {}).get("command", "")
                    for pattern in _TAMPER_PATTERNS:
                        if pattern.search(command):
                            hits.append(f"{log_file.name}: {command[:80]}")
                            break
        except OSError:
            continue

    if hits:
        return {
            "name": "tamper",
            "ok": False,
            "detail": f"{len(hits)} tamper hit(s): {'; '.join(hits[:3])}",
        }
    return {"name": "tamper", "ok": True, "detail": "clean"}


# ── Masters check ───────────────────────────────────────────────────────────


def _check_masters(data_dir: Path, our_master: Path, run_id: str) -> dict:
    """Check that no foreign master was modified during the run."""
    plans_dir = data_dir / "plans"
    if not plans_dir.exists():
        return {"name": "masters", "ok": True, "detail": "no plans dir"}

    # Get run window from sentinel
    sentinel_file = data_dir / "runtime" / "launcher" / "last-exit.json"
    if not sentinel_file.exists():
        return {"name": "masters", "ok": True, "detail": "no sentinel"}

    try:
        sentinel = json.loads(sentinel_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"name": "masters", "ok": True, "detail": "sentinel unreadable"}

    started_at = sentinel.get("started_at")
    ended_at = sentinel.get("ended_at")
    if not started_at or not ended_at:
        return {"name": "masters", "ok": True, "detail": "no run window"}

    try:
        run_start = _parse_iso(started_at)
        run_end = _parse_iso(ended_at)
    except ValueError:
        return {"name": "masters", "ok": True, "detail": "unparseable timestamps"}

    # Check all MASTER files
    our_master_name = our_master.name
    violations = []
    for master_file in sorted(plans_dir.glob("MASTER-*.md")):
        if master_file.name == our_master_name:
            continue
        mtime = datetime.fromtimestamp(master_file.stat().st_mtime, tz=timezone.utc)
        if run_start <= mtime <= run_end:
            violations.append(master_file.name)

    if violations:
        return {
            "name": "masters",
            "ok": False,
            "detail": f"{len(violations)} foreign master(s) modified: {', '.join(violations[:3])}",
        }
    return {"name": "masters", "ok": True, "detail": "clean"}


# ── Master/sub-plan resolution ──────────────────────────────────────────────


def _find_master_and_subplans(data_dir: Path, run_id: str) -> tuple[Path | None, list[dict]]:
    """Find the master and its sub-plans for this run."""
    plans_dir = data_dir / "plans"
    if not plans_dir.exists():
        return None, []

    # Find shipped masters
    shipped_masters = []
    for master_file in sorted(plans_dir.glob("MASTER-*.md")):
        content = master_file.read_text(encoding="utf-8")
        if _extract_frontmatter_field(content, "status") == "shipped":
            shipped_masters.append(master_file)

    if not shipped_masters:
        return None, []

    # Use the newest shipped master (by mtime)
    master = max(shipped_masters, key=lambda p: p.stat().st_mtime)

    # Parse sub-plans
    sub_plans = _parse_sub_plans(master, plans_dir)

    return master, sub_plans


def _parse_sub_plans(master: Path, plans_dir: Path) -> list[dict]:
    """Parse sub-plan files referenced in the master."""
    content = master.read_text(encoding="utf-8")
    sub_plans = []

    # Find sub-plan references in the registry table
    for match in re.finditer(r"\[([^\]]+\.md)\]", content):
        sub_plan_name = match.group(1)
        sub_plan_file = plans_dir / sub_plan_name
        if sub_plan_file.exists():
            sp_content = sub_plan_file.read_text(encoding="utf-8")
            scope_paths = _extract_yaml_list(sp_content, "scope_paths")
            sub_plans.append({
                "name": sub_plan_name,
                "slug": _extract_frontmatter_field(sp_content, "plan") or "",
                "scope_paths": scope_paths,
            })

    return sub_plans


def _extract_frontmatter_field(content: str, field: str) -> str | None:
    """Extract a field from YAML front-matter."""
    in_frontmatter = False
    for line in content.splitlines():
        if line.strip() == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if in_frontmatter and line.startswith(f"{field}:"):
            value = line.split(":", 1)[1].strip().strip('"').strip("'")
            return value
    return None


def _extract_yaml_list(content: str, field: str) -> list[str]:
    """Extract a YAML list field from front-matter."""
    in_frontmatter = False
    in_list = False
    results = []
    for line in content.splitlines():
        if line.strip() == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if not in_frontmatter:
            continue
        if line.startswith(f"{field}:"):
            in_list = True
            continue
        if in_list:
            if line.startswith("  - "):
                value = line[4:].strip().strip('"').strip("'")
                results.append(value)
            elif not line.startswith(" "):
                in_list = False
    return results


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 timestamp, handling common formats."""
    # Try with timezone offset
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S+0800",
        "%Y-%m-%dT%H:%M:%S+08:00",
    ]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    # Fallback: parse with fromisoformat (Python 3.11+ handles more formats)
    return datetime.fromisoformat(s)


# ── Audit output ────────────────────────────────────────────────────────────


def _write_audit(result: dict, data_dir: Path, run_id: str) -> None:
    """Write the audit result to logs/audits/<run_id>.json."""
    audit_dir = data_dir / "logs" / "audits"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / f"{run_id}.json"
    audit_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a shipped batch")
    parser.add_argument("--project", required=True, help="Path to the git repo")
    parser.add_argument("--run-id", required=True, help="The run ID to audit")
    parser.add_argument("--json", action="store_true", help="Print result as JSON")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    run_id = args.run_id

    # Resolve data_dir from ILK_DATA_HOME (ilk_paths has no resolve_data_dir;
    # the name this called never existed, so the CLI raised on every run).
    from ilk_paths import project_data_dir, project_key
    data_dir = project_data_dir(project_key(project))

    result = audit(project=project, data_dir=data_dir, run_id=run_id)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"verdict: {result['verdict']}")
        for check in result["checks"]:
            status = "ok" if check["ok"] else "FAIL"
            print(f"  {check['name']}: {status} — {check['detail']}")

    verdict = result["verdict"]
    if verdict == "pass":
        return 0
    elif verdict == "unmeasured":
        return 2
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())