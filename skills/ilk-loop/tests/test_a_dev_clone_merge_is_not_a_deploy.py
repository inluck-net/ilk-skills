"""Red-first tests: under a release the dev-clone merge-back is not a deploy.

Part of sub-plan ``a-dev-clone-merge-is-not-a-deploy``
(MASTER-2026-10-02d).

Six acceptance criteria:

  AC-1  from a release copy, ``selfmod_isolation_required`` returns 0 for
        the dev clone and ``_resolve_toolkit_clone`` prints its realpath.
  AC-2  ``selfmod_worktree.py merge --not-a-deploy`` with a live loop on
        the clone exits 0 (skips the live-loop hold) and the clone HEAD
        advances.
  AC-3  ``merge_selfmod_worktree`` in the runner passes ``--not-a-deploy``
        to the merge CLI.
  AC-4  from a release copy, ``_check_other_project_merge_pending``
        returns 1 (no yield) when another project is merge-pending.
  AC-5  (control) AC-2 without ``--not-a-deploy`` exits 2 (passes today).
  AC-6  (control) AC-4 sourced from this tree (clone layout) still prints
        the other project's key (passes today).

AC-1..AC-4 are expected to FAIL (xfail) until step 1 implements the
release-aware behaviour.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_REPO = _SCRIPTS.parent.parent.parent  # worktree root

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from test_selfmod_worktree import _create_throwaway_repo  # noqa: E402

_PATH = os.environ.get("PATH", "")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return cp.stdout.strip()


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside *root*."""
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _make_release_fixture(tmp_path: Path) -> dict[str, Path]:
    """Build a dev clone and a release copy.

    The release copy has:
      - <tmp>/releases/v1/skills/ (copytree of this tree's skills/)
      - <tmp>/releases/v1/.ilk-release.json naming the dev clone as source_repo

    Returns dict with keys: dev_clone, release_root, release_skills, runner.
    """
    # Dev clone: a minimal git repo.
    dev_clone = _create_throwaway_repo(tmp_path)

    # Release copy: extract skills and write manifest.
    release_root = tmp_path / "releases" / "v1"
    release_root.mkdir(parents=True)
    release_skills = release_root / "skills"
    shutil.copytree(
        _REPO / "skills",
        release_skills,
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    manifest = {
        "tag": "v1",
        "sha": _git(dev_clone, "rev-parse", "HEAD"),
        "source_repo": str(dev_clone),
        "extracted_at": "2026-10-03T00:00:00+0800",
    }
    (release_root / ".ilk-release.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )

    runner = release_skills / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"

    return {
        "dev_clone": dev_clone,
        "release_root": release_root,
        "release_skills": release_skills,
        "runner": runner,
    }


def _run_driver_func(
    func_name: str,
    project: Path,
    env: dict[str, str],
    skill_root: Path | None = None,
) -> subprocess.CompletedProcess:
    """Source the driver and call a named function."""
    skill_root_arg = skill_root or _REPO / "skills"
    script = f"""
export ILK_DOTSOURCE_ONLY=1
export _SKILL_ROOT='{skill_root_arg}'
source '{skill_root_arg}/ilk-loop/scripts/run_ilk_loop_claude.sh'
PROJECT_PATH='{project}'
{func_name}
echo "RC=$?"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(project),
    )


def _make_project_with_merge_pending(
    tmp_path: Path, name: str, data_home: Path,
) -> Path:
    """Create a project with merge-deferred sentinel and unmerged worktree.

    Returns the external project dir.
    """
    project = tmp_path / "repos" / name
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    (project / "README.md").write_text("seed\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-q", "-m", "seed")

    import ilk_paths
    key = ilk_paths.project_key(project)
    ext_project = data_home / "projects" / key
    launcher = ext_project / "runtime" / "launcher"
    launcher.mkdir(parents=True)

    # Worktree with unmerged commit.
    wt = launcher / "worktrees" / "selfmod-batch"
    _git(project, "worktree", "add", "--detach", str(wt))
    (wt / "work.txt").write_text("work\n", encoding="utf-8")
    _git(wt, "add", "work.txt")
    _git(wt, "commit", "-q", "-m", "work commit")

    # Sentinel with merge-deferred state.
    sentinel = launcher / "last-exit.json"
    sentinel.write_text(json.dumps({
        "state": "merge-deferred",
        "pid": 12345,
        "run_id": "test-run",
        "started_at": "2026-01-01T00:00:00+0000",
        "ended_at": None,
        "iterations": 1,
        "project_path": str(project),
        "cli": "claude",
        "jsonl_log": str(ext_project / "test.jsonl"),
        "merge_deferred": None,
        "held_by": None,
        "failed_check": None,
    }), encoding="utf-8")

    return ext_project


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac1_release_resolves_dev_clone_as_toolkit(tmp_path: Path) -> None:
    """AC-1: from a release, selfmod_isolation_required returns 0 for the
    dev clone and _resolve_toolkit_clone prints its realpath.

    A release dir has no .git and no child symlinks, so today it falls
    through and selfmod_isolation_required returns 1 for the dev clone.
    """
    fx = _make_release_fixture(tmp_path)
    dev_clone = fx["dev_clone"]
    runner = fx["runner"]

    env = _sandbox_env(tmp_path)
    env["_SKILL_ROOT"] = str(fx["release_skills"])

    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export _SKILL_ROOT='{fx["release_skills"]}'
        source '{runner}'
        PROJECT_PATH='{dev_clone}'
        # Call _resolve_toolkit_clone and capture output
        _resolved=$(_resolve_toolkit_clone)
        echo "RESOLVED=$_resolved"
        # Call selfmod_isolation_required
        selfmod_isolation_required && iso_rc=0 || iso_rc=$?
        echo "ISO_RC=$iso_rc"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    lines = result.stdout.splitlines()
    resolved_line = [l for l in lines if l.startswith("RESOLVED=")]
    iso_line = [l for l in lines if l.startswith("ISO_RC=")]
    assert resolved_line, f"no RESOLVED output: {result.stdout}\n{result.stderr}"
    assert iso_line, f"no ISO_RC output: {result.stdout}\n{result.stderr}"

    resolved_val = resolved_line[-1].split("=", 1)[1]
    iso_val = iso_line[-1].split("=", 1)[1]

    expected = str(dev_clone.resolve())
    assert resolved_val == expected, (
        f"_resolve_toolkit_clone should print realpath of dev clone, "
        f"got {resolved_val!r} (expected {expected!r})"
    )
    assert iso_val == "0", (
        f"selfmod_isolation_required should return 0 for dev clone "
        f"under a release, got exit {iso_val}"
    )


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac2_not_a_deploy_skips_live_loop_hold(tmp_path: Path) -> None:
    """AC-2: merge --not-a-deploy with a live loop exits 0 and advances clone.

    Without --not-a-deploy, a live loop on the clone blocks the merge (exit 2).
    With --not-a-deploy, the live-loop hold is skipped.
    """
    if not shutil.which("pgrep"):
        pytest.skip("pgrep not available")

    fx = _make_release_fixture(tmp_path)
    dev_clone = fx["dev_clone"]

    # Create a worktree for the merge.
    wt_path = tmp_path / "selfmod-wt"
    _git(dev_clone, "worktree", "add", "--detach", str(wt_path))
    (wt_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(wt_path, "add", "change.txt")
    _git(wt_path, "commit", "-q", "-m", "selfmod change")

    clone_head_before = _git(dev_clone, "rev-parse", "HEAD")

    # Spawn a fake live loop whose argv contains the clone path.
    token = f"ILKTEST{os.getpid()}"
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    fake_runner = bindir / "run_ilk_loop_claude.sh"
    fake_runner.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
    fake_runner.chmod(0o755)

    proc = subprocess.Popen(
        [str(fake_runner), "--project-path", str(dev_clone), "--token", token],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import time
        time.sleep(0.5)  # let it appear in process table

        # Merge with --not-a-deploy should succeed despite live loop.
        sw_script = fx["release_skills"] / "ilk-loop" / "scripts" / "selfmod_worktree.py"
        result = subprocess.run(
            [sys.executable, str(sw_script), "merge",
             "--not-a-deploy",
             "--probe-pattern", token,
             str(dev_clone), str(wt_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        assert result.returncode == 0, (
            f"merge --not-a-deploy should exit 0 with live loop, "
            f"got {result.returncode}: {result.stderr}"
        )

        clone_head_after = _git(dev_clone, "rev-parse", "HEAD")
        assert clone_head_after != clone_head_before, (
            "clone HEAD should advance after merge"
        )
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac3_runner_passes_not_a_deploy_flag(tmp_path: Path) -> None:
    """AC-3: merge_selfmod_worktree in the runner passes --not-a-deploy
    to the merge CLI when running from a release.

    Replace selfmod_worktree.py with a stub that records its argv.
    """
    fx = _make_release_fixture(tmp_path)
    dev_clone = fx["dev_clone"]

    # Create a worktree.
    wt_path = tmp_path / "selfmod-wt"
    _git(dev_clone, "worktree", "add", "--detach", str(wt_path))
    (wt_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(wt_path, "add", "change.txt")
    _git(wt_path, "commit", "-q", "-m", "selfmod change")

    # Replace selfmod_worktree.py with a stub.
    stub = fx["release_skills"] / "ilk-loop" / "scripts" / "selfmod_worktree.py"
    argv_capture = tmp_path / "argv.json"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys
        with open("{argv_capture}", "w") as f:
            json.dump(sys.argv, f)
        sys.exit(0)
    """), encoding="utf-8")

    env = _sandbox_env(tmp_path)
    env["_SKILL_ROOT"] = str(fx["release_skills"])
    env["SELFMOD_WORKTREE_PATH"] = str(wt_path)
    env["SELFMOD_ORIGINAL_PROJECT_PATH"] = str(dev_clone)
    env["SELFMOD_MERGE_LOCK_PATH"] = str(tmp_path / "merge.lock")
    env["PROJECT_KEY"] = "test-project"

    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export _SKILL_ROOT='{fx["release_skills"]}'
        source '{fx["runner"]}'
        PROJECT_PATH='{dev_clone}'
        merge_selfmod_worktree
        echo "RC=$?"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    assert result.returncode == 0, (
        f"merge_selfmod_worktree should exit 0: {result.stderr}"
    )
    assert argv_capture.exists(), "stub was not called"

    argv = json.loads(argv_capture.read_text(encoding="utf-8"))
    assert "--not-a-deploy" in argv, (
        f"--not-a-deploy not in argv: {argv}"
    )


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac4_release_skips_yield_check(tmp_path: Path) -> None:
    """AC-4: from a release, _check_other_project_merge_pending returns 1
    (no yield) when another project is merge-pending.

    Under a release, no merge is deferred by a live loop, so the yield
    check is unnecessary and should return immediately.
    """
    fx = _make_release_fixture(tmp_path)
    dev_clone = fx["dev_clone"]

    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True)

    # Create a project with merge pending.
    _make_project_with_merge_pending(tmp_path, "other-project", data_home)

    # Create a minimal current project entry.
    import ilk_paths
    current_key = ilk_paths.project_key(dev_clone)
    current_ext = data_home / "projects" / current_key
    (current_ext / "runtime" / "launcher").mkdir(parents=True)
    (current_ext / "runtime" / "launcher" / "last-exit.json").write_text(
        json.dumps({"state": "running", "pid": os.getpid()}),
        encoding="utf-8",
    )

    env = _sandbox_env(tmp_path)
    env["_SKILL_ROOT"] = str(fx["release_skills"])
    env["PROJECT_KEY"] = current_key

    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export _SKILL_ROOT='{fx["release_skills"]}'
        source '{fx["runner"]}'
        PROJECT_PATH='{dev_clone}'
        _yield=$(_check_other_project_merge_pending) && rc=0 || rc=$?
        echo "YIELD=$_yield"
        echo "RC=$rc"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    lines = result.stdout.splitlines()
    rc_line = [l for l in lines if l.startswith("RC=")]
    assert rc_line, f"no RC output: {result.stdout}\n{result.stderr}"
    rc_val = rc_line[-1].split("=", 1)[1]
    assert rc_val == "1", (
        f"_check_other_project_merge_pending should return 1 under a release "
        f"(no yield), got exit {rc_val}"
    )


