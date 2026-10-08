"""Tests for the machine-issued release permit feature.

Sub-plan: a-machine-can-issue-a-permit, step 0 (red-first pins).

Each test builds a throwaway git repo under ``tmp_path``, writes a minimal
``.ilk-launch.json`` with two hosts, pins ``HOME`` and ``ILK_DATA_HOME`` to
``tmp_path``, and monkeypatches ``probe_host`` so no real ssh or pgrep runs.

The feature does not exist yet; every test is marked
``xfail(strict=True)`` until step 1 ships it.

Acceptance criteria:
  AC-1  --machine writes permits with by:"machine"; owner writes by:"owner"
  AC-2  pinned loop permits; unpinned loop refusal
  AC-3  permit_mode read: absent→owner; "machine"→machine; "auto"→owner+warn
  AC-4  scheduler.sh wiring: maybe_start_release_train calls write_permits --machine
  AC-5  other unit_test_targets pass unchanged (control, no xfail needed)
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_real_data_home

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"
WATCHDOG_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-watchdog" / "scripts"


def _import_write_permits():
    """Import ``write_permits`` with the correct sys.path."""
    for p in (str(SHIP_SCRIPTS), str(LOOP_SCRIPTS), str(WATCHDOG_SCRIPTS)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return importlib.import_module("write_permits")


def _make_git_repo(project: Path) -> str:
    """Create a minimal git repo with one commit and tag ``v0.0.1``.

    Returns the short SHA of the initial commit.
    """
    subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=project,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project,
                   check=True, capture_output=True)
    (project / "README").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(["git", "tag", "v0.0.1"], cwd=project, check=True,
                   capture_output=True)
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=project,
                       check=True, capture_output=True, text=True)
    return r.stdout.strip()


def _setup_launch_config(project: Path, *, extra: dict | None = None) -> None:
    """Write a minimal ``.ilk-launch.json`` with two test hosts."""
    config = {"ship": {"hosts": ["local-a", "remote-b"]}}
    if extra:
        config.update(extra)
    (project / ".ilk-launch.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8",
    )


def _read_permit(permit_dir: Path, host: str) -> dict:
    """Read and return the parsed permit JSON for *host*."""
    return json.loads((permit_dir / f"{host}.json").read_text(encoding="utf-8"))


# ── AC-1: --machine writes permits with by:"machine"; owner writes by:"owner"


@pytest.mark.xfail(strict=True, reason="no machine permit yet")
def test_machine_flag_writes_by_machine(tmp_path: Path, monkeypatch) -> None:
    """--machine with quiet fleet → permits carry ``by: "machine"``."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()
    monkeypatch.setattr(wp, "probe_host", lambda host, local: [])

    rc = wp.main(["--project", str(project), "--machine"])
    assert rc == 0

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"

    for host in ("local-a", "remote-b"):
        data = _read_permit(permit_dir, host)
        assert data.get("by") == "machine", f"{host}: expected by='machine'"


@pytest.mark.xfail(strict=True, reason="no machine permit yet")
def test_owner_run_writes_by_owner(tmp_path: Path, monkeypatch) -> None:
    """Without --machine, permits carry ``by: "owner"``."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()
    monkeypatch.setattr(wp, "probe_host", lambda host, local: [])

    rc = wp.main(["--project", str(project)])
    assert rc == 0

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"

    for host in ("local-a", "remote-b"):
        data = _read_permit(permit_dir, host)
        assert data.get("by") == "owner", f"{host}: expected by='owner'"


# ── AC-2: pinned loop permits; unpinned loop refusal


@pytest.mark.xfail(strict=True, reason="no machine permit yet")
def test_machine_pinned_loop_permits(tmp_path: Path, monkeypatch) -> None:
    """A PINNED live loop → --machine still writes permits."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()

    # Simulate a pinned loop: command under ~/.ilk/releases/v9/ with marker
    pinned_cmd = (
        str(tmp_path / ".ilk" / "releases" / "v9" / "skills" / "ilk-loop"
            / "scripts" / "run_ilk_loop_claude.sh")
    )
    # Create the marker file so is_pinned returns True
    marker_dir = (tmp_path / ".ilk" / "releases" / "v9" / "skills"
                  / "ilk-loop" / "scripts")
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "RUN_PINS_RELEASE").write_text("", encoding="utf-8")

    def _pinned_probe(host: str, *, local: bool):
        if host == "local-a":
            return ["99999"]  # pid with pinned command
        return []

    monkeypatch.setattr(wp, "probe_host", _pinned_probe)

    # Monkeypatch ps output to return the pinned command for pid 99999
    # The quiet-fleet check uses probe_host, which returns pids.
    # pinned_loops.unpinned filters those pids.  We need to make the
    # probe return the pid AND make pinned_loops see the command.
    # For the test, we monkeypatch pinned_loops.unpinned directly.
    from pinned_loops import unpinned as real_unpinned

    def _mock_unpinned(entries, exists=os.path.exists):
        # All entries are pinned → empty list → fleet quiet
        return []

    monkeypatch.setattr(wp, "pinned_loops_unpinned", _mock_unpinned)

    rc = wp.main(["--project", str(project), "--machine"])
    assert rc == 0, "pinned loop should not block permits"

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"
    assert any(permit_dir.glob("*.json")), "permits should be written"


