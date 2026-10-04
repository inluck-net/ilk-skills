"""A release host never runs from the clone.

Part of sub-plan a-release-host-never-runs-from-the-clone.  These tests
verify that launch.sh, watchdog.sh, and scheduler.sh refuse (exit 3) when
run from a git tree on a host whose layout file reads ``release``.

  AC-1   release layout + clone dir → exit 3  (xfail until guard exists)
  AC-2   release layout + ILK_SKILL_HOME=clone → exit 3  (xfail)
  AC-3   release layout + ILK_ALLOW_CLONE_RUN=1 → exit 0  (control)
  AC-4   no layout file / layout=clone + clone → exit 0  (control)
  AC-5   release layout + release dir (no .git) → exit 0  (control)
  AC-6   selfmod worktree under release layout → exit 3  (xfail)
  AC-7   sourcing (ILK_SKIP_MAIN=1 / ILK_DOTSOURCE_ONLY=1) → no refusal  (control)
  AC-8   ilk_host_layout prints ``release`` only for exact content  (xfail)
  AC-10  ilk-run.sh refuses, no master promotion  (xfail)

All tests build a fake HOME under tmp_path with (or without) ``.ilk/layout``,
copy ``skills/`` into a tmp tree, and never run against the real ``~/.ilk``.
No test starts a real loop, ``screen``, or ``launchctl``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_real_data_home

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SKILLS_DIR = _REPO_ROOT / "skills"

_SCRIPTS = {
    "launch.sh": _SKILLS_DIR / "ilk-launcher" / "scripts" / "launch.sh",
    "watchdog.sh": _SKILLS_DIR / "ilk-watchdog" / "scripts" / "watchdog.sh",
    "scheduler.sh": _SKILLS_DIR / "ilk-watchdog" / "scripts" / "scheduler.sh",
    "ilk-run.sh": _SKILLS_DIR / "ilk-runner" / "scripts" / "ilk-run.sh",
}

_PATH = os.environ.get("PATH", "")


# ── helpers ──────────────────────────────────────────────────────────────────


def _copy_skills(src: Path, dst: Path) -> None:
    """Copy the skills tree, ignoring __pycache__ and tests."""
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )


def _init_git_repo(repo_dir: Path) -> None:
    """Turn *repo_dir* into a git repo with one commit."""
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
    (repo_dir / "README.md").write_text("stub\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test",
        },
    )


def _env_for(home: Path, releases_root: Path | None = None) -> dict[str, str]:
    """Build a clean env with HOME pinned and no inherited ILK vars."""
    env = {
        "PATH": _PATH,
        "HOME": str(home),
        "ILK_DATA_HOME": str(home / ".ilk-data"),
    }
    if releases_root is not None:
        env["ILK_RELEASES_ROOT"] = str(releases_root)
    # Strip vars the guard reads.
    for var in ("ILK_SKILL_HOME", "ILK_ALLOW_CLONE_RUN"):
        env[var] = ""
    return env


def _run_script(
    script: Path,
    env: dict[str, str],
    *,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a script with ``--help`` and return the result."""
    cmd = ["bash", str(script)] + (extra_args or ["--help"])
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def release_layout_home(tmp_path: Path) -> Path:
    """A fake HOME whose ``.ilk/layout`` reads ``release``."""
    home = tmp_path / "home"
    home.mkdir()
    ilk = home / ".ilk"
    ilk.mkdir()
    (ilk / "layout").write_text("release\n", encoding="utf-8")
    (ilk / "releases").mkdir()
    return home


@pytest.fixture
def clone_dir(tmp_path: Path) -> Path:
    """A git repo containing a copy of ``skills/``."""
    clone = tmp_path / "clone"
    clone.mkdir()
    _init_git_repo(clone)
    _copy_skills(_SKILLS_DIR, clone / "skills")
    return clone


@pytest.fixture
def release_skills_dir(tmp_path: Path) -> Path:
    """A non-git dir containing a copy of ``skills/`` (simulating a release)."""
    rel = tmp_path / "releases" / "v9"
    rel.mkdir(parents=True)
    _copy_skills(_SKILLS_DIR, rel / "skills")
    # Symlink current -> v9
    current = tmp_path / "current"
    current.symlink_to(rel)
    return rel


# ── AC-1: release layout + clone → exit 3 ────────────────────────────────────


@pytest.mark.parametrize("script_name", ["launch.sh", "watchdog.sh", "scheduler.sh"])
def test_ac1_release_layout_clone_refuses(
    script_name: str,
    release_layout_home: Path,
    clone_dir: Path,
) -> None:
    """AC-1: under release layout, running from a git tree → exit 3."""
    releases_root = release_layout_home / ".ilk" / "releases"
    env = _env_for(release_layout_home, releases_root)
    script = clone_dir / "skills" / _SCRIPTS[script_name].relative_to(_SKILLS_DIR)
    result = _run_script(script, env)
    assert result.returncode == 3, (
        f"{script_name} returned {result.returncode}, expected 3. "
        f"stderr={result.stderr!r}"
    )
    assert "clone-run-on-release-host" in result.stderr


