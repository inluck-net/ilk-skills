"""Red-first pins: a queued wait needs a live drainer.

Part of sub-plan ``a-ledger-wait-needs-a-live-drainer`` (MASTER-2026-10-07e).

Tests ``suite_ledger.wait_for`` — when ``queued.json`` names our tree but
no live process can drain the queue, the wait should return at once instead
of sleeping until timeout.

  AC-1  queued.json names our tree, running.json names a dead pid on another
        tree → returns None with 0 sleeps; the dead file is renamed
        ``running.json.dead-*``.
  AC-2  queued.json names our tree and running.json names a live pid on
        another tree → it waits (≥1 sleep) until lookup returns an entry.
  AC-3  running.json live on OUR tree → waits (unchanged behaviour).
  AC-4  neither file → returns None at once (unchanged).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import suite_ledger  # noqa: E402

DEAD_PID = 99999999  # guaranteed not to exist


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME to tmp_path; clear ILK_WORKER_SESSION."""
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    original_home = os.environ.get("HOME", "")
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    try:
        suite_ledger._ORIGINAL_HOME = original_home
    except ImportError:
        pass


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ── AC-1: dead drainer on another tree → returns at once ────────────────


def test_ac1_dead_drainer_returns_at_once(tmp_path: Path) -> None:
    """queued.json names our tree, running.json names a dead pid on another
    tree → returns None with 0 sleeps; the dead file is renamed."""
    project = tmp_path / "proj"
    project.mkdir()
    ld = project / "ledger"
    ld.mkdir()

    our_tree = "abc123"
    other_tree = "def456"

    _write_json(ld / "running.json", {"pid": DEAD_PID, "tree": other_tree})
    _write_json(ld / "queued.json", {"tree": our_tree})

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(suite_ledger, "ledger_dir", lambda _p: ld)

    sleep_calls = 0
    original_sleep = suite_ledger.time.sleep

    def counting_sleep(seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(suite_ledger.time, "sleep", counting_sleep)

    inv = "python3 -m pytest -q"

    try:
        result = suite_ledger.wait_for(project, our_tree, inv, timeout_s=30)
    finally:
        monkeypatch.undo()

    assert result is None
    assert sleep_calls == 0

    # The dead file should be renamed.
    dead_files = list(ld.glob("running.json.dead-*"))
    assert len(dead_files) == 1, f"expected 1 dead file, got {dead_files}"
    assert not (ld / "running.json").exists()


# ── AC-2: live drainer on another tree → waits until lookup ─────────────


def test_ac2_live_drainer_on_other_tree_waits(tmp_path: Path) -> None:
    """queued.json names our tree and running.json names a live pid on
    another tree → it waits (≥1 sleep) until lookup returns an entry."""
    project = tmp_path / "proj"
    project.mkdir()
    ld = project / "ledger"
    ld.mkdir()

    our_tree = "abc123"
    other_tree = "def456"

    _write_json(ld / "running.json", {"pid": os.getpid(), "tree": other_tree})
    _write_json(ld / "queued.json", {"tree": our_tree})

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(suite_ledger, "ledger_dir", lambda _p: ld)

    sleep_calls = 0
    original_sleep = suite_ledger.time.sleep

    def counting_sleep(seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(suite_ledger.time, "sleep", counting_sleep)

    # Patch lookup to return an entry on the 3rd call.
    lookup_calls = 0
    original_lookup = suite_ledger.lookup

    def patched_lookup(project: Path, tree: str,
                       invocation: str | None = None) -> dict | None:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls >= 3:
            return {"tree": tree, "status": "pass"}
        return None

    monkeypatch.setattr(suite_ledger, "lookup", patched_lookup)

    inv = "python3 -m pytest -q"

    try:
        result = suite_ledger.wait_for(project, our_tree, inv, timeout_s=30)
    finally:
        monkeypatch.undo()

    assert result is not None
    assert sleep_calls >= 1


# ── AC-3: live drainer on our tree → waits ──────────────────────────────


def test_ac3_live_drainer_on_our_tree_waits(tmp_path: Path) -> None:
    """running.json live on OUR tree → waits (unchanged behaviour)."""
    project = tmp_path / "proj"
    project.mkdir()
    ld = project / "ledger"
    ld.mkdir()

    our_tree = "abc123"

    _write_json(ld / "running.json", {"pid": os.getpid(), "tree": our_tree})

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(suite_ledger, "ledger_dir", lambda _p: ld)

    sleep_calls = 0
    original_sleep = suite_ledger.time.sleep

    def counting_sleep(seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(suite_ledger.time, "sleep", counting_sleep)

    # Patch lookup to return an entry on the 3rd call.
    lookup_calls = 0
    original_lookup = suite_ledger.lookup

    def patched_lookup(project: Path, tree: str,
                       invocation: str | None = None) -> dict | None:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls >= 3:
            return {"tree": tree, "status": "pass"}
        return None

    monkeypatch.setattr(suite_ledger, "lookup", patched_lookup)

    inv = "python3 -m pytest -q"

    try:
        result = suite_ledger.wait_for(project, our_tree, inv, timeout_s=30)
    finally:
        monkeypatch.undo()

    assert result is not None
    assert sleep_calls >= 1


# ── AC-4: neither file → returns None at once ───────────────────────────


def test_ac4_neither_file_returns_at_once(tmp_path: Path) -> None:
    """neither file → returns None at once (unchanged)."""
    project = tmp_path / "proj"
    project.mkdir()
    ld = project / "ledger"
    ld.mkdir()

    our_tree = "abc123"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(suite_ledger, "ledger_dir", lambda _p: ld)

    sleep_calls = 0
    original_sleep = suite_ledger.time.sleep

    def counting_sleep(seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(suite_ledger.time, "sleep", counting_sleep)

    inv = "python3 -m pytest -q"

    try:
        result = suite_ledger.wait_for(project, our_tree, inv, timeout_s=30)
    finally:
        monkeypatch.undo()

    assert result is None
    assert sleep_calls == 0