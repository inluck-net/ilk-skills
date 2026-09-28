"""A selfmod iteration snapshots the WORKTREE, and keeps isolation across iterations.

Run 20260928-212103 (ilk-skills, MASTER-2026-09-28c): the per-iteration
heads-before snapshot ran before the selfmod block set SELFMOD_ISOLATED=1,
so it recorded the live clone's HEAD (b8fcaaf) instead of the worktree's
(5b9dd2a).  The worktree was 9 commits ahead after refused merges, so the
iteration counted all 9 as its own and re-gated every slug in them,
including another master's full-suite verify: 65 minutes after a 29-minute
iteration.  The same ordering had re-gated a flaky caller test in run
20260928-202541 and reverted a correct sub-plan.

Two more defects share the root and are pinned here:
- after a deferred merge, the next iteration re-detected isolation with
  PROJECT_PATH already on the worktree, failed the toolkit-clone
  comparison, and dropped isolation for the rest of the run;
- a deferred merge that landed on the entry retry restored PROJECT_PATH to
  the live clone and removed the worktree, so the iteration would have
  dispatched its worker into the live clone.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


def _clone_with_worktree_ahead(tmp_path: Path, ahead: int = 2) -> tuple[Path, Path, Path]:
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-q", "-b", "main")
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "t")
    (clone / "f.txt").write_text("0\n")
    _git(clone, "add", "f.txt")
    _git(clone, "commit", "-q", "-m", "base")
    runtime = tmp_path / "runtime"
    wt = runtime / "worktrees" / "selfmod-batch"
    wt.parent.mkdir(parents=True)
    _git(clone, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    for n in range(ahead):
        (wt / "f.txt").write_text(f"{n + 1}\n")
        _git(wt, "commit", "-q", "-am", f"wt {n + 1}")
    return clone, wt, runtime


def _run(body: str, tmp_path: Path) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        "HOME": str(home),
        "ILK_DATA_HOME": str(home / ".ilk-data"),
    }
    script = f"""
set -uo pipefail
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
{body}
"""
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=60,
    )


def _stubs(clone: Path, runtime: Path, *, required: bool = True) -> str:
    return f"""
CREATE_CALLS=0
selfmod_isolation_required() {{ return {0 if required else 1}; }}
get_ilk_runtime_dir() {{ echo '{runtime}'; }}
create_selfmod_worktree() {{ CREATE_CALLS=$((CREATE_CALLS+1)); PROJECT_PATH="$SELFMOD_WORKTREE_PATH"; }}
REPOS=('{clone}')
"""


def test_first_entry_snapshots_the_worktree(tmp_path: Path) -> None:
    clone, wt, runtime = _clone_with_worktree_ahead(tmp_path)
    out = tmp_path / "heads"
    r = _run(_stubs(clone, runtime) + f"""
PROJECT_PATH='{clone}'
SELFMOD_ISOLATED=0
rc=0; setup_selfmod_isolation || rc=$?; echo "rc=$rc"
get_repo_heads '{out}'
echo "iso=$SELFMOD_ISOLATED pp=$PROJECT_PATH"
""", tmp_path)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert out.read_text().strip() == f"{clone}={_git(wt, 'rev-parse', 'HEAD')}", (
        f"heads-before must hold the worktree HEAD, not the clone's "
        f"({_git(clone, 'rev-parse', 'HEAD')}); got {out.read_text()!r}"
    )
    assert f"iso=1 pp={wt}" in r.stdout


def test_reentry_after_deferral_keeps_isolation(tmp_path: Path) -> None:
    # PROJECT_PATH is already the worktree, so re-detection would fail.
    clone, wt, runtime = _clone_with_worktree_ahead(tmp_path)
    out = tmp_path / "heads"
    r = _run(_stubs(clone, runtime, required=False) + f"""