# ── AC-2: release layout + ILK_SKILL_HOME=clone → exit 3 ────────────────────


@pytest.mark.parametrize("script_name", ["launch.sh", "watchdog.sh"])
def test_ac2_release_layout_skill_home_clone_refuses(
    script_name: str,
    release_layout_home: Path,
    clone_dir: Path,
    tmp_path: Path,
) -> None:
    """AC-2: ILK_SKILL_HOME points to a clone → still exit 3."""
    releases_root = release_layout_home / ".ilk" / "releases"
    # Put the script in a non-git dir but set ILK_SKILL_HOME to the clone.
    non_git = tmp_path / "non-git"
    non_git.mkdir()
    shutil.copytree(clone_dir / "skills", non_git / "skills")
    env = _env_for(release_layout_home, releases_root)
    env["ILK_SKILL_HOME"] = str(clone_dir / "skills")
    script = non_git / "skills" / _SCRIPTS[script_name].relative_to(_SKILLS_DIR)
    result = _run_script(script, env)
    assert result.returncode == 3, (
        f"{script_name} returned {result.returncode}, expected 3. "
        f"stderr={result.stderr!r}"
    )
    assert "clone-run-on-release-host" in result.stderr


# ── AC-3: release layout + ILK_ALLOW_CLONE_RUN=1 → exit 0 ───────────────────


@pytest.mark.parametrize("script_name", ["launch.sh", "watchdog.sh", "scheduler.sh"])
def test_ac3_clone_allowed_with_override(
    script_name: str,
    release_layout_home: Path,
    clone_dir: Path,
) -> None:
    """AC-3 (control): ILK_ALLOW_CLONE_RUN=1 bypasses the guard."""
    releases_root = release_layout_home / ".ilk" / "releases"
    env = _env_for(release_layout_home, releases_root)
    env["ILK_ALLOW_CLONE_RUN"] = "1"
    script = clone_dir / "skills" / _SCRIPTS[script_name].relative_to(_SKILLS_DIR)
    result = _run_script(script, env)
    assert result.returncode == 0, (
        f"{script_name} returned {result.returncode}, expected 0. "
        f"stderr={result.stderr!r}"
    )


# ── AC-4: no layout file / layout=clone → exit 0 ────────────────────────────


@pytest.mark.parametrize("layout_content", [None, "clone\n"])
@pytest.mark.parametrize("script_name", ["launch.sh", "watchdog.sh", "scheduler.sh"])
def test_ac4_no_release_layout_allows_clone(
    layout_content: str | None,
    script_name: str,
    tmp_path: Path,
    clone_dir: Path,
) -> None:
    """AC-4 (control): no layout file or layout=clone → no refusal."""
    home = tmp_path / "home"
    home.mkdir()
    ilk = home / ".ilk"
    ilk.mkdir()
    (ilk / "releases").mkdir()
    if layout_content is not None:
        (ilk / "layout").write_text(layout_content, encoding="utf-8")
    releases_root = ilk / "releases"
    env = _env_for(home, releases_root)
    script = clone_dir / "skills" / _SCRIPTS[script_name].relative_to(_SKILLS_DIR)
    result = _run_script(script, env)
    assert result.returncode == 0, (
        f"{script_name} returned {result.returncode}, expected 0. "
        f"stderr={result.stderr!r}"
    )


# ── AC-5: release layout + release dir (no .git) → exit 0 ────────────────────


@pytest.mark.parametrize("script_name", ["launch.sh", "watchdog.sh", "scheduler.sh"])
def test_ac5_release_dir_no_git_allows(
    script_name: str,
    release_layout_home: Path,
    release_skills_dir: Path,
    tmp_path: Path,
) -> None:
    """AC-5 (control): skills in a release dir with no .git → no refusal."""
    releases_root = release_layout_home / ".ilk" / "releases"
    # Create current symlink under releases_root
    current = release_layout_home / ".ilk" / "current"
    current.symlink_to(release_skills_dir)
    env = _env_for(release_layout_home, releases_root)
    script = (
        release_skills_dir
        / "skills"
        / _SCRIPTS[script_name].relative_to(_SKILLS_DIR)
    )
    result = _run_script(script, env)
    assert result.returncode == 0, (
        f"{script_name} returned {result.returncode}, expected 0. "
        f"stderr={result.stderr!r}"
    )


# ── AC-6: selfmod worktree under release layout → exit 3 ────────────────────


