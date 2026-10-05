#!/usr/bin/env python3
"""safety_case.py — run the RSI safety case components and write a verdict.

Part of guard 4 of the RSI safety case (design :325-331).

Verbs:
  run  — run invariants + golden + teeth, record the verdict

CLI: safety_case.py run --project P [--data-dir D] [--only C ...] [--no-record] [--json]

Exit codes: 0 pass, 1 fail, 2 internal error, 3 worker refusal.
Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent


class WorkerSessionRefused(PermissionError):
    """Raised when a worker session attempts to run the safety case."""


def _refuse_in_worker_session(what: str) -> None:
    """Raise ``WorkerSessionRefused`` if ``ILK_WORKER_SESSION=1``."""
    if os.environ.get("ILK_WORKER_SESSION") == "1":
        raise WorkerSessionRefused(
            f"{what} refused in a worker session — commit and end your turn"
        )


def _git_head_sha(project: Path) -> str:
    """Get HEAD SHA for the project."""
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    return r.stdout.strip()


def _git_tree_sha(project: Path) -> str:
    """Get tree SHA for the project (stable across commits with same tree)."""
    r = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=project, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    return r.stdout.strip()


def _file_sha256(path: Path) -> str:
    """SHA-256 hex digest of a file, or empty string if missing."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── Component runners ───────────────────────────────────────────────────────

_BUDGETS = {
    "invariants": 120,
    "teeth": 900,
}


