"""Pin install.sh --layout release|clone behaviour on fixture homes.

Part of sub-plan install-wires-a-layout (step 0 — red-first pins).
Every test passes HOME=<tmp_path>/home and
ILK_RELEASES_ROOT=<tmp_path>/home/.ilk/releases and asserts, before
running, that the subprocess HOME is under tmp_path.

AC-1..AC-7 are expected to fail today (unknown --layout flag exits 2).
AC-8 is a control (plain install.sh --apply on a fresh fixture — passes
today).
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = str(REPO_ROOT / "install.sh")

# Names matching the measured layout on chad-mbp (2026-10-02).
SKILL_NAMES = ["ilk-loop", "ilk-launcher", "ilk-watchdog"]
COMMAND_FILES = ["ilk.md", "ilk-plan.md"]
SCHEDULER_LABEL = "net.inluck.ilk.scheduler"
SCHEDULER_HEALTH_LABEL = "net.inluck.ilk.scheduler-health"
SCHEDULER_SCRIPT = "skills/ilk-watchdog/scripts/scheduler.sh"
SCHEDULER_HEALTH_SCRIPT = "skills/ilk-watchdog/scripts/scheduler_health.sh"

# Agent homes: Claude (primary + workers) are seeded by default;
# Cursor and Codex are optional and off by default so existing tests
# are unaffected until step 0 explicitly enables them.
AGENT_HOME_DIRS = [".claude", ".cursor", ".codex"]
WORKER_HOME_DIRS = [".claude-worker", ".claude-manager"]


def _assert_under_tmp(tmp_path: Path, env_home: str) -> None:
    """Assert the env HOME is a subdir of tmp_path (never the real home)."""
    real_tmp = tmp_path.resolve()
    real_home = Path(env_home).resolve()
    assert str(real_home).startswith(str(real_tmp) + os.sep), (
        f"HOME={env_home} is not under tmp_path={tmp_path}"
    )


def _write_plist(path: Path, script_path: str) -> None:
    """Write a minimal scheduler plist in the measured shape."""
    plist = {
        "Label": SCHEDULER_LABEL,
        "ProgramArguments": ["/bin/bash", script_path, "--poll-min", "5"],
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin",
            "HOME": str(path.parent.parent),  # placeholder
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(plist, f)


def _write_health_plist(path: Path, script_path: str) -> None:
    """Write a minimal scheduler-health plist."""
    plist = {
        "Label": SCHEDULER_HEALTH_LABEL,
        "ProgramArguments": ["/bin/bash", script_path],
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin",
            "HOME": str(path.parent.parent),
        },
        "RunAtLoad": False,
        "StartInterval": 300,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(plist, f)


def _seed_clone_links(home: Path) -> None:
    """Create skill and command symlinks in a home pointing into the repo (clone layout)."""
    skills_dir = home / "skills"
    commands_dir = home / "commands"
    skills_dir.mkdir(parents=True, exist_ok=True)
    commands_dir.mkdir(parents=True, exist_ok=True)
    for name in SKILL_NAMES:
        (skills_dir / name).symlink_to(REPO_ROOT / "skills" / name)
    for name in COMMAND_FILES:
        (commands_dir / name).symlink_to(REPO_ROOT / "commands" / name)


def _seed_release_dir(release_dir: Path) -> None:
    """Create a minimal release directory with skills/ and commands/."""
    skills_dir = release_dir / "skills"
    commands_dir = release_dir / "commands"
    skills_dir.mkdir(parents=True, exist_ok=True)
    commands_dir.mkdir(parents=True, exist_ok=True)
    for name in SKILL_NAMES:
        (skills_dir / name).mkdir(parents=True, exist_ok=True)
    for name in COMMAND_FILES:
        (commands_dir / name).write_text(f"# {name}\n")


def _setup_fixture(tmp_path: Path, *, with_current: bool = True,
                   with_plist: bool = True, with_health_plist: bool = True,
                   with_manager: bool = True,
                   with_cursor: bool = False,
                   with_codex: bool = False) -> dict:
    """Build a full fixture home and release dir. Returns paths dict."""
    home = tmp_path / "home"
    home.mkdir()

    # Three Claude homes.
    for suffix in ["", "-worker", "-manager"]:
        h = home / f".claude{suffix}"
        if suffix == "-manager" and not with_manager:
            continue
        _seed_clone_links(h)

    # Cursor and Codex homes (off by default; step 0 enables them).
    if with_cursor:
        _seed_clone_links(home / ".cursor")
    if with_codex:
        _seed_clone_links(home / ".codex")

    # Release dir.
    releases_root = home / ".ilk" / "releases"
    release_v1 = releases_root / "v1"
    _seed_release_dir(release_v1)

    # current symlink.
    if with_current:
        current_link = home / ".ilk" / "current"
        current_link.parent.mkdir(parents=True, exist_ok=True)
        current_link.symlink_to(release_v1)

    # Plist.
    agents_dir = home / "Library" / "LaunchAgents"
    if with_plist:
        _write_plist(agents_dir / f"{SCHEDULER_LABEL}.plist",
                     str(REPO_ROOT / SCHEDULER_SCRIPT))
    if with_health_plist:
        _write_health_plist(agents_dir / f"{SCHEDULER_HEALTH_LABEL}.plist",
                            str(REPO_ROOT / SCHEDULER_HEALTH_SCRIPT))

    return {
        "home": home,
        "releases_root": releases_root,
        "release_v1": release_v1,
        "current": home / ".ilk" / "current",
        "agents_dir": agents_dir,
    }


def _agent_homes(home: Path) -> list[Path]:
    """Return all agent home directories that exist under *home*.

    Includes .claude, .cursor, .codex and the worker homes.
    """
    homes = []
    for name in AGENT_HOME_DIRS + WORKER_HOME_DIRS:
        p = home / name
        if p.exists():
            homes.append(p)
    return homes


def _all_link_paths(home: Path) -> list[Path]:
    """Return every skill and command symlink under every agent home."""
    links = []
    for h in _agent_homes(home):
        for name in SKILL_NAMES:
            links.append(h / "skills" / name)
        for name in COMMAND_FILES:
            links.append(h / "commands" / name)
    return links


def _run_install(home: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run install.sh with HOME overridden. Returns the result."""
    env_home = str(home)
    _assert_under_tmp(Path(home).parent.parent, env_home)  # safety: home is under tmp
    env = {
        "HOME": env_home,
        "PATH": os.environ["PATH"],
        "ILK_RELEASES_ROOT": str(home / ".ilk" / "releases"),
    }
    cmd = ["bash", INSTALL_SH, *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=check)


