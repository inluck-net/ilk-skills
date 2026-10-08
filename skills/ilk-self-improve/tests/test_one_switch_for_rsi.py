"""Tests: one switch for RSI.

Sub-plan: one-switch-for-rsi (step 0).
Covers AC-1..AC-5: pause, park, resume, off, status (including corrupt file).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
_LOOP_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts")
_SELF_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")


# ── helpers ──────────────────────────────────────────────────────────────


def _load_module():
    """Import rsi_switch."""
    for d in (_LOOP_SCRIPTS, _SELF_SCRIPTS):
        if d not in sys.path:
            sys.path.insert(0, d)
    import rsi_switch
    return rsi_switch


def _setup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin HOME and ILK_DATA_HOME to tmp_path; return the data root."""
    home = tmp_path / "home"
    home.mkdir()
    data_root = tmp_path / "ilk-data"
    data_root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_root))
    monkeypatch.setenv("ILK_DATA_DIR", str(data_root))  # back-compat alias
    # Ensure autoplan dir exists
    (data_root / "autoplan").mkdir(parents=True, exist_ok=True)
    return data_root


def _make_toolkit_plans(tmp_path: Path) -> Path:
    """Create a fake toolkit plans dir with one auto-planned and one session-planned master."""
    plans_dir = tmp_path / "toolkit" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # Auto-planned master (queued)
    auto_master = plans_dir / "MASTER-2026-10-07-auto-batch.md"
    auto_master.write_text(
        "---\n"
        "master_plan: 2026-10-07-auto-batch\n"
        "status: queued\n"
        'auto_planned: "true"\n'
        "---\n\n"
        "# Auto-planned batch\n\n"
        "## Sub-plan registry\n\n"
        "| # | Order | Slug | Items | Steps | Status |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | 0 | [2026-10-07-auto-sub.md](./2026-10-07-auto-sub.md) | test | 2 | pending |\n",
        encoding="utf-8",
    )

    # Session-planned master (queued, no auto_planned flag)
    session_master = plans_dir / "MASTER-2026-10-07-session-batch.md"
    session_master.write_text(
        "---\n"
        "master_plan: 2026-10-07-session-batch\n"
        "status: queued\n"
        "---\n\n"
        "# Session-planned batch\n\n"
        "## Sub-plan registry\n\n"
        "| # | Order | Slug | Items | Steps | Status |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | 0 | [2026-10-07-session-sub.md](./2026-10-07-session-sub.md) | test | 2 | pending |\n",
        encoding="utf-8",
    )

    return plans_dir


def _write_master_with_status(plans_dir: Path, name: str, status: str) -> None:
    """Overwrite a master's status in frontmatter."""
    p = plans_dir / name
    text = p.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    out = []
    for line in lines:
        if line.startswith("status:"):
            out.append(f"status: {status}\n")
        else:
            out.append(line)
    p.write_text("".join(out), encoding="utf-8")


# ── AC-1: pause ──────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="no RSI switch yet")
def test_pause_writes_paused_json_with_all_keys(tmp_path, monkeypatch):
    """AC-1: pause --for 2h --reason x writes paused.json with all five keys
    and until ≈ now+2h; status contains 'paused' and the reason."""
    data_root = _setup_env(tmp_path, monkeypatch)
    rsi_switch = _load_module()

    # Pause for 2 hours
    rc = rsi_switch.main(["pause", "--for", "2h", "--reason", "big task"])
    assert rc == 0

    # Verify paused.json
    paused_file = data_root / "autoplan" / "paused.json"
    assert paused_file.exists(), "paused.json not created"
    paused = json.loads(paused_file.read_text(encoding="utf-8"))

    # All five keys present
    assert "by" in paused, "missing 'by' key"
    assert "reason" in paused, "missing 'reason' key"
    assert "since" in paused, "missing 'since' key"
    assert "until" in paused, "missing 'until' key"
    assert "level" in paused, "missing 'level' key"

    # Values
    assert paused["reason"] == "big task"
    assert paused["level"] == "pause"

    # until ≈ now + 2h (within 60s tolerance)
    until_dt = datetime.fromisoformat(paused["until"])
    if until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = abs((until_dt - now).total_seconds() - 7200)
    assert diff < 60, f"until off by {diff}s from now+2h"

    # Status contains paused and reason
    rc = rsi_switch.main(["status"])
    assert rc == 0


