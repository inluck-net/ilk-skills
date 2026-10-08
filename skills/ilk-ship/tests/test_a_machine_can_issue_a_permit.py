"""Tests for the machine-issued release permit feature.

Sub-plan: a-machine-can-issue-a-permit, step 1 (implementation).

Each test builds a throwaway git repo under ``tmp_path``, writes a minimal
``.ilk-launch.json`` with two hosts, pins ``HOME`` and ``ILK_DATA_HOME`` to
``tmp_path``, and monkeypatches ``probe_host`` so no real ssh or pgrep runs.

Acceptance criteria:
  AC-1  --machine writes permits with by:"machine"; owner writes by:"owner"
  AC-2  pinned loop permits; unpinned loop refusal
  AC-3  permit_mode read: absent→owner; "machine"→machine; "auto"→owner+warn
  AC-4  write_permits.py accepts --machine flag
  AC-5  other unit_test_targets pass unchanged (control, verified separately)
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
        # Deep-merge: update nested ship dict rather than overwriting
        for k, v in extra.items():
            if k == "ship" and isinstance(v, dict) and "ship" in config:
                config["ship"].update(v)
            else:
                config[k] = v
    (project / ".ilk-launch.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8",
    )


def _read_permit(permit_dir: Path, host: str) -> dict:
    """Read and return the parsed permit JSON for *host*."""
    return json.loads((permit_dir / f"{host}.json").read_text(encoding="utf-8"))


# ── AC-1: --machine writes permits with by:"machine"; owner writes by:"owner"


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
        assert data.get("mode") == "machine", f"{host}: expected mode='machine'"


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
        assert data.get("mode") == "owner", f"{host}: expected mode='owner'"


# ── AC-2: pinned loop permits; unpinned loop refusal


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
    marker_dir = (tmp_path / ".ilk" / "releases" / "v9" / "skills"
                  / "ilk-loop" / "scripts")
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "RUN_PINS_RELEASE").write_text("", encoding="utf-8")
    pinned_cmd = str(marker_dir / "run_ilk_loop_claude.sh")

    def _pinned_probe(host: str, *, local: bool):
        if host == "local-a":
            return ["99999"]
        return []

    monkeypatch.setattr(wp, "probe_host", _pinned_probe)
    monkeypatch.setattr(wp, "_get_process_command",
                        lambda pid, local, host="": pinned_cmd)

    rc = wp.main(["--project", str(project), "--machine"])
    assert rc == 0, "pinned loop should not block permits"

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"
    assert any(permit_dir.glob("*.json")), "permits should be written"


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
            return ["99999"]
        return []

    monkeypatch.setattr(wp, "probe_host", _unpinned_probe)
    # Return a command that is NOT pinned (no release dir pattern)
    monkeypatch.setattr(wp, "_get_process_command",
                        lambda pid, local, host="": "run_ilk_loop_claude.sh")

    rc = wp.main(["--project", str(project), "--machine"])
    assert rc == 2, "unpinned loop should refuse with exit 2"

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"
    assert not any(permit_dir.glob("*.json")), "no permits on unpinned loop"


# ── AC-3: permit_mode read


def test_permit_mode_absent_defaults_to_owner(tmp_path: Path) -> None:
    """No ship.permit_mode key → mode is "owner"."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)  # no permit_mode key

    wp = _import_write_permits()
    mode = wp.read_permit_mode(project)
    assert mode == "owner"


def test_permit_mode_machine(tmp_path: Path) -> None:
    """ship.permit_mode = "machine" → mode is "machine"."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project, extra={"ship": {"permit_mode": "machine",
                                                   "hosts": ["local-a"]}})

    wp = _import_write_permits()
    mode = wp.read_permit_mode(project)
    assert mode == "machine"


def test_permit_mode_auto_warns_and_defaults_to_owner(
    tmp_path: Path, capsys,
) -> None:
    """ship.permit_mode = "auto" → mode is "owner" + stderr warning."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project, extra={"ship": {"permit_mode": "auto",
                                                   "hosts": ["local-a"]}})

    wp = _import_write_permits()
    mode = wp.read_permit_mode(project)
    assert mode == "owner"
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower() or "permit_mode" in captured.err.lower()


# ── AC-4: write_permits.py accepts --machine flag


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

    rc = wp.main(["--project", str(project), "--machine", "--dry-run"])
    assert rc == 0, "write_permits.py --machine --dry-run should succeed"