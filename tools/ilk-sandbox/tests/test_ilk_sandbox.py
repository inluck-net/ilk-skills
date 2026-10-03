"""Tests for the sandbox launcher and stable-home write refusal.

Sub-plan ``a-sandbox-cannot-write-the-stable-home`` (MASTER-2026-10-02d).

AC-1..AC-5 are ``xfail(strict=True, reason="red-first")`` — the sandbox
launcher and guards do not exist yet.  AC-6 is a plain control that must
pass today (nothing refuses writes when ``ILK_SANDBOX`` is unset).

**Every test pins HOME, ILK_DATA_HOME, ILK_RELEASES_ROOT and all other
paths to tmp.  Never pass the real home, ``~/.ilk-data`` or
``~/.ilk-sandbox`` to anything.**
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

# ── Paths ────────────────────────────────────────────────────────────────────

SANDBOX_SH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "tools" / "ilk-sandbox" / "ilk-sandbox.sh"
)
RUNNER_SH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
)
SCHEDULER_SH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
)
INSTALL_SCHEDULER = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "skills" / "ilk-watchdog" / "scripts" / "install-scheduler-autostart.sh"
)
SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "skills" / "ilk-loop" / "scripts"
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def sandbox_dirs(tmp_path: Path):
    """Create the tmp directory structure every test needs.

    Returns a dict with keys: ``root``, ``stable``, ``worktree``,
    ``home``, ``releases``, and ``skills``.
    """
    root = tmp_path / "sb"
    stable = tmp_path / "stable"
    worktree = tmp_path / "worktree"
    home = tmp_path / "home"
    releases = tmp_path / "releases"
    root.mkdir()
    stable.mkdir()
    worktree.mkdir()
    home.mkdir()
    releases.mkdir()
    # The worktree must be a git repo with a skills/ dir.
    subprocess.run(["git", "init"], cwd=worktree, check=True,
                   capture_output=True, timeout=10)
    skills = worktree / "skills"
    skills.mkdir()
    # Minimal marker so ilk-sandbox.sh can verify the dir.
    (skills / ".marker").touch()
    return {
        "root": root,
        "stable": stable,
        "worktree": worktree,
        "home": home,
        "releases": releases,
        "skills": skills,
    }


def _sandbox_env(home: Path, stable: Path, releases: Path) -> dict[str, str]:
    """Build a clean environment that pins ``HOME``, ``ILK_DATA_HOME``,
    ``ILK_RELEASES_ROOT``, and ``PATH`` — nothing from the real user."""
    return {
        "HOME": str(home),
        "ILK_DATA_HOME": str(stable),
        "ILK_RELEASES_ROOT": str(releases),
        "PATH": os.environ.get("PATH", ""),
        # Ensure no inherited alias leaks in.
        "ILK_DATA_DIR": "",
    }


# ── AC-1: pinned env ────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac1_sandbox_env_is_pinned(sandbox_dirs: dict) -> None:
    """``ilk-sandbox.sh --root <root> --worktree <wt> -- env`` prints
    all expected pinned variables and omits ``ILK_DATA_DIR``."""
    d = sandbox_dirs
    env = _sandbox_env(d["home"], d["stable"], d["releases"])
    result = subprocess.run(
        [
            "bash", str(SANDBOX_SH),
            "--root", str(d["root"]),
            "--worktree", str(d["worktree"]),
            "--", "env",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    out = result.stdout
    assert f"HOME={d['root']}/home" in out
    assert f"ILK_DATA_HOME={d['root']}/data" in out
    assert f"CLAUDE_CONFIG_DIR={d['root']}/claude-config" in out
    real_wt = d["worktree"].resolve()
    assert f"ILK_SKILL_HOME={real_wt}/skills" in out
    assert "ILK_SANDBOX=1" in out
    stable_real = d["stable"].resolve()
    assert f"ILK_STABLE_DATA_HOME={stable_real}" in out
    # ILK_DATA_DIR must not appear.
    for line in out.splitlines():
        assert not line.startswith("ILK_DATA_DIR="), (
            f"ILK_DATA_DIR leaked: {line}"
        )


# ── AC-2: refusal cases ─────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac2_missing_worktree_exits_nonzero(sandbox_dirs: dict) -> None:
    """Missing ``--worktree`` exits non-zero and does not run the command."""
    if not SANDBOX_SH.exists():
        pytest.fail("ilk-sandbox.sh does not exist yet — red-first pin")
    d = sandbox_dirs
    marker = d["root"] / "marker"
    env = _sandbox_env(d["home"], d["stable"], d["releases"])
    result = subprocess.run(
        [
            "bash", str(SANDBOX_SH),
            "--root", str(d["root"]),
            "--worktree", "/nonexistent/path",
            "--", "touch", str(marker),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode != 0
    assert not marker.exists(), "command ran despite invalid worktree"


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac2_worktree_under_releases_exits_nonzero(sandbox_dirs: dict) -> None:
    """A worktree whose realpath is under ``ILK_RELEASES_ROOT`` is refused."""
    if not SANDBOX_SH.exists():
        pytest.fail("ilk-sandbox.sh does not exist yet — red-first pin")
    d = sandbox_dirs
    # Create a worktree inside releases.
    bad_wt = d["releases"] / "v1" / "repo"
    bad_wt.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=bad_wt, check=True,
                   capture_output=True, timeout=10)
    (bad_wt / "skills").mkdir()
    marker = d["root"] / "marker"
    env = _sandbox_env(d["home"], d["stable"], d["releases"])
    result = subprocess.run(
        [
            "bash", str(SANDBOX_SH),
            "--root", str(d["root"]),
            "--worktree", str(bad_wt),
            "--", "touch", str(marker),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode != 0
    assert not marker.exists(), "command ran despite worktree under releases"


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac2_detach_flag_exits_nonzero(sandbox_dirs: dict) -> None:
    """``--detach`` in sandbox args exits non-zero."""
    if not SANDBOX_SH.exists():
        pytest.fail("ilk-sandbox.sh does not exist yet — red-first pin")
    d = sandbox_dirs
    marker = d["root"] / "marker"
    env = _sandbox_env(d["home"], d["stable"], d["releases"])
    result = subprocess.run(
        [
            "bash", str(SANDBOX_SH),
            "--root", str(d["root"]),
            "--worktree", str(d["worktree"]),
            "--detach",
            "--", "touch", str(marker),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode != 0
    assert not marker.exists(), "command ran despite --detach"


# ── AC-3: Python writers refuse ─────────────────────────────────────────────

def _source_runner_and_call(
    func_call: str,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Dot-source the runner and execute *func_call* in the same shell."""
    env: dict[str, str] = {
        "ILK_DOTSOURCE_ONLY": "1",
        "PATH": os.environ.get("PATH", ""),
    }
    if env_extra:
        env.update(env_extra)
    script = (
        f"export ILK_DOTSOURCE_ONLY=1; "
        f"source '{RUNNER_SH}' 2>/dev/null; "
        f"set +e; "
        f"{func_call}"
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac3_append_revert_row_refuses_under_sandbox(
    sandbox_dirs: dict,
    tmp_path: Path,
) -> None:
    """``append_revert_row`` into the stable home raises ``PermissionError``
    under ``ILK_SANDBOX=1`` and writes nothing."""
    d = sandbox_dirs
    stable = d["stable"]
    target = stable / "projects" / "k" / "runtime" / "launcher" / "ship-reverts.jsonl"
    # Must not pre-exist.
    assert not target.exists()

    script = textwrap.dedent(f"""\
        import sys
        sys.path.insert(0, "{SCRIPTS_DIR}")
        from revert_notice import append_revert_row
        try:
            append_revert_row(
                "{target}",
                slug="test-slug",
                ship_commit="abc1234",
                reason="test",
                from_status="shipped",
                to_status="in-progress",
                from_step=2,
                to_step=1,
                run_id="r01",
                iteration=1,
                timestamp="2026-01-01T00:00:00",
                site="integrity",
            )
        except PermissionError:
            sys.exit(10)
        sys.exit(0)
    """)
    env = {
        "HOME": str(d["home"]),
        "ILK_DATA_HOME": str(stable),
        "ILK_SANDBOX": "1",
        "ILK_STABLE_DATA_HOME": str(stable.resolve()),
        "PATH": os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        ["python3", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 10, (
        f"expected PermissionError (exit 10), got exit {result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not target.exists(), "file was created despite refusal"


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac3_append_record_refuses_under_sandbox(
    sandbox_dirs: dict,
    tmp_path: Path,
) -> None:
    """``append_record`` into the stable home raises ``PermissionError``
    under ``ILK_SANDBOX=1`` and writes nothing."""
    d = sandbox_dirs
    stable = d["stable"]
    target = stable / "projects" / "k" / "runtime" / "launcher" / "ship-proof.jsonl"
    assert not target.exists()

    script = textwrap.dedent(f"""\
        import sys
        sys.path.insert(0, "{SCRIPTS_DIR}")
        from ship_proof_ledger import append_record
        from pathlib import Path
        try:
            append_record(
                Path("{target}"),
                {{"run_id":"r01","iteration":1,"slug":"test","repo":"/tmp","step_from":0,"step_to":1,"commits":["abc"]}},
            )
        except PermissionError:
            sys.exit(10)
        sys.exit(0)
    """)
    env = {
        "HOME": str(d["home"]),
        "ILK_DATA_HOME": str(stable),
        "ILK_SANDBOX": "1",
        "ILK_STABLE_DATA_HOME": str(stable.resolve()),
        "PATH": os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        ["python3", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 10, (
        f"expected PermissionError (exit 10), got exit {result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not target.exists(), "file was created despite refusal"


# ── AC-4: _runtime_file refuses ──────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac4_runtime_file_refuses_under_sandbox(
    sandbox_dirs: dict,
    tmp_path: Path,
) -> None:
    """With ``ILK_SANDBOX=1`` and the half-pinned case (``ILK_DATA_HOME``
    pointing to the stable home), ``_runtime_file ship-reverts.jsonl``
    returns non-zero with empty stdout and the refusal on stderr."""
    d = sandbox_dirs
    stable = d["stable"]
    wt = d["worktree"]
    env = {
        "HOME": str(d["home"]),
        "ILK_DATA_HOME": str(stable),
        "ILK_SANDBOX": "1",
        "ILK_STABLE_DATA_HOME": str(stable.resolve()),
        "PATH": os.environ.get("PATH", ""),
        "ILK_DOTSOURCE_ONLY": "1",
    }
    # Use the worktree as PROJECT_PATH so get_ilk_runtime_dir can resolve.
    script = (
        f"export ILK_DOTSOURCE_ONLY=1; "
        f"source '{RUNNER_SH}' 2>/dev/null; "
        f"PROJECT_PATH='{wt}'; "
        f"_runtime_file ship-reverts.jsonl"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode != 0, (
        f"expected nonzero exit, got {result.returncode}"
    )
    assert result.stdout.strip() == "", (
        f"stdout should be empty, got: {result.stdout!r}"
    )
    assert "refusing" in result.stderr.lower() or "stable" in result.stderr.lower(), (
        f"expected refusal on stderr, got: {result.stderr!r}"
    )


# ── AC-5: no daemons in sandbox ─────────────────────────────────────────────

@pytest.mark.expects_blocked_host  # install script may call launchctl
@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac5_install_scheduler_exits_nonzero_under_sandbox(
    sandbox_dirs: dict,
) -> None:
    """``install-scheduler-autostart.sh`` exits non-zero under
    ``ILK_SANDBOX=1`` and writes no plist."""
    d = sandbox_dirs
    # The plist path is $HOME/Library/LaunchAgents/...
    plist_dir = d["home"] / "Library" / "LaunchAgents"
    plist = plist_dir / "net.inluck.ilk.scheduler.plist"
    env = _sandbox_env(d["home"], d["stable"], d["releases"])
    env["ILK_SANDBOX"] = "1"
    env["ILK_STABLE_DATA_HOME"] = str(d["stable"].resolve())
    # Point the script at our temp skill root.
    skill_root = SANDBOX_SH.parent.parent.parent
    env["ILK_SKILL_HOME"] = str(skill_root)
    result = subprocess.run(
        ["bash", str(INSTALL_SCHEDULER)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode != 0, (
        f"expected nonzero exit, got {result.returncode}: {result.stdout}"
    )
    assert not plist.exists(), f"plist was written: {plist}"


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac5_scheduler_exits_nonzero_under_sandbox(
    sandbox_dirs: dict,
) -> None:
    """``scheduler.sh --once --dry-run`` exits non-zero under
    ``ILK_SANDBOX=1`` within 10 s and writes no pidfile."""
    d = sandbox_dirs
    pidfile = d["home"] / ".ilk-data" / "scheduler.pid"
    env = _sandbox_env(d["home"], d["stable"], d["releases"])
    env["ILK_SANDBOX"] = "1"
    env["ILK_STABLE_DATA_HOME"] = str(d["stable"].resolve())
    result = subprocess.run(
        [
            "bash", str(SCHEDULER_SH),
            "--once", "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert result.returncode != 0, (
        f"expected nonzero exit, got {result.returncode}: {result.stdout}"
    )
    assert not pidfile.exists(), f"pidfile was written: {pidfile}"


# ── AC-6: control (no sandbox) ──────────────────────────────────────────────

def test_ac6_writers_succeed_without_sandbox(
    sandbox_dirs: dict,
    tmp_path: Path,
) -> None:
    """Without ``ILK_SANDBOX``, ``append_revert_row`` and ``append_record``
    succeed into a tmp path.  With ``ILK_SANDBOX=1``, a write under the
    sandbox's own data dir (outside the stable home) also succeeds.
    Both must pass today and keep passing."""
    d = sandbox_dirs

    # Part A: no ILK_SANDBOX → writes succeed.
    target_a = tmp_path / "control-reverts.jsonl"
    script_a = textwrap.dedent(f"""\
        import sys
        sys.path.insert(0, "{SCRIPTS_DIR}")
        from revert_notice import append_revert_row
        append_revert_row(
            "{target_a}",
            slug="ctrl",
            ship_commit="abc1234",
            reason="test",
            from_status="shipped",
            to_status="in-progress",
            from_step=2,
            to_step=1,
            run_id="r01",
            iteration=1,
            timestamp="2026-01-01T00:00:00",
            site="integrity",
        )
    """)
    env_a = {
        "HOME": str(d["home"]),
        "PATH": os.environ.get("PATH", ""),
    }
    result_a = subprocess.run(
        ["python3", "-c", script_a],
        capture_output=True,
        text=True,
        timeout=30,
        env=env_a,
    )
    assert result_a.returncode == 0, (
        f"no-sandbox write failed: {result_a.stderr}"
    )
    assert target_a.exists(), "no-sandbox write did not create the file"

    # Part B: ILK_SANDBOX=1, write OUTSIDE the stable home → succeeds.
    sandbox_data = d["root"] / "data"
    sandbox_data.mkdir(parents=True, exist_ok=True)
    target_b = sandbox_data / "control-reverts.jsonl"
    script_b = textwrap.dedent(f"""\
        import sys
        sys.path.insert(0, "{SCRIPTS_DIR}")
        from revert_notice import append_revert_row
        append_revert_row(
            "{target_b}",
            slug="ctrl",
            ship_commit="abc1234",
            reason="test",
            from_status="shipped",
            to_status="in-progress",
            from_step=2,
            to_step=1,
            run_id="r01",
            iteration=1,
            timestamp="2026-01-01T00:00:00",
            site="integrity",
        )
    """)
    env_b = {
        "HOME": str(d["home"]),
        "ILK_SANDBOX": "1",
        "ILK_STABLE_DATA_HOME": str(d["stable"].resolve()),
        "PATH": os.environ.get("PATH", ""),
    }
    result_b = subprocess.run(
        ["python3", "-c", script_b],
        capture_output=True,
        text=True,
        timeout=30,
        env=env_b,
    )
    assert result_b.returncode == 0, (
        f"sandbox non-stable write failed: {result_b.stderr}"
    )
    assert target_b.exists(), "sandbox non-stable write did not create the file"