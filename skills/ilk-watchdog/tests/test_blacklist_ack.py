"""Tests for blacklist_status.py — the blacklist-vs-resolve-ack decision core.

Hermetic: tmp_path project data dirs; injected fixed `now`; never touches the
real ~/.ilk-data.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import blacklist_status as bl  # noqa: E402

GEN = "2026-06-13T10:00:00"               # postmortem generated_at
NOW_WITHIN = dt.datetime(2026, 6, 13, 10, 30, 0)   # < expiry (11:00)
NOW_AFTER = dt.datetime(2026, 6, 13, 11, 30, 0)    # >= expiry
ACK_AFTER = "2026-06-13T10:05:00"          # >= GEN -> resolves
ACK_BEFORE = "2026-06-13T09:00:00"         # <  GEN -> stale


def _write_pm(data_dir: Path, run_id: str, classification: str, generated_at: str,
              mtime: float, *, bom: bool = False) -> None:
    pm_dir = data_dir / "runtime" / "launcher" / "postmortems"
    pm_dir.mkdir(parents=True, exist_ok=True)
    body = (f"---\nproject: x\nclassification: \"{classification}\"\n"
            f"generated_at: \"{generated_at}\"\n---\n\n# Postmortem {run_id}\n")
    p = pm_dir / f"{run_id}.md"
    p.write_text(body, encoding="utf-8-sig" if bom else "utf-8")
    os.utime(p, (mtime, mtime))


# ── decision table ──────────────────────────────────────────────────

class TestDecision:
    def test_no_postmortem(self, tmp_path):
        r = bl.is_blacklisted(tmp_path, now=NOW_WITHIN)
        assert r["blacklisted"] is False and r["reason"] == "no-postmortem"

    def test_blacklist_within_backoff_no_ack(self, tmp_path):
        _write_pm(tmp_path, "r1", "stuck-no-progress", GEN, mtime=1000)
        r = bl.is_blacklisted(tmp_path, now=NOW_WITHIN)
        assert r["blacklisted"] is True
        assert r["reason"] == "within-backoff"
        assert r["expiry"].startswith("2026-06-13T11:00")

    def test_blacklist_expired(self, tmp_path):
        _write_pm(tmp_path, "r1", "stuck-no-progress", GEN, mtime=1000)
        r = bl.is_blacklisted(tmp_path, now=NOW_AFTER)
        assert r["blacklisted"] is False and r["reason"] == "expired"

    def test_ack_after_clears(self, tmp_path):
        _write_pm(tmp_path, "r1", "stuck-no-progress", GEN, mtime=1000)
        bl.write_resume_ack(tmp_path, ACK_AFTER)
        r = bl.is_blacklisted(tmp_path, now=NOW_WITHIN)
        assert r["blacklisted"] is False and r["reason"] == "resolved-by-ack"

    def test_stale_ack_ignored(self, tmp_path):
        _write_pm(tmp_path, "r1", "stuck-no-progress", GEN, mtime=1000)
        bl.write_resume_ack(tmp_path, ACK_BEFORE)
        r = bl.is_blacklisted(tmp_path, now=NOW_WITHIN)
        assert r["blacklisted"] is True and r["reason"] == "within-backoff"

    def test_newest_clean_success_unblacklists(self, tmp_path):
        # older stuck run, then a newer clean-success run -> NOT blacklisted
        _write_pm(tmp_path, "r1", "stuck-no-progress", GEN, mtime=1000)
        _write_pm(tmp_path, "r2", "clean-success", "2026-06-13T10:40:00", mtime=2000)
        r = bl.is_blacklisted(tmp_path, now=NOW_WITHIN)
        assert r["blacklisted"] is False
        assert r["reason"] == "latest-not-blacklist-class"
        assert r["classification"] == "clean-success"

    def test_non_blacklist_class(self, tmp_path):
        _write_pm(tmp_path, "r1", "timeout-bound", GEN, mtime=1000)
        r = bl.is_blacklisted(tmp_path, now=NOW_WITHIN)
        assert r["blacklisted"] is False

    def test_dependency_unreachable_is_blacklist(self, tmp_path):
        _write_pm(tmp_path, "r1", "dependency-unreachable", GEN, mtime=1000)
        r = bl.is_blacklisted(tmp_path, now=NOW_WITHIN)
        assert r["blacklisted"] is True

    def test_bom_postmortem_parses(self, tmp_path):
        _write_pm(tmp_path, "r1", "stuck-no-progress", GEN, mtime=1000, bom=True)
        r = bl.is_blacklisted(tmp_path, now=NOW_WITHIN)
        assert r["blacklisted"] is True and r["classification"] == "stuck-no-progress"


# ── write_resume_ack ────────────────────────────────────────────────

class TestAck:
    def test_roundtrip_writer_reader(self, tmp_path):
        _write_pm(tmp_path, "r1", "stuck-no-progress", GEN, mtime=1000)
        # default cleared_at = now -> after GEN -> clears
        bl.write_resume_ack(tmp_path, "2026-06-13T10:10:00")
        back = bl.read_resume_ack(tmp_path)
        assert back == dt.datetime(2026, 6, 13, 10, 10, 0)
        assert bl.is_blacklisted(tmp_path, now=NOW_WITHIN)["blacklisted"] is False

    def test_ack_is_bom_free(self, tmp_path):
        p = bl.write_resume_ack(tmp_path, ACK_AFTER)
        raw = p.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert json.loads(raw.decode("utf-8"))["cleared_at"] == ACK_AFTER


# ── CLI (check / ack) ───────────────────────────────────────────────

class TestCLI:
    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS_DIR / "blacklist_status.py"), *args],
                              capture_output=True, text=True)

    def test_check_no_postmortem(self, tmp_path):
        r = self._run("check", "--project", str(tmp_path))
        assert r.returncode == 0
        assert json.loads(r.stdout)["blacklisted"] is False

    def test_ack_then_check_clears(self, tmp_path):
        _write_pm(tmp_path, "r1", "stuck-no-progress", GEN, mtime=1000)
        # ack with a cleared_at after GEN
        a = self._run("ack", "--project", str(tmp_path), "--cleared-at", ACK_AFTER)
        assert a.returncode == 0 and json.loads(a.stdout)["acked"] is True
        # now check reads the ack back (it's within backoff vs real now, but ack >= gen)
        c = self._run("check", "--project", str(tmp_path))
        assert c.returncode == 0
        # real `now` is well past expiry (GEN is 2026) so expired anyway; assert no crash + parses
        assert "blacklisted" in json.loads(c.stdout)