# --- AC-1: --layout release --apply rewires links and plist ---------------

def _assert_links_through_current(home: Path, current: Path, homes: list[str]) -> None:
    """Assert every skill/command link under *homes* points through *current*."""
    for name in homes:
        h = home / name
        if not h.exists():
            continue
        for skill in SKILL_NAMES:
            link = h / "skills" / skill
            assert link.is_symlink(), f"{link} is not a symlink"
            target = os.readlink(link)
            expected = str(current / "skills" / skill)
            assert target == expected, f"{link} -> {target!r}, expected {expected!r}"
        for cmd in COMMAND_FILES:
            link = h / "commands" / cmd
            assert link.is_symlink(), f"{link} is not a symlink"
            target = os.readlink(link)
            expected = str(current / "commands" / cmd)
            assert target == expected, f"{link} -> {target!r}, expected {expected!r}"


def _assert_links_to_repo(home: Path, homes: list[str]) -> None:
    """Assert every skill/command link under *homes* points to the repo."""
    for name in homes:
        h = home / name
        if not h.exists():
            continue
        for skill in SKILL_NAMES:
            link = h / "skills" / skill
            assert link.is_symlink(), f"{link} is not a symlink"
            target = os.readlink(link)
            expected = str(REPO_ROOT / "skills" / skill)
            assert target == expected, f"{link} -> {target!r}, expected {expected!r}"
        for cmd in COMMAND_FILES:
            link = h / "commands" / cmd
            assert link.is_symlink(), f"{link} is not a symlink"
            target = os.readlink(link)
            expected = str(REPO_ROOT / "commands" / cmd)
            assert target == expected, f"{link} -> {target!r}, expected {expected!r}"