@pytest.mark.parametrize("script_name", ["launch.sh", "watchdog.sh", "scheduler.sh"])
def test_ac6_selfmod_worktree_refuses(
    script_name: str,
    release_layout_home: Path,
    clone_dir: Path,
    tmp_path: Path,
) -> None:
    """AC-6: a git worktree (.git is a file) under release layout → exit 3."""
    # Create a worktree from the clone
    wt_dir = tmp_path / "worktree"
    subprocess.run(
        ["git", "worktree", "add", str(wt_dir), "HEAD"],
        cwd=clone_dir,
        capture_output=True,
        check=True,
    )
    _copy_skills(_SKILLS_DIR, wt_dir / "skills")
    releases_root = release_layout_home / ".ilk" / "releases"
    env = _env_for(release_layout_home, releases_root)
    script = wt_dir / "skills" / _SCRIPTS[script_name].relative_to(_SKILLS_DIR)
    result = _run_script(script, env)
    assert result.returncode == 3, (
        f"{script_name} returned {result.returncode}, expected 3. "
        f"stderr={result.stderr!r}"
    )
    assert "clone-run-on-release-host" in result.stderr


# ── AC-7: sourcing does not refuse ───────────────────────────────────────────


@pytest.mark.parametrize(
    "script_name,env_var",
    [
        ("launch.sh", "ILK_SKIP_MAIN"),
        ("scheduler.sh", "ILK_DOTSOURCE_ONLY"),
    ],
)
def test_ac7_sourcing_does_not_refuse(
    script_name: str,
    env_var: str,
    release_layout_home: Path,
    clone_dir: Path,
) -> None:
    """AC-7 (control): sourcing with skip/dotsource → no refusal."""
    releases_root = release_layout_home / ".ilk" / "releases"
    env = _env_for(release_layout_home, releases_root)
    env[env_var] = "1"
    script = clone_dir / "skills" / _SCRIPTS[script_name].relative_to(_SKILLS_DIR)
    result = subprocess.run(
        ["bash", "-c", f"source {script}; echo ok"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "ok" in result.stdout, (
        f"sourcing {script_name} with {env_var}=1 failed: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ── AC-8: ilk_host_layout content matching ───────────────────────────────────


@pytest.mark.parametrize(
    "content,expected",
    [
        ("release\n", "release"),
        ("release", "release"),
        ("release-x\n", "clone"),
        ("", "clone"),
        ("\n", "clone"),
    ],
)
def test_ac8_ilk_host_layout_content_matching(
    content: str,
    expected: str,
    tmp_path: Path,
) -> None:
    """AC-8: ilk_host_layout prints 'release' only for exact content."""
    home = tmp_path / "home"
    home.mkdir()
    ilk = home / ".ilk"
    ilk.mkdir()
    (ilk / "releases").mkdir()
    if content != "":
        (ilk / "layout").write_text(content, encoding="utf-8")
    # Source the helper and call ilk_host_layout
    helper = _SKILLS_DIR / "ilk-loop" / "scripts" / "_ilk_skill_root.sh"
    env = _env_for(home, home / ".ilk" / "releases")
    result = subprocess.run(
        [
            "bash", "-c",
            f"source {helper}; ilk_host_layout",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    actual = result.stdout.strip()
    assert actual == expected, (
        f"content={content!r}: expected {expected!r}, got {actual!r}. "
        f"stderr={result.stderr!r}"
    )


# ── AC-10: ilk-run.sh refuses and doesn't promote ───────────────────────────


def test_ac10_ilk_run_refuses_no_promotion(
    release_layout_home: Path,
    clone_dir: Path,
    tmp_path: Path,
) -> None:
    """AC-10: ilk-run.sh refuses on release host and doesn't promote a master."""
    releases_root = release_layout_home / ".ilk" / "releases"
    # Set up a fake plans dir with a queued master
    data_home = release_layout_home / ".ilk-data"
    plans_dir = data_home / "projects" / "test-key" / "plans"
    plans_dir.mkdir(parents=True)
    master = plans_dir / "MASTER-2026-10-04-test.md"
    master.write_text(
        "---\nmaster_plan: test\nstatus: queued\ncurrent_subplan: test\n---\n",
        encoding="utf-8",
    )
    env = _env_for(release_layout_home, releases_root)
    env["ILK_DATA_HOME"] = str(data_home)
    script = clone_dir / "skills" / "ilk-runner" / "scripts" / "ilk-run.sh"
    result = _run_script(script, env, extra_args=["--help"])
    assert result.returncode == 3, (
        f"ilk-run.sh returned {result.returncode}, expected 3. "
        f"stderr={result.stderr!r}"
    )
    assert "clone-run-on-release-host" in result.stderr
    # Verify master was not promoted
    master_text = master.read_text(encoding="utf-8")
    assert "queued" in master_text, "master was promoted despite refusal"