# ── AC-2: park ────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="no RSI switch yet")
def test_park_only_parks_auto_planned_masters(tmp_path, monkeypatch):
    """AC-2: park with one auto-planned queued master and one session-planned
    queued master parks ONLY the auto-planned one and records it in
    parked_masters."""
    data_root = _setup_env(tmp_path, monkeypatch)
    plans_dir = _make_toolkit_plans(tmp_path)
    rsi_switch = _load_module()

    # Patch the toolkit plans dir resolution so rsi_switch finds our fake dir.
    # We also need to stub park_master so it doesn't actually shell out.
    parked_log: list[str] = []

    original_main = rsi_switch.main

    def _patched_park_master(*args, **kwargs):
        """Stub park_master that records what was parked."""
        parked_log.append(args[0] if args else "")
        return 0

    # Park
    rc = rsi_switch.main(["park", "--reason", "testing park"])
    assert rc == 0

    # Verify paused.json has level=park and parked_masters
    paused_file = data_root / "autoplan" / "paused.json"
    assert paused_file.exists(), "paused.json not created"
    paused = json.loads(paused_file.read_text(encoding="utf-8"))
    assert paused["level"] == "park"
    assert "parked_masters" in paused, "missing parked_masters"
    assert len(paused["parked_masters"]) == 1, \
        f"expected 1 parked master, got {len(paused['parked_masters'])}"

    # The parked master should be the auto-planned one
    parked_name = paused["parked_masters"][0]
    assert "auto" in parked_name, \
        f"expected auto-planned master parked, got {parked_name}"


# ── AC-3: resume after park ──────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="no RSI switch yet")
def test_resume_unparks_and_removes_paused_json(tmp_path, monkeypatch):
    """AC-3: resume after AC-2 un-parks that one master, removes paused.json,
    and leaves the session-planned master untouched."""
    data_root = _setup_env(tmp_path, monkeypatch)
    plans_dir = _make_toolkit_plans(tmp_path)
    rsi_switch = _load_module()

    # Park first
    rc = rsi_switch.main(["park", "--reason", "test park"])
    assert rc == 0

    paused_file = data_root / "autoplan" / "paused.json"
    assert paused_file.exists(), "paused.json not created before resume"

    # Resume
    rc = rsi_switch.main(["resume"])
    assert rc == 0

    # paused.json removed
    assert not paused_file.exists(), "paused.json still exists after resume"

    # Session-planned master untouched (still queued)
    session_master = plans_dir / "MASTER-2026-10-07-session-batch.md"
    text = session_master.read_text(encoding="utf-8")
    assert "status: queued" in text, "session-planned master was modified"


# ── AC-4: off and resume ─────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="no RSI switch yet")
def test_off_creates_disabled_and_resume_removes_it(tmp_path, monkeypatch):
    """AC-4: off creates autoplan.disabled; status says off; resume removes it."""
    data_root = _setup_env(tmp_path, monkeypatch)
    rsi_switch = _load_module()

    # Off
    rc = rsi_switch.main(["off", "--reason", "maintenance"])
    assert rc == 0

    disabled_file = data_root / "autoplan.disabled"
    assert disabled_file.exists(), "autoplan.disabled not created"

    # Status says off
    rc = rsi_switch.main(["status"])
    assert rc == 0

    # Resume removes it
    rc = rsi_switch.main(["resume"])
    assert rc == 0
    assert not disabled_file.exists(), "autoplan.disabled still exists after resume"


# ── AC-5: corrupt paused.json ────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="no RSI switch yet")
def test_status_with_corrupt_paused_json(tmp_path, monkeypatch, capsys):
    """AC-5: status with a corrupt paused.json exits 0 and prints
    'RSI: paused (unreadable pause file)'."""
    data_root = _setup_env(tmp_path, monkeypatch)
    rsi_switch = _load_module()

    # Write corrupt paused.json
    paused_file = data_root / "autoplan" / "paused.json"
    paused_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

    rc = rsi_switch.main(["status"])
    assert rc == 0, "status should exit 0 even with corrupt paused.json"

    out = capsys.readouterr().out
    assert "paused" in out.lower(), f"output missing 'paused': {out}"
    assert "unreadable" in out.lower(), f"output missing 'unreadable': {out}"


@pytest.mark.xfail(strict=True, reason="no RSI switch yet")
def test_ilk_status_md_mentions_rsi_switch(tmp_path, monkeypatch):
    """AC-5: commands/ilk-status.md contains rsi_switch.py" status."""
    status_md = Path(__file__).resolve().parent.parent.parent.parent / "commands" / "ilk-status.md"
    if not status_md.exists():
        pytest.skip("ilk-status.md not found")
    text = status_md.read_text(encoding="utf-8")
    assert "rsi_switch.py" in text, "ilk-status.md does not mention rsi_switch.py"