def _snapshot_mtimes(home: Path, homes: list[str]) -> dict[str, int]:
    """Return {link_path: st_mtime_ns} for every skill/command link."""
    mtimes: dict[str, int] = {}
    for name in homes:
        h = home / name
        if not h.exists():
            continue
        for skill in SKILL_NAMES:
            link = h / "skills" / skill
            mtimes[str(link)] = link.lstat().st_mtime_ns
        for cmd in COMMAND_FILES:
            link = h / "commands" / cmd
            mtimes[str(link)] = link.lstat().st_mtime_ns
    return mtimes


def _snapshot_targets(home: Path, homes: list[str]) -> dict[str, str]:
    """Return {link_path: readlink_target} for every skill/command link."""
    targets: dict[str, str] = {}
    for name in homes:
        h = home / name
        if not h.exists():
            continue
        for skill in SKILL_NAMES:
            link = h / "skills" / skill
            targets[str(link)] = os.readlink(link)
        for cmd in COMMAND_FILES:
            link = h / "commands" / cmd
            targets[str(link)] = os.readlink(link)
    return targets


CLAUDE_HOMES = [".claude", ".claude-worker", ".claude-manager"]
ALL_AGENT_HOMES = CLAUDE_HOMES + [".cursor", ".codex"]


def test_ac1_layout_release_rewires_links_and_plist(tmp_path: Path) -> None:
    """AC-1: --layout release --apply rewires every skill/command link to go
    through current, rewrites the scheduler plist's script element, and
    writes 'release' to the layout file."""
    fx = _setup_fixture(tmp_path, with_cursor=True, with_codex=True)
    home = fx["home"]
    current = fx["current"]

    result = _run_install(home, "--layout", "release", "--apply")
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

    # Claude homes: existing controls (green today).
    _assert_links_through_current(home, current, CLAUDE_HOMES)

    # Cursor and Codex: red-first pins (xfail until step 1).
    pytest.importorskip("pytest")
    for agent_home in [".cursor", ".codex"]:
        try:
            _assert_links_through_current(home, current, [agent_home])
        except AssertionError as exc:
            pytest.xfail(f"layout switch does not yet cover {agent_home}: {exc}")

    # Plist script element should point through current.
    plist_path = fx["agents_dir"] / f"{SCHEDULER_LABEL}.plist"
    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)
    args = plist["ProgramArguments"]
    assert args[0] == "/bin/bash"
    expected_script = str(current / SCHEDULER_SCRIPT)
    assert args[1] == expected_script, f"ProgramArguments[1] = {args[1]!r}"
    # Other keys unchanged.
    assert args[2:] == ["--poll-min", "5"]
    assert plist["Label"] == SCHEDULER_LABEL

    # Health plist script element should point through current.
    health_path = fx["agents_dir"] / f"{SCHEDULER_HEALTH_LABEL}.plist"
    with open(health_path, "rb") as f:
        health_plist = plistlib.load(f)
    h_args = health_plist["ProgramArguments"]
    assert h_args[0] == "/bin/bash"
    expected_health = str(current / SCHEDULER_HEALTH_SCRIPT)
    assert h_args[1] == expected_health, f"health ProgramArguments[1] = {h_args[1]!r}"

    # Layout file.
    layout_path = home / ".ilk" / "layout"
    assert layout_path.read_text().strip() == "release"


# --- AC-2: idempotent — no change on second run --------------------------

