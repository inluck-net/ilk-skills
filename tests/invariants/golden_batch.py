"""Golden batch harness — runs a hermetic golden batch end to end.

Drives the real runner (``run_ilk_loop_claude.sh``) from a private copy of
``skills/``, over a fixture project and a three-sub-plan batch with a stub
``claude``, and compares the outcome and the wall clock with
``tests/invariants/fixtures/golden/expected.json``.

Usage::

    python3 tests/invariants/golden_batch.py [--json] [--keep] [--measure N]

Exit codes: 0 pass, 1 fail, 2 refusal (no gtimeout, ILK_SKILL_HOME in repo).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "golden"
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"
_RUNNER = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"


def _check_refusals() -> None:
    """Refuse if gtimeout is missing or ILK_SKILL_HOME would be in repo."""
    if shutil.which("gtimeout") is None:
        print("refusal: gtimeout not found on PATH", file=sys.stderr)
        raise SystemExit(2)

    skill_home = os.environ.get("ILK_SKILL_HOME", "")
    if skill_home:
        # Check if it resolves inside this repo.
        try:
            resolved = Path(skill_home).resolve()
            repo_resolved = _REPO.resolve()
            if resolved == repo_resolved or str(resolved).startswith(str(repo_resolved) + os.sep):
                print("refusal: ILK_SKILL_HOME resolves inside this repo", file=sys.stderr)
                raise SystemExit(2)
        except (OSError, ValueError):
            pass


def _build_tmp_root(keep: bool = False) -> Path:
    """Build a hermetic tmp root with the fixture project and a skills copy."""
    if keep:
        root = Path("/tmp/golden-batch-keep")
        if root.exists():
            shutil.rmtree(root)
    else:
        root = Path(os.environ.get("TMPDIR", "/tmp")) / f"golden-batch-{os.getpid()}"
    root.mkdir(parents=True, exist_ok=True)

    # Copy the fixture project.
    project = root / "project"
    if project.exists():
        shutil.rmtree(project)
    shutil.copytree(_FIXTURES / "project", project)

    # Copy the script directory (patches) to the project.
    script_dir = project / "script"
    if script_dir.exists():
        shutil.rmtree(script_dir)
    shutil.copytree(_FIXTURES / "script", script_dir)

    # Git init the project.
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "init", "-q", str(project)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "add", "-A"],
        cwd=str(project), check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=str(project), check=True, capture_output=True, text=True,
    )

    # Copy skills/ to ILK_SKILL_HOME.
    skill_home = root / "skills"
    if skill_home.exists():
        shutil.rmtree(skill_home)
    shutil.copytree(_REPO / "skills", skill_home)

    # Set up ILK_DATA_HOME.
    data_home = root / ".ilk-data"

    # Copy plans to the data home.
    sys.path.insert(0, str(_SCRIPTS))
    import ilk_paths
    from unittest.mock import patch as _patch
    with _patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURES / "plans", plans, dirs_exist_ok=True)

    # Copy stub-claude to bin/.
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "claude"
    shutil.copy2(_FIXTURES / "stub-claude", stub)
    stub.chmod(0o755)

    # Create python3 wrapper script (not a symlink) that forces the correct
    # Python. macOS /usr/bin/python3 is a shim that may redirect to Xcode's
    # Python depending on shell initialization; a wrapper avoids that.
    python_wrapper = bin_dir / "python3"
    python_wrapper.write_text('#!/bin/bash\nexec /usr/bin/python3 "$@"\n')
    python_wrapper.chmod(0o755)

    # Create .claude dir.
    (root / ".claude").mkdir(exist_ok=True)

    # Create .bash_env that prepends the correct PATH before bash -c runs.
    # macOS path_helper in /etc/profile reorders PATH, putting
    # /Library/Developer/CommandLineTools/usr/bin first (which has no pytest).
    # BASH_ENV is loaded by bash before /etc/profile, so this overrides it.
    # Use $PATH (literal, not Python f-string) to preserve the existing PATH.
    bash_env = root / ".bash_env"
    bash_env.write_text(f'export PATH="{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin' + ':$PATH"\n')

    return root


def _run_golden_batch(root: Path) -> subprocess.CompletedProcess:
    """Run the runner on the fixture project."""
    project = root / "project"
    skill_home = root / "skills"
    data_home = root / ".ilk-data"
    bin_dir = root / "bin"

    # Build PATH with /usr/bin first to ensure correct Python with pytest.
    # Include the original PATH for bash, gtimeout, and other utilities.
    original_path = os.environ.get("PATH", "")
    path_parts = [str(bin_dir), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    if original_path:
        path_parts.append(original_path)
    # PYTHONPATH must include the real user's site-packages because the
    # runner sets HOME to the tmp root for isolation, which moves Python's
    # user site-packages to a non-existent path under the tmp root.
    real_home = os.environ.get("HOME", "")
    user_site = ""
    if real_home:
        import subprocess as _sp
        try:
            user_site = _sp.check_output(
                ["/usr/bin/python3", "-m", "site", "--user-site"],
                text=True, timeout=5,
            ).strip()
        except Exception:
            user_site = str(Path(real_home) / "Library" / "Python" / "3.9" / "lib" / "python" / "site-packages")

    env = {
        **os.environ,
        "HOME": str(root),
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(skill_home),
        "PATH": os.pathsep.join(path_parts),
        "CLAUDE_CONFIG_DIR": str(root / ".claude"),
        # Ensure the runner uses the correct Python.
        "ILK_PATH_PRELUDE": "export PATH=/usr/bin:$PATH",
        # BASH_ENV is loaded by bash before /etc/profile, preventing
        # macOS path_helper from reordering PATH to put Xcode python first.
        "BASH_ENV": str(root / ".bash_env"),
    }
    # PYTHONPATH: include real user site-packages so pytest is findable
    # even when HOME is redirected to the tmp root.
    if user_site and Path(user_site).is_dir():
        env["PYTHONPATH"] = user_site
    env.pop("ILK_DATA_DIR", None)

    # Read expected.json for max_iterations.
    expected = json.loads((_FIXTURES / "expected.json").read_text(encoding="utf-8"))
    max_iter = expected.get("max_iterations", 8)

    return subprocess.run(
        ["bash", "--noprofile", str(_RUNNER),
         "--project-path", str(project),
         "--max-iterations", str(max_iter),
         "--iteration-timeout-min", "2",
         "--run-local-checks"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, env=env, cwd=str(root),
    )


_ATTRIBUTED_RE = re.compile(r"(\d+) attributed regression\(s\): (.+?)\. A row")


def _attributed_by_verify(root: Path, project: Path, data_home: Path, batch_slug: str):
    """Node ids the COPY's ``verify_attribution.py`` attributes, sorted.

    Runs the shipped verifier (not a re-implementation of it) on the
    fixture's batch record with ``--no-write-gate-record``.  It refuses an
    attributed record by naming the nodes; exit 0 means none attributed.
    Any other refusal is returned as a string, which never equals the
    expected list, so a missing or unreadable record is a mismatch.
    """
    script = root / "skills" / "ilk-loop" / "scripts" / "verify_attribution.py"
    env = dict(os.environ, ILK_DATA_HOME=str(data_home))
    r = subprocess.run(
        [sys.executable, str(script), "--batch", batch_slug,
         "--project", str(project), "--no-write-gate-record"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(project), timeout=120,
    )
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0:
        return []
    m = _ATTRIBUTED_RE.search(out)
    if m:
        return sorted(n.strip() for n in m.group(2).split(","))
    tail = out.strip().splitlines()[-1] if out.strip() else f"exit {r.returncode}"
    return f"verify refused: {tail[:300]}"


def _check_results(root: Path, proc: subprocess.CompletedProcess) -> dict:
    """Check the results against expected.json."""
    project = root / "project"
    skill_home = root / "skills"
    data_home = root / ".ilk-data"

    # Read expected.json.
    expected = json.loads((_FIXTURES / "expected.json").read_text(encoding="utf-8"))

    # Resolve the plans directory.
    sys.path.insert(0, str(_SCRIPTS))
    import ilk_paths
    from unittest.mock import patch as _patch
    with _patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"

    # Check each sub-plan's status.
    mismatches = []
    for slug in ["golden-real", "golden-inert", "golden-red", "golden-verify"]:
        plan_file = plans / f"2026-10-04-{slug}.md"
        if not plan_file.exists():
            mismatches.append({"slug": slug, "expected": expected[slug]["status"], "got": "missing"})
            continue

        text = plan_file.read_text(encoding="utf-8")
        import re
        m = re.search(r'^status:\s*(\S+)', text, re.MULTILINE)
        actual_status = m.group(1) if m else "unknown"

        want = expected[slug]
        if "status" in want and actual_status != want["status"]:
            mismatches.append({"slug": slug, "expected": want["status"], "got": actual_status})
        if "not_status" in want and actual_status == want["not_status"]:
            mismatches.append({"slug": slug, "expected": f"not {want['not_status']}", "got": actual_status})

    # AC-4: the planted red is caught BY the shipped verify machinery and
    # attributed to exactly the expected node ids.  A status alone cannot
    # say that: a verify that never ran, or ran a plain pytest, leaves the
    # same "not shipped" status as one that attributed the red.
    want_nodes = expected.get("golden-verify", {}).get("attributed_nodes")
    if want_nodes is not None:
        got_nodes = _attributed_by_verify(root, project, data_home, expected["batch_slug"])
        if got_nodes != sorted(want_nodes):
            mismatches.append({
                "slug": "golden-verify",
                "expected": {"attributed": sorted(want_nodes)},
                "got": got_nodes,
            })

    return {
        "verdict": "pass" if not mismatches else "fail",
        "mismatches": mismatches,
        "exit_code": proc.returncode,
    }


def run(*, out: Path | None = None, keep: bool = False) -> dict:
    """Run the golden batch and return the result dict.

    Raises SystemExit(2) for refusals.
    """
    _check_refusals()

    root = _build_tmp_root(keep=keep)
    start = time.monotonic()

    try:
        proc = _run_golden_batch(root)
        elapsed = time.monotonic() - start

        result = _check_results(root, proc)
        result["seconds"] = round(elapsed, 1)

        # Read budget.json if it exists.
        budget_file = _FIXTURES / "budget.json"
        if budget_file.exists():
            budget = json.loads(budget_file.read_text(encoding="utf-8"))
            result["budget_seconds"] = budget.get("max_seconds", 0)
            result["over_budget"] = elapsed > budget.get("max_seconds", float("inf"))
        else:
            result["budget_seconds"] = 0
            result["over_budget"] = False

        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2), encoding="utf-8")

        return result
    finally:
        if not keep and root.exists():
            shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Run the golden batch")
    parser.add_argument("--json", action="store_true", help="Print result as JSON")
    parser.add_argument("--keep", action="store_true", help="Keep tmp dir for debugging")
    parser.add_argument("--measure", type=int, default=0, help="Run N times and print seconds")
    parser.add_argument("--out", type=Path, help="Write result to file")
    args = parser.parse_args()

    if args.measure > 0:
        times = []
        for i in range(args.measure):
            result = run(keep=args.keep)
            times.append(result["seconds"])
            print(f"run {i+1}: {result['seconds']}s", file=sys.stderr)
        print(json.dumps({"runs": times}))
        return 0

    result = run(out=args.out, keep=args.keep)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"verdict: {result['verdict']}")
        print(f"seconds: {result['seconds']}")
        if result.get("budget_seconds"):
            print(f"budget: {result['budget_seconds']}s")
        if result["mismatches"]:
            print("mismatches:")
            for m in result["mismatches"]:
                print(f"  {m['slug']}: expected {m['expected']}, got {m['got']}")

    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())