def _run_component(
    name: str,
    project: Path,
    *,
    runner: object | None = None,
    timeout: int | None = None,
) -> dict:
    """Run one safety-case component.

    Returns ``{name, ok, seconds, budget_seconds, exit, tail}``.
    """
    # Build the command for each component.
    scripts_dir = project / "skills" / "ilk-loop" / "scripts"

    if name == "invariants":
        cmd = [sys.executable, "-m", "pytest", "tests/invariants", "-q", "-p", "no:cacheprovider"]
        budget = _BUDGETS["invariants"]
    elif name == "golden":
        cmd = [sys.executable, str(project / "tests" / "invariants" / "golden_batch.py"), "--json"]
        # Budget from fixture budget.json
        budget_path = project / "tests" / "invariants" / "fixtures" / "golden" / "budget.json"
        if not budget_path.exists():
            return {
                "name": name, "ok": False, "seconds": 0,
                "budget_seconds": 0, "exit": 2,
                "tail": "missing budget.json",
            }
        try:
            budget_data = json.loads(budget_path.read_text())
            budget = budget_data.get("max_seconds", 0)
        except (json.JSONDecodeError, KeyError):
            return {
                "name": name, "ok": False, "seconds": 0,
                "budget_seconds": 0, "exit": 2,
                "tail": "corrupt budget.json",
            }
    elif name == "teeth":
        cmd = [
            sys.executable, str(scripts_dir / "teeth.py"),
            "run", "--repo", str(project),
            "--catalog", str(project / "tests" / "invariants" / "mutations.json"),
            "--jobs", "4", "--json",
        ]
        budget = _BUDGETS["teeth"]
        # Parse structured outcomes from teeth JSON output.
        # This is filled after the subprocess completes.
    else:
        return {
            "name": name, "ok": False, "seconds": 0,
            "budget_seconds": 0, "exit": 2,
            "tail": f"unknown component: {name}",
        }

    effective_timeout = timeout or budget
    t0 = time.monotonic()

    try:
        if runner is not None:
            # Injectable runner (for testing)
            result = runner(cmd, cwd=project, timeout=effective_timeout)
            elapsed = time.monotonic() - t0
            exit_code = result.returncode
            stdout_text = result.stdout
            raw_output = (result.stdout + result.stderr).strip()
        else:
            result = subprocess.run(
                cmd, cwd=project,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=effective_timeout,
            )
            elapsed = time.monotonic() - t0
            exit_code = result.returncode
            stdout_text = result.stdout
            raw_output = (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return {
            "name": name, "ok": False, "seconds": round(elapsed, 1),
            "budget_seconds": budget, "exit": -1,
            "tail": f"timeout after {elapsed:.0f}s (budget {budget}s)",
        }
    except (OSError, ValueError) as exc:
        elapsed = time.monotonic() - t0
        return {
            "name": name, "ok": False, "seconds": round(elapsed, 1),
            "budget_seconds": budget, "exit": 2,
            "tail": str(exc),
        }

    ok = exit_code == 0 and elapsed <= budget
    # Truncate tail to last 500 chars for readability
    tail_display = raw_output[-500:] if len(raw_output) > 500 else raw_output

    component = {
        "name": name,
        "ok": ok,
        "seconds": round(elapsed, 1),
        "budget_seconds": budget,
        "exit": exit_code,
        "tail": tail_display,
    }

    # For teeth, parse the JSON output and include structured mutation outcomes.
    # Use the full stdout (not truncated tail) for reliable JSON parsing.
    if name == "teeth" and exit_code in (0, 1):
        try:
            teeth_data = json.loads(stdout_text.strip())
            component["mutation_outcomes"] = [
                {"id": r["id"], "outcome": r["outcome"]}
                for r in teeth_data.get("results", [])
            ]
        except (json.JSONDecodeError, KeyError):
            pass

    return component


# ── Record writer ───────────────────────────────────────────────────────────

def _write_record(
    data_dir: Path,
    tree: str,
    head: str,
    verdict: str,
    components: list[dict],
    catalog_sha256: str,
    kernel_sha256: str,
) -> Path:
    """Write the safety-case record atomically (tempfile + os.replace)."""
    record_dir = data_dir / "runtime" / "safety-case"
    record_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "tree": tree,
        "head": head,
        "verdict": verdict,
        "components": components,
        "catalog_sha256": catalog_sha256,
        "kernel_sha256": kernel_sha256,
        "writer": "driver",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    record_path = record_dir / f"{tree}.json"

    # Atomic write: tempfile + os.replace
    fd, tmp_path = tempfile.mkstemp(dir=record_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, record_path)
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return record_path


# ── Public API ──────────────────────────────────────────────────────────────

def run(
    project: Path,
    *,
    data_dir: Path,
    components: tuple[str, ...] = ("invariants", "golden", "teeth"),
    runner: object | None = None,
    no_record: bool = False,
) -> dict:
    """Run the safety case and return the verdict.

    Returns ``{verdict, tree, head, components, catalog_sha256,
    kernel_sha256, writer, ts}``.

    Raises ``WorkerSessionRefused`` when ``ILK_WORKER_SESSION=1``.
    """
    _refuse_in_worker_session("safety case")

    head = _git_head_sha(project)
    tree = _git_tree_sha(project)

    # Hash the catalog and kernel for the record
    catalog_sha256 = _file_sha256(
        project / "tests" / "invariants" / "mutations.json")
    kernel_sha256 = _file_sha256(
        project / "skills" / "ilk-loop" / "safety-kernel.json")

    results: list[dict] = []
    all_ok = True

    for name in components:
        result = _run_component(name, project, runner=runner)
        results.append(result)
        if not result["ok"]:
            all_ok = False

    verdict = "pass" if all_ok else "fail"

    if not no_record:
        _write_record(data_dir, tree, head, verdict, results,
                      catalog_sha256, kernel_sha256)

    return {
        "verdict": verdict,
        "tree": tree,
        "head": head,
        "components": results,
        "catalog_sha256": catalog_sha256,
        "kernel_sha256": kernel_sha256,
        "writer": "driver",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="RSI safety case runner",
    )
    sub = parser.add_subparsers(dest="verb")

    p_run = sub.add_parser("run", help="Run the safety case")
    p_run.add_argument("--project", type=Path, required=True)
    p_run.add_argument("--data-dir", type=Path, default=None)
    p_run.add_argument("--only", nargs="*", default=None,
                       help="Run only these components")
    p_run.add_argument("--no-record", action="store_true",
                       help="Don't write the record file")
    p_run.add_argument("--json", action="store_true", dest="json_out")

    args = parser.parse_args()
    if not args.verb:
        parser.print_help()
        return 2

    project = args.project.resolve()

    # Resolve data_dir
    if args.data_dir:
        data_dir = args.data_dir.resolve()
    else:
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        from ilk_paths import ilk_data_root  # noqa: E402
        data_root = ilk_data_root()
        data_dir = data_root / "projects" / project.name

    components = tuple(args.only) if args.only else ("invariants", "golden", "teeth")

    try:
        result = run(
            project,
            data_dir=data_dir,
            components=components,
            no_record=args.no_record,
        )
    except WorkerSessionRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        print(f"verdict: {result['verdict']}")
        for c in result["components"]:
            status = "ok" if c["ok"] else "FAIL"
            print(f"  {c['name']}: {status} ({c['seconds']}s / {c['budget_seconds']}s)")

    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())