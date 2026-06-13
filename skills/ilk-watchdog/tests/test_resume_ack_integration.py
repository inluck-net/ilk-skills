"""Integration test for the resolve-ack writer -> reader round-trip.

Exercises the path /ilk-resume drives: write the ack (function + `ack` CLI),
then confirm the blacklist decision flips to not-blacklisted. Hermetic.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import blacklist_status as bl  # noqa: E402

BL_PY = SCRIPTS_DIR / "blacklist_status.py"


def _write_recent_blacklist_pm(data_dir: Path) -> str:
    """Write a stuck-no-progress postmortem generated ~5 min ago (within backoff)."""
    gen = (dt.datetime.now() - dt.timedelta(minutes=5)).isoformat(timespec="seconds")
    pm_dir = data_dir / "runtime" / "launcher" / "postmortems"
    pm_dir.mkdir(parents=True, exist_ok=True)
    (pm_dir / "20260613-000000.md").write_text(
        f'---\nclassification: "stuck-no-progress"\ngenerated_at: "{gen}"\n---\n# pm\n',
        encoding="utf-8",
    )
    return gen


def test_ack_function_roundtrip(tmp_path: Path):
    _write_recent_blacklist_pm(tmp_path)
    # Before ack: blacklisted (within the 60-min backoff, no ack)
    assert bl.is_blacklisted(tmp_path)["blacklisted"] is True
    # Write the ack (default cleared_at = now, which is after generated_at)
    bl.write_resume_ack(tmp_path)
    # After ack: cleared
    after = bl.is_blacklisted(tmp_path)
    assert after["blacklisted"] is False
    assert after["reason"] == "resolved-by-ack"


def test_ack_cli_roundtrip(tmp_path: Path):
    _write_recent_blacklist_pm(tmp_path)

    def run(*args):
        return subprocess.run([sys.executable, str(BL_PY), *args],
                              capture_output=True, text=True)

    # check -> blacklisted
    c1 = run("check", "--project", str(tmp_path))
    assert c1.returncode == 0 and json.loads(c1.stdout)["blacklisted"] is True

    # ack (the command /ilk-resume runs)
    a = run("ack", "--project", str(tmp_path))
    assert a.returncode == 0 and json.loads(a.stdout)["acked"] is True
    # the ack sentinel exists and is BOM-free
    ack_file = tmp_path / "runtime" / "launcher" / "blacklist-cleared.json"
    assert ack_file.exists() and not ack_file.read_bytes().startswith(b"\xef\xbb\xbf")

    # check -> now cleared
    c2 = run("check", "--project", str(tmp_path))
    assert c2.returncode == 0 and json.loads(c2.stdout)["blacklisted"] is False