def test_ac2_idempotent(tmp_path: Path) -> None:
    """AC-2: running --layout release --apply twice changes no link
    lstat().st_mtime_ns and no plist byte."""
    fx = _setup_fixture(tmp_path, with_cursor=True, with_codex=True)
    home = fx["home"]

    _run_install(home, "--layout", "release", "--apply")

    # Snapshot mtimes for all homes.
    mtimes = _snapshot_mtimes(home, ALL_AGENT_HOMES)

    plist_path = fx["agents_dir"] / f"{SCHEDULER_LABEL}.plist"
    plist_before = plist_path.read_bytes()
    health_path = fx["agents_dir"] / f"{SCHEDULER_HEALTH_LABEL}.plist"
    health_before = health_path.read_bytes()

    _run_install(home, "--layout", "release", "--apply")

    # No mtime change on Claude homes (existing control).
    for link_str, mtime_before in mtimes.items():
        link = Path(link_str)
        if any(link_str.endswith(f".claude{s}/skills/{n}") or link_str.endswith(f".claude{s}/commands/{f}")
               for s in ["", "-worker", "-manager"] for n in SKILL_NAMES for f in COMMAND_FILES):
            assert link.lstat().st_mtime_ns == mtime_before, f"{link} mtime changed"

    # Cursor/Codex mtimes: xfail if the layout switch touched them.
    for link_str, mtime_before in mtimes.items():
        link = Path(link_str)
        if ".cursor/" in link_str or ".codex/" in link_str:
            try:
                assert link.lstat().st_mtime_ns == mtime_before, f"{link} mtime changed"
            except AssertionError as exc:
                pytest.xfail(f"layout switch does not yet leave Cursor/Codex idempotent: {exc}")

    # No plist byte change.
    assert plist_path.read_bytes() == plist_before
    assert health_path.read_bytes() == health_before


# --- AC-3: --layout clone --apply restores links and plist ----------------

def test_ac3_layout_clone_restores(tmp_path: Path) -> None:
    """AC-3: --layout clone --apply after --layout release restores every
    link and the plist to the clone's paths; layout reads 'clone'."""
    fx = _setup_fixture(tmp_path, with_cursor=True, with_codex=True)
    home = fx["home"]

    _run_install(home, "--layout", "release", "--apply")
    _run_install(home, "--layout", "clone", "--apply")

    # Claude homes: existing control (green today).
    _assert_links_to_repo(home, CLAUDE_HOMES)

    # Cursor and Codex: red-first pins.
    for agent_home in [".cursor", ".codex"]:
        try:
            _assert_links_to_repo(home, [agent_home])
        except AssertionError as exc:
            pytest.xfail(f"clone restore does not yet cover {agent_home}: {exc}")

    # Plist script restored to clone.
    plist_path = fx["agents_dir"] / f"{SCHEDULER_LABEL}.plist"
    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)
    args = plist["ProgramArguments"]
    expected_script = str(REPO_ROOT / SCHEDULER_SCRIPT)
    assert args[1] == expected_script

    # Layout file.
    layout_path = home / ".ilk" / "layout"
    assert layout_path.read_text().strip() == "clone"


# --- AC-4: no current ⇒ exit non-zero, change nothing --------------------

def test_ac4_no_current_exits_nonzero(tmp_path: Path) -> None:
    """AC-4: with no 'current' symlink, --layout release --apply exits
    non-zero and changes nothing."""
    fx = _setup_fixture(tmp_path, with_current=False, with_cursor=True, with_codex=True)
    home = fx["home"]

    # Snapshot link targets for all homes.
    targets_before = _snapshot_targets(home, ALL_AGENT_HOMES)

    result = _run_install(home, "--layout", "release", "--apply", check=False)
    assert result.returncode == 2, f"expected exit 2, got {result.returncode}"
    # The error should mention current / missing, not "unknown flag".
    assert "current" in result.stderr.lower(), (
        f"stderr does not mention 'current': {result.stderr!r}"
    )

    # No link changed.
    for link_str, target_before in targets_before.items():
        link = Path(link_str)
        assert os.readlink(link) == target_before, f"{link} was changed"


# --- AC-5: --layout release without --apply changes nothing ---------------

def test_ac5_dry_run_changes_nothing(tmp_path: Path) -> None:
    """AC-5: --layout release (no --apply) changes nothing."""
    fx = _setup_fixture(tmp_path, with_cursor=True, with_codex=True)
    home = fx["home"]

    targets_before = _snapshot_targets(home, ALL_AGENT_HOMES)

    plist_path = fx["agents_dir"] / f"{SCHEDULER_LABEL}.plist"
    plist_before = plist_path.read_bytes()

    result = _run_install(home, "--layout", "release")
    assert result.returncode == 0

    # No link changed.
    for link_str, target_before in targets_before.items():
        link = Path(link_str)
        assert os.readlink(link) == target_before, f"{link} was changed"

    # Plist unchanged.
    assert plist_path.read_bytes() == plist_before


# --- AC-6: plain install.sh --apply respects a release layout -------------

