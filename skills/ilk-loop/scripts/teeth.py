"""Teeth test: verify that removed rails turn their gates red.

Applies known-bad mutations to ``git archive HEAD`` copies and runs each
mutation's catcher in isolation.  A mutation that survives means a rail has
decayed.

Part of guard 4 of the RSI safety case (design :327-331).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _hermetic_env(root: Path, skill_home: Path) -> dict[str, str]:
    """Build an env dict that isolates the catcher from the ambient host.

    ``HOME`` is changed so the runner resolves its data dirs under *root*,
    but ``PYTHONPATH`` carries the real user site-packages so ``pytest``
    (installed there) is still importable.
    """
    import site
    real_user_site = site.getusersitepackages()
    env = {
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / "ilk-data"),
        "ILK_SKILL_HOME": str(skill_home),
        "CLAUDE_CONFIG_DIR": str(root / ".claude"),
        "PATH": f"/opt/homebrew/bin{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONPATH": real_user_site,
        "PYTHONNOUSERSITE": "1",
    }
    # Remove aliases that leak ambient state.
    for key in ("ILK_DATA_DIR", "ILK_WORKER_SESSION",
                "ILK_ITERATION_SUBPLAN", "ILK_MASTER", "ILK_SHIPPED_MARKER"):
        env.pop(key, None)
    return env


def _run_catcher(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_s: int,
) -> tuple[int, str, str]:
    """Run a catcher subprocess.  Returns (exit_code, stdout, stderr)."""
    try:
        cp = subprocess.run(
            argv, cwd=str(cwd), env=env,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout_s,
        )
        return cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def _extract_archive(repo: Path, dest: Path) -> None:
    """Extract ``git archive HEAD`` into *dest*."""
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "HEAD"],
        cwd=str(repo), stdout=subprocess.PIPE, check=True,
    )
    subprocess.run(
        ["tar", "-x", "-C", str(dest)],
        input=archive.stdout, check=True,
    )


def _apply_mutation(copy: Path, mutation: dict) -> tuple[bool, int]:
    """Apply a mutation to the extracted copy.

    Returns ``(applied, match_count)``.  ``applied=False`` when
    ``match_count < min_matches`` (the mutation is stale).
    """
    target = copy / mutation["file"]
    if not target.exists():
        return False, 0

    content = target.read_text(encoding="utf-8")
    kind = mutation["kind"]
    pattern = mutation["pattern"]
    min_matches = mutation.get("min_matches", 1)

    if kind == "replace":
        replacement = mutation["replacement"]
        new_content, n = re.subn(pattern, replacement, content,
                                 flags=re.MULTILINE)
    elif kind == "delete-lines":
        lines = content.splitlines(keepends=True)
        new_lines = [l for l in lines if not re.search(pattern, l)]
        n = len(lines) - len(new_lines)
        new_content = "".join(new_lines)
    else:
        return False, 0

    if n < min_matches:
        return False, n

    target.write_text(new_content, encoding="utf-8")
    return True, n


def run(
    repo: Path,
    catalog: Path,
    *,
    only: list[str] | None = None,
    catcher_prefix: str | None = None,
    jobs: int = 4,
    timeout_s: int = 600,
) -> dict:
    """Run the teeth test.

    Returns a dict with ``verdict`` (``pass``/``fail``), ``head``,
    ``catalog_sha256``, ``seconds``, and ``results``.
    """
    t0 = time.monotonic()
    repo = repo.resolve()

    # Validate: repo must be a git repo.
    git_dir = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(repo), capture_output=True, text=True,
    )
    if git_dir.returncode != 0:
        return {"verdict": "fail", "reason": "not-a-git-repo"}

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), capture_output=True, text=True,
    ).stdout.strip()

    cat_hash = hashlib.sha256(
        catalog.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()

    data = json.loads(catalog.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        return {"verdict": "fail", "reason": "bad-schema"}

    all_mutations = data.get("mutations", [])

    # Filter.
    if only is not None:
        selected = [m for m in all_mutations if m["id"] in only]
    else:
        selected = list(all_mutations)

    if not selected:
        return {
            "verdict": "fail",
            "reason": "no-mutations-selected",
            "head": head,
            "catalog_sha256": cat_hash,
            "seconds": round(time.monotonic() - t0, 3),
            "results": [],
        }

    # Deduplicate control runs: each unique catcher argv runs once.
    control_cache: dict[tuple[str, ...], tuple[int, str, str]] = {}

    def _run_control(argv_key: tuple[str, ...]) -> None:
        if argv_key in control_cache:
            return
        control_root = Path(tempfile.mkdtemp(prefix="ilk-teeth-ctrl-"))
        try:
            _extract_archive(repo, control_root / "src")
            skill_home = control_root / "src" / "skills"
            env = _hermetic_env(control_root, skill_home)
            exit_code, stdout, stderr = _run_catcher(
                list(argv_key), control_root / "src", env, timeout_s)
            control_cache[argv_key] = (exit_code, stdout, stderr)
        finally:
            shutil.rmtree(control_root, ignore_errors=True)

    # Pre-run controls in parallel.
    unique_argv: set[tuple[str, ...]] = set()
    for m in selected:
        unique_argv.add(tuple(m["catcher"]["argv"]))

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(_run_control, k): k for k in unique_argv}
        for f in as_completed(futs):
            f.result()  # propagate exceptions

    # Run each mutation.
    results: list[dict] = []

    def _run_one(mutation: dict) -> dict:
        mid = mutation["id"]
        argv = mutation["catcher"]["argv"]
        argv_key = tuple(argv)
        expect = mutation["catcher"].get("expect_in_output", [])
        t1 = time.monotonic()

        # Guard: control must be green.
        ctrl_exit, _, _ = control_cache[argv_key]
        if ctrl_exit != 0:
            return {
                "id": mid, "outcome": "control-red",
                "seconds": round(time.monotonic() - t1, 3),
                "catcher_exit": ctrl_exit, "missing_expect": [],
            }

        # Extract mutant copy.
        mutant_root = Path(tempfile.mkdtemp(prefix="ilk-teeth-"))
        try:
            _extract_archive(repo, mutant_root / "src")
            applied, match_count = _apply_mutation(
                mutant_root / "src", mutation)
            if not applied:
                return {
                    "id": mid, "outcome": "stale",
                    "seconds": round(time.monotonic() - t1, 3),
                    "catcher_exit": None, "missing_expect": [],
                    "match_count": match_count,
                }

            skill_home = mutant_root / "src" / "skills"
            env = _hermetic_env(mutant_root, skill_home)
            exit_code, stdout, stderr = _run_catcher(
                argv, mutant_root / "src", env, timeout_s)

            combined = stdout + "\n" + stderr
            if exit_code == 0:
                return {
                    "id": mid, "outcome": "survived",
                    "seconds": round(time.monotonic() - t1, 3),
                    "catcher_exit": exit_code, "missing_expect": [],
                }

            missing = [s for s in expect if s not in combined]
            if missing:
                return {
                    "id": mid, "outcome": "red-for-another-reason",
                    "seconds": round(time.monotonic() - t1, 3),
                    "catcher_exit": exit_code, "missing_expect": missing,
                }

            return {
                "id": mid, "outcome": "killed",
                "seconds": round(time.monotonic() - t1, 3),
                "catcher_exit": exit_code, "missing_expect": [],
            }
        finally:
            shutil.rmtree(mutant_root, ignore_errors=True)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(_run_one, m): m for m in selected}
        for f in as_completed(futs):
            results.append(f.result())

    # Order results by catalog order.
    order = {m["id"]: i for i, m in enumerate(all_mutations)}
    results.sort(key=lambda r: order.get(r["id"], 999))

    all_killed = all(r["outcome"] == "killed" for r in results)
    verdict = "pass" if all_killed else "fail"

    return {
        "verdict": verdict,
        "head": head,
        "catalog_sha256": cat_hash,
        "seconds": round(time.monotonic() - t0, 3),
        "results": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="teeth: mutation-based rail test")
    sub = ap.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Run mutations against a repo")
    run_p.add_argument("--repo", required=True, help="Path to git repo")
    run_p.add_argument("--catalog", required=True,
                       help="Path to mutations.json")
    run_p.add_argument("--only", nargs="*", default=None,
                       help="Run only these mutation ids")
    run_p.add_argument("--catcher-prefix", default=None)
    run_p.add_argument("--jobs", type=int, default=4)
    run_p.add_argument("--timeout", type=int, default=600,
                       help="Per-catcher timeout in seconds")
    run_p.add_argument("--json", dest="json_out", action="store_true")
    run_p.add_argument("--out", default=None, help="Write JSON to file")

    list_p = sub.add_parser("list", help="List mutation ids")
    list_p.add_argument("--catalog", required=True)

    args = ap.parse_args()
    if args.cmd is None:
        ap.print_help()
        sys.exit(2)

    if args.cmd == "list":
        data = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        for m in data.get("mutations", []):
            print(m["id"])
        sys.exit(0)

    if args.cmd == "run":
        result = run(
            Path(args.repo), Path(args.catalog),
            only=args.only,
            catcher_prefix=args.catcher_prefix,
            jobs=args.jobs,
            timeout_s=args.timeout,
        )
        if args.out:
            Path(args.out).write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if args.json_out:
            print(json.dumps(result, indent=2))
        else:
            print(f"verdict: {result['verdict']}")
            for r in result.get("results", []):
                print(f"  {r['id']}: {r['outcome']}")
        sys.exit(0 if result["verdict"] == "pass" else 1)


if __name__ == "__main__":
    main()