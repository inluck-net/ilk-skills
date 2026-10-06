"""Tests for the toolkit's release-permit writer.

Sub-plan: a-permit-writer-in-the-toolkit, step 0 (red-first pins).

Each test builds a throwaway git repo under ``tmp_path``, writes a minimal
``.ilk-launch.json`` with two hosts, pins ``HOME`` and ``ILK_DATA_HOME`` to
``tmp_path``, and monkeypatches ``probe_host`` so no real ssh or pgrep runs.

The module under test (``write_permits``) does not exist yet; every test is
marked ``xfail(strict=True)`` until step 1 ships it.

Acceptance criteria:
  AC-1  writes permits, accepted by dispatcher
  AC-2  busy fleet refuses
  AC-3  unreachable host refuses
  AC-4  previous permit kept on re-run
  AC-5  no hard-coded repo path, project key, or host list
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


def _setup_launch_config(project: Path) -> None:
    """Write a minimal ``.ilk-launch.json`` with two test hosts."""
    config = {"ship": {"hosts": ["local-a", "remote-b"]}}
    (project / ".ilk-launch.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8",
    )


# ── AC-1: writes permits, accepted by dispatcher ───────────────────────────


def test_writes_permits_accepted_by_dispatcher(tmp_path: Path, monkeypatch, capsys) -> None:
    """probe quiet → exit 0, two permit files, dispatcher accepts."""
    project = tmp_path / "project"
    project.mkdir()
    head_short = _make_git_repo(project)
    _setup_launch_config(project)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()
    monkeypatch.setattr(wp, "probe_host", lambda host, local: [])

    rc = wp.main(["--project", str(project)])
    assert rc == 0, "exit 0 when fleet is quiet"

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"

    for host in ("local-a", "remote-b"):
        pf = permit_dir / f"{host}.json"
        assert pf.exists(), f"permit file missing for {host}"
        data = json.loads(pf.read_text(encoding="utf-8"))
        assert data["project"] == pk
        assert data["base_tag"] == "v0.0.1"
        assert data["candidate_head"][:len(head_short)] == head_short

    sys.path.insert(0, str(WATCHDOG_SCRIPTS))
    from release_train_dispatch import check_permits_for_dispatch
    result = check_permits_for_dispatch(
        tmp_path / "projects" / pk, project,
    )
    assert result["ok"] is True, f"dispatcher should accept: {result}"


# ── AC-2: busy fleet refuses ───────────────────────────────────────────────


def test_busy_fleet_refuses(tmp_path: Path, monkeypatch, capsys) -> None:
    """remote-b has a live loop → exit 2, REFUSED, no permit files."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()

    def _busy_probe(host: str, *, local: bool):
        return ["12345"] if host == "remote-b" else []

    monkeypatch.setattr(wp, "probe_host", _busy_probe)

    rc = wp.main(["--project", str(project)])
    assert rc == 2, "exit 2 when fleet is busy"
    assert "REFUSED" in capsys.readouterr().out  # noqa: F821 — fixture provides it

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"
    assert not any(permit_dir.glob("*.json")), "no permit files on busy fleet"


# ── AC-3: unreachable host refuses ─────────────────────────────────────────


def test_unreachable_host_refuses(tmp_path: Path, monkeypatch, capsys) -> None:
    """remote-b ssh fails → exit 2, REFUSED, no permit files."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()

    def _unreachable_probe(host: str, *, local: bool):
        return "UNREACHABLE" if host == "remote-b" else []

    monkeypatch.setattr(wp, "probe_host", _unreachable_probe)

    rc = wp.main(["--project", str(project)])
    assert rc == 2, "exit 2 when host is unreachable"
    assert "REFUSED" in capsys.readouterr().out

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"
    assert not any(permit_dir.glob("*.json")), "no permit files on unreachable host"


# ── AC-4: previous permit kept on re-run ────────────────────────────────────


def test_previous_permit_kept_on_rerun(tmp_path: Path, monkeypatch, capsys) -> None:
    """Two runs → .prev-* backup and fresh unconsumed permit."""
    project = tmp_path / "project"
    project.mkdir()
    _make_git_repo(project)
    _setup_launch_config(project)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))

    wp = _import_write_permits()
    monkeypatch.setattr(wp, "probe_host", lambda host, local: [])

    # First run
    rc1 = wp.main(["--project", str(project)])
    assert rc1 == 0

    # Second run
    rc2 = wp.main(["--project", str(project)])
    assert rc2 == 0

    from ilk_paths import project_key
    pk = project_key(project)
    permit_dir = tmp_path / "projects" / pk / "runtime" / "permits"

    for host in ("local-a", "remote-b"):
        backups = list(permit_dir.glob(f"{host}.json.prev-*"))
        assert len(backups) == 1, f"expected one .prev-* for {host}, got {len(backups)}"
        current = permit_dir / f"{host}.json"
        assert current.exists(), f"current permit missing for {host}"
        data = json.loads(current.read_text(encoding="utf-8"))
        assert data["consumed"] is False, "new permit should be unconsumed"


# ── AC-5: no hard-coded repo path, project key, or host list ────────────────


def test_no_hard_coding() -> None:
    """Source contains neither inluck-net, 604d727, nor rezmac."""
    source = (SHIP_SCRIPTS / "write_permits.py").read_text(encoding="utf-8")
    assert "inluck-net" not in source
    assert "604d727" not in source
    assert '"rezmac"' not in source