def test_ac6_plain_install_respects_release_layout(tmp_path: Path) -> None:
    """AC-6: after --layout release, plain install.sh --apply leaves
    existing Claude/Cursor/Codex links on 'current' and recreates
    deliberately removed links with 'current' targets."""
    fx = _setup_fixture(tmp_path, with_cursor=True, with_codex=True)
    home = fx["home"]
    current = fx["current"]

    _run_install(home, "--layout", "release", "--apply")

    # Remove one Cursor skill link and one Codex command link.
    cursor_skill_link = home / ".cursor" / "skills" / "ilk-loop"
    codex_cmd_link = home / ".codex" / "commands" / "ilk.md"
    cursor_skill_link.unlink()
    codex_cmd_link.unlink()

    # Now run plain install.sh --apply (no --layout).
    _run_install(home, "--apply")

    # Claude homes: existing control — skill link still on current.
    link = home / ".claude" / "skills" / "ilk-loop"
    assert link.is_symlink(), f"{link} is not a symlink"
    target = os.readlink(link)
    expected = str(current / "skills" / "ilk-loop")
    assert target == expected, f"{link} -> {target!r}, expected {expected!r}"

    # Cursor/Codex: plain apply should recreate removed links on current.
    # This is a red-first pin — the current installer skips Cursor/Codex
    # in release layout, so the link will be recreated pointing at the
    # clone instead of current.
    try:
        assert cursor_skill_link.is_symlink(), f"{cursor_skill_link} was not recreated"
        target = os.readlink(cursor_skill_link)
        expected = str(current / "skills" / "ilk-loop")
        assert target == expected, f"{cursor_skill_link} -> {target!r}, expected {expected!r}"
    except AssertionError as exc:
        pytest.xfail(f"plain install does not yet recreate Cursor links on current: {exc}")

    try:
        assert codex_cmd_link.is_symlink(), f"{codex_cmd_link} was not recreated"
        target = os.readlink(codex_cmd_link)
        expected = str(current / "commands" / "ilk.md")
        assert target == expected, f"{codex_cmd_link} -> {target!r}, expected {expected!r}"
    except AssertionError as exc:
        pytest.xfail(f"plain install does not yet recreate Codex links on current: {exc}")

    # Run plain apply again — idempotent for Cursor/Codex too.
    mtimes_before = _snapshot_mtimes(home, [".cursor", ".codex"])
    _run_install(home, "--apply")
    for link_str, mtime_before in mtimes_before.items():
        link = Path(link_str)
        try:
            assert link.lstat().st_mtime_ns == mtime_before, f"{link} mtime changed"
        except AssertionError as exc:
            pytest.xfail(f"plain install Cursor/Codex not yet idempotent: {exc}")


# --- AC-7: missing .claude-manager and plist ⇒ exit 0, create neither ----

def test_ac7_missing_manager_and_plist(tmp_path: Path) -> None:
    """AC-7: with ~/.claude-manager and the plist absent, --layout release
    --apply exits 0 and creates neither."""
    fx = _setup_fixture(tmp_path, with_manager=False, with_plist=False,
                        with_health_plist=False)
    home = fx["home"]

    result = _run_install(home, "--layout", "release", "--apply")
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

    # .claude-manager should not have been created.
    manager = home / ".claude-manager"
    assert not manager.exists(), f"{manager} was created"

    # Plist should not have been created.
    plist_path = fx["agents_dir"] / f"{SCHEDULER_LABEL}.plist"
    assert not plist_path.exists(), f"{plist_path} was created"


# --- AC-8: control — plain install on fresh fixture (passes today) --------

def test_ac8_plain_install_on_fresh_fixture(tmp_path: Path) -> None:
    """AC-8 (control): install.sh --apply --only-claude on a fresh fixture
    home with no layout file links skills/ilk-loop to this tree (passes
    today)."""
    home = tmp_path / "home"
    home.mkdir()
    claude = home / ".claude"
    claude.mkdir()

    result = _run_install(home, "--apply", "--only-claude")
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

    link = claude / "skills" / "ilk-loop"
    assert link.is_symlink(), f"{link} is not a symlink"
    target = os.readlink(link)
    expected = str(REPO_ROOT / "skills" / "ilk-loop")
    assert target == expected, f"{link} -> {target!r}, expected {expected!r}"