@pytest.mark.xfail(strict=True, reason="no machine permit yet")
def test_machine_unpinned_loop_refuses(tmp_path: Path, monkeypatch) -> None:
    """An UNPINNED live loop → --machine exits 2, no permits."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()

    def _unpinned_probe(host: str, *, local: bool):
        if host == "local-a":
            return ["99999"]  # pid with unpinned command
        return []

    monkeypatch.setattr(wp, "probe_host", _unpinned_probe)

    # Monkeypatch pinned_loops.unpinned to return the pid (unpinned)
    def _mock_unpinned(entries, exists=os.path.exists):
        return [pid for pid, _ in entries]

    monkeypatch.setattr(wp, "pinned_loops_unpinned", _mock_unpinned)

    rc = wp.main(["--project", str(project), "--machine"])
    assert rc == 2, "unpinned loop should refuse with exit 2"

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"
    assert not any(permit_dir.glob("*.json")), "no permits on unpinned loop"


# ── AC-3: permit_mode read


@pytest.mark.xfail(strict=True, reason="no machine permit yet")
def test_permit_mode_absent_defaults_to_owner(tmp_path: Path, monkeypatch) -> None:
    """No ship.permit_mode key → mode is "owner"."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)  # no permit_mode key

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()
    mode = wp.read_permit_mode(project)
    assert mode == "owner"


@pytest.mark.xfail(strict=True, reason="no machine permit yet")
def test_permit_mode_machine(tmp_path: Path, monkeypatch) -> None:
    """ship.permit_mode = "machine" → mode is "machine"."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project, extra={"ship": {"permit_mode": "machine",
                                                   "hosts": ["local-a"]}})

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()
    mode = wp.read_permit_mode(project)
    assert mode == "machine"


@pytest.mark.xfail(strict=True, reason="no machine permit yet")
def test_permit_mode_auto_warns_and_defaults_to_owner(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    """ship.permit_mode = "auto" → mode is "owner" + stderr warning."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project, extra={"ship": {"permit_mode": "auto",
                                                   "hosts": ["local-a"]}})

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()
    mode = wp.read_permit_mode(project)
    assert mode == "owner"
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower() or "permit_mode" in captured.err.lower()


# ── AC-4 (wiring): write_permits.py accepts --machine for the scheduler to call


@pytest.mark.xfail(strict=True, reason="no machine permit yet")
def test_write_permits_accepts_machine_flag(
    tmp_path: Path, monkeypatch,
) -> None:
    """write_permits.py --project <p> --machine parses without error.

    This is the prerequisite for scheduler.sh's maybe_start_release_train
    to call write_permits.py --machine.  If --machine is not a valid flag,
    the wiring cannot work.
    """
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()
    monkeypatch.setattr(wp, "probe_host", lambda host, local: [])

    # --machine must be accepted by the arg parser (not cause SystemExit)
    rc = wp.main(["--project", str(project), "--machine", "--dry-run"])
    assert rc == 0, "write_permits.py --machine --dry-run should succeed"