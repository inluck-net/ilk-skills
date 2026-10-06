#!/usr/bin/env python3
"""safety_case.py — run the RSI safety case components and write a verdict.

Part of guard 4 of the RSI safety case (design :325-331).

Verbs:
  run  — run invariants + golden + teeth + sealed, record the verdict

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
    # Judgment call: the sealed slice gets 300 s. Basis: Chad's Oct 6 cap of
    # ~5 min per train for golden-vs-incumbent + sealed; 4 cases measured
    # 35 s at --jobs 4. Wrong if the cap is meant to cover more than these two.
    "sealed": 300,
}


def _run_component(
    name: str,
    project: Path,
    *,
    runner: object | None = None,
    timeout: int | None = None,
    extra_args: tuple[str, ...] = (),
) -> dict:
    """Run one safety-case component.

    Returns ``{name, ok, seconds, budget_seconds, exit, tail}``.
    """
    # Build the command for each component.
    scripts_dir = project / "skills" / "ilk-loop" / "scripts"

    env = None
    if name == "invariants":
        cmd = [sys.executable, "-m", "pytest", "tests/invariants", "-q", "-p", "no:cacheprovider"]
        budget = _BUDGETS["invariants"]
        # Pin HOME and ILK_DATA_HOME together to a private root, as teeth's
        # control runs do, so the conftest data-root guard watches only this
        # run's writes. Measured 2026-10-06: a scheduler drain-verification
        # launch wrote the real ilk-skills runtime mid-run and the guard
        # refused the v0.9.154 train with golden and teeth green.
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        import teeth  # noqa: E402
        _inv_root = Path(tempfile.mkdtemp(prefix="ilk-safety-invariants-"))
        env = {**os.environ, **teeth._hermetic_env(_inv_root, project / "skills")}
        for _k in ("ILK_DATA_DIR", "ILK_WORKER_SESSION", "ILK_ITERATION_SUBPLAN",
                   "ILK_MASTER", "ILK_SHIPPED_MARKER"):
            env.pop(_k, None)
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
    elif name == "sealed":
        # The catalog path comes in through extra_args (see _run_sealed);
        # it lives outside the repo and is never named in the record.
        cmd = [
            sys.executable, str(scripts_dir / "teeth.py"),
            "run", "--repo", str(project), "--jobs", "4", "--json",
        ]
        budget = _BUDGETS["sealed"]
    else:
        return {
            "name": name, "ok": False, "seconds": 0,
            "budget_seconds": 0, "exit": 2,
            "tail": f"unknown component: {name}",
        }

    cmd = cmd + list(extra_args)
    effective_timeout = timeout or budget
    t0 = time.monotonic()

    try:
        if runner is not None:
            # Injectable runner (for testing)
            result = runner(cmd, cwd=project, timeout=effective_timeout,
                            **({"env": env} if env is not None else {}))
            elapsed = time.monotonic() - t0
            exit_code = result.returncode
            stdout_text = result.stdout
            raw_output = (result.stdout + result.stderr).strip()
        else:
            result = subprocess.run(
                cmd, cwd=project,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=effective_timeout, env=env,
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

    if name == "sealed":
        component["_stdout"] = stdout_text

    if name == "golden" and exit_code in (0, 1):
        try:
            g = json.loads(stdout_text.strip())
            component["golden"] = {
                "verdict": g.get("verdict"),
                "mismatches": len(g.get("mismatches") or []),
                "seconds": g.get("seconds"),
            }
        except (json.JSONDecodeError, AttributeError):
            pass

    return component


# ── Sealed held-out slice (RSI design point 2, Oct 6) ──────────────────────


def _default_sealed_catalog() -> Path:
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    from ilk_paths import ilk_data_root  # noqa: E402
    return ilk_data_root() / "sealed" / "mutations.json"


def _run_sealed(project: Path, *, runner: object | None,
                catalog: Path | None) -> dict:
    """Run the sealed mutations; record ONLY how many exist and were caught.

    Each case's catcher is the whole invariant suite, so a case names no
    test. Ids, outcomes and the output tail never enter the record: a
    sealed case that is reported becomes a public one. Every case must be
    caught; a survivor or a red control refuses.

    Judgment call: an ABSENT catalog passes with ``status: absent`` (it is
    a strength check, not a rail, and only the train host has one). Wrong
    if absence goes unread in the digest; the deny hook keeps workers from
    deleting it.
    """
    if catalog is None or not catalog.is_file():
        return {"name": "sealed", "ok": True, "seconds": 0,
                "budget_seconds": _BUDGETS["sealed"], "exit": 0,
                "tail": "", "sealed": {"status": "absent"}}
    comp = _run_component("sealed", project, runner=runner,
                          extra_args=("--catalog", str(catalog)))
    total = caught = None
    try:
        # The tail is truncated, so counts come from teeth's full stdout.
        data = json.loads(comp.pop("_stdout", "") or "{}")
        results = data.get("results") or []
        total = len(results)
        caught = sum(1 for r in results if r.get("outcome") == "caught-red")
    except (json.JSONDecodeError, AttributeError):
        pass
    comp["tail"] = ""
    comp["sealed"] = {"total": total, "caught": caught}
    comp["ok"] = bool(comp["ok"] and total and caught == total)
    return comp


# ── Golden: incumbent vs candidate (RSI design point 1, Oct 6) ─────────────

# Judgment call: flag (never fail) a candidate golden run more than 10% slower
# than the incumbent's. Basis: 3 runs spanned 66.9-71.1 s (Oct 6). Wrong if
# load noise between two back-to-back runs exceeds 10%.
GOLDEN_TIME_FLAG_RATIO = 1.10


def _resolve_incumbent(project: Path) -> Path | None:
    """The deployed release to compare against, or None.

    ``$ILK_GOLDEN_INCUMBENT`` wins; otherwise ``~/.ilk/current``. None when it
    does not resolve, has no runner, or IS the project under test.
    """
    raw = os.environ.get("ILK_GOLDEN_INCUMBENT") or str(Path.home() / ".ilk" / "current")
    try:
        inc = Path(raw).expanduser().resolve(strict=True)
    except OSError:
        return None
    if not (inc / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh").is_file():
        return None
    if inc == project.resolve():
        return None
    return inc


def _brief(c: dict) -> dict:
    return {k: c.get(k) for k in ("ok", "seconds", "exit", "golden", "tail")}


def _compare_golden(project: Path, candidate: dict, *, runner: object | None,
                    incumbent: Path | None) -> dict:
    """Run the golden batch on the incumbent too, and apply the flip rule.

    The oracle (fixture + expected.json) is the candidate tree's
    ``tests/invariants`` for both runs; that directory is rules tier, so no
    loop build can move it. Rules:
      - incumbent passes, candidate fails: re-run the candidate once; still
        failing => refuse (``ok`` stays False, ``refusal`` says why).
      - time: candidate > incumbent * GOLDEN_TIME_FLAG_RATIO => ``time_flag``
        only; it never changes ``ok``.
      - no incumbent: recorded as unavailable; ``ok`` unchanged.
    """
    if incumbent is None:
        candidate["incumbent"] = {"status": "unavailable"}
        return candidate
    inc = _run_component("golden", project, runner=runner,
                         extra_args=("--skills-from", str(incumbent)))
    candidate["incumbent"] = {"skills_from": str(incumbent), **_brief(inc)}
    if not candidate["ok"] and inc["ok"]:
        retry = _run_component("golden", project, runner=runner)
        candidate["candidate_retry"] = _brief(retry)
        if retry["ok"]:
            candidate["ok"] = True
            candidate["note"] = "verdict flip cleared on one re-run"
        else:
            candidate["refusal"] = "verdict flip: incumbent passes, candidate fails twice"
    if candidate["ok"] and inc["ok"] and inc.get("seconds"):
        candidate["time_flag"] = (
            candidate["seconds"] > inc["seconds"] * GOLDEN_TIME_FLAG_RATIO)
    return candidate


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
    components: tuple[str, ...] = ("invariants", "golden", "teeth", "sealed"),
    runner: object | None = None,
    no_record: bool = False,
    incumbent: Path | None | str = "auto",
    sealed_catalog: Path | None | str = "auto",
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

    if incumbent == "auto":
        # An injected runner is a test stub; never compare it against the
        # real deployed release unless the test passes one explicitly.
        incumbent = None if runner is not None else _resolve_incumbent(project)

    if sealed_catalog == "auto":
        # Same rule as the incumbent: a test stub never reads the real slice.
        sealed_catalog = None if runner is not None else _default_sealed_catalog()

    for name in components:
        if name == "sealed":
            results.append(_run_sealed(project, runner=runner,
                                       catalog=sealed_catalog))
            if not results[-1]["ok"]:
                all_ok = False
            continue
        result = _run_component(name, project, runner=runner)
        if name == "golden":
            result = _compare_golden(project, result, runner=runner,
                                     incumbent=incumbent)
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

    components = tuple(args.only) if args.only else ("invariants", "golden", "teeth", "sealed")

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