# ── Controls (expected to pass today) ────────────────────────────────────────


def test_ac5_control_merge_blocked_without_flag(tmp_path: Path) -> None:
    """AC-5 (control): merge without --not-a-deploy exits 2 when a live
    loop is detected.

    This passes today: the live-loop hold is the current behaviour.
    """
    if not shutil.which("pgrep"):
        pytest.skip("pgrep not available")

    dev_clone = _create_throwaway_repo(tmp_path)
    wt_path = tmp_path / "selfmod-wt"
    _git(dev_clone, "worktree", "add", "--detach", str(wt_path))
    (wt_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(wt_path, "add", "change.txt")
    _git(wt_path, "commit", "-q", "-m", "selfmod change")

    token = f"ILKCTRL{os.getpid()}"
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    fake_runner = bindir / "run_ilk_loop_claude.sh"
    fake_runner.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
    fake_runner.chmod(0o755)

    proc = subprocess.Popen(
        [str(fake_runner), "--project-path", str(dev_clone), "--token", token],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import time
        time.sleep(0.5)

        sw_script = _SCRIPTS / "selfmod_worktree.py"
        result = subprocess.run(
            [sys.executable, str(sw_script), "merge",
             "--probe-pattern", token,
             str(dev_clone), str(wt_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        assert result.returncode == 2, (
            f"merge without --not-a-deploy should exit 2 with live loop, "
            f"got {result.returncode}: {result.stderr}"
        )
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_ac6_control_clone_layout_yields(tmp_path: Path) -> None:
    """AC-6 (control): _check_other_project_merge_pending from clone layout
    still prints the other project's key.

    This passes today: the yield check works for the clone layout.
    """
    data_home = tmp_path / ".ilk-data"
    data_home.mkdir(parents=True)

    # Current project (clone layout = sourced from this tree).
    dev_clone = _create_throwaway_repo(tmp_path)
    import ilk_paths
    current_key = ilk_paths.project_key(dev_clone)
    current_ext = data_home / "projects" / current_key
    (current_ext / "runtime" / "launcher").mkdir(parents=True)
    (current_ext / "runtime" / "launcher" / "last-exit.json").write_text(
        json.dumps({"state": "running", "pid": os.getpid()}),
        encoding="utf-8",
    )

    # Other project with merge pending.
    _make_project_with_merge_pending(tmp_path, "other-project", data_home)

    env = _sandbox_env(tmp_path)
    env["_SKILL_ROOT"] = str(_REPO / "skills")
    env["ILK_SKILL_HOME"] = str(_REPO / "skills")
    env["PROJECT_KEY"] = current_key

    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export _SKILL_ROOT='{_REPO / "skills"}'
        export ILK_SKILL_HOME='{_REPO / "skills"}'
        source '{_REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"}'
        PROJECT_PATH='{dev_clone}'
        _yield=$(_check_other_project_merge_pending) && rc=0 || rc=$?
        echo "YIELD=$_yield"
        echo "RC=$rc"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    lines = result.stdout.splitlines()
    yield_line = [l for l in lines if l.startswith("YIELD=")]
    rc_line = [l for l in lines if l.startswith("RC=")]
    assert rc_line, f"no RC output: {result.stdout}\n{result.stderr}"
    rc_val = rc_line[-1].split("=", 1)[1]
    assert rc_val == "0", (
        f"_check_other_project_merge_pending should exit 0 (yield needed), "
        f"got exit {rc_val}"
    )
    assert yield_line, f"no YIELD output: {result.stdout}\n{result.stderr}"
    yield_val = yield_line[-1].split("=", 1)[1]
    assert yield_val, (
        "should print the other project's key"
    )