SELFMOD_ISOLATED=1
SELFMOD_WORKTREE_PATH='{wt}'
SELFMOD_ORIGINAL_PROJECT_PATH='{clone}'
PROJECT_PATH='{wt}'
rc=0; setup_selfmod_isolation || rc=$?; echo "rc=$rc"
get_repo_heads '{out}'
echo "iso=$SELFMOD_ISOLATED creates=$CREATE_CALLS"
""", tmp_path)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert "iso=1 creates=0" in r.stdout, r.stdout
    assert out.read_text().strip() == f"{clone}={_git(wt, 'rev-parse', 'HEAD')}"


def test_retry_merge_that_lands_reenters_isolation(tmp_path: Path) -> None:
    clone, wt, runtime = _clone_with_worktree_ahead(tmp_path)
    (wt / ".ilk-merge-deferred").write_text('{"live_pids": "1", "since": "x"}\n')
    r = _run(_stubs(clone, runtime) + f"""
merge_selfmod_worktree() {{ PROJECT_PATH='{clone}'; return 0; }}
PROJECT_PATH='{clone}'
SELFMOD_ISOLATED=0
rc=0; setup_selfmod_isolation || rc=$?; echo "rc=$rc"
echo "iso=$SELFMOD_ISOLATED creates=$CREATE_CALLS pp=$PROJECT_PATH"
""", tmp_path)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert f"iso=1 creates=2 pp={wt}" in r.stdout, (
        "after a retry merge lands, isolation must be re-entered; otherwise "
        "the worker is dispatched into the live clone: " + r.stdout
    )
    assert not (wt / ".ilk-merge-deferred").exists()


def test_retry_merge_hard_failure_returns_3(tmp_path: Path) -> None:
    clone, wt, runtime = _clone_with_worktree_ahead(tmp_path)
    (wt / ".ilk-merge-deferred").write_text("{}\n")
    r = _run(_stubs(clone, runtime) + f"""
merge_selfmod_worktree() {{ return 5; }}
PROJECT_PATH='{clone}'
SELFMOD_ISOLATED=0
rc=0; setup_selfmod_isolation || rc=$?; echo "rc=$rc"
""", tmp_path)
    assert "rc=3" in r.stdout, r.stdout + r.stderr


def test_isolation_is_set_up_before_both_snapshots() -> None:
    text = _RUNNER.read_text(encoding="utf-8")
    call = text.index("setup_selfmod_isolation || _selfmod_rc=$?")
    heads = text.index('get_repo_heads "$heads_before_file"')
    snap = text.index('_snap_eff="$(selfmod_effective_repo "$_snap_repo")"')
    assert call < heads and call < snap, (
        "the selfmod setup must run before the heads-before snapshot and the "
        "pre-iteration snapshot; both read through selfmod_effective_repo"
    )
    # The old inline block (reset + re-detect every iteration) must be gone.
    assert not re.search(r"^    SELFMOD_ISOLATED=0\n    if selfmod_isolation_required; then", text, re.M)


def test_a_landed_merge_clears_the_deferral(tmp_path: Path) -> None:
    """merge_was_deferred was sticky: a deferral followed by a landed merge
    still tripped the "deferred + no new commits" spin guard."""
    wt = tmp_path / "wt"
    wt.mkdir()
    r = _run(f"""
SELFMOD_WORKTREE_PATH='{wt}'
SELFMOD_ORIGINAL_PROJECT_PATH='{tmp_path}'
merge_was_deferred=0; iter_stop_reason=""; stop_reason=""
record_selfmod_merge_outcome 2
echo "after-2 deferred=$merge_was_deferred stop=[$stop_reason] marker=$([ -f '{wt}/.ilk-merge-deferred' ] && echo yes || echo no)"
record_selfmod_merge_outcome 0
echo "after-0 deferred=$merge_was_deferred stop=[$stop_reason]"
record_selfmod_merge_outcome 5
echo "after-5 deferred=$merge_was_deferred stop=[$stop_reason]"
""", tmp_path)
    assert "after-2 deferred=1 stop=[] marker=yes" in r.stdout, r.stdout + r.stderr
    assert "after-0 deferred=0 stop=[]" in r.stdout, r.stdout
    assert "after-5 deferred=0 stop=[selfmod_merge_failed]" in r.stdout, r.stdout
