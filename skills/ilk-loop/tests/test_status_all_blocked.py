"""RED test — status_all --json blocked-surfacing fields.

Builds a synthetic ILK_DATA_HOME with three projects:
  (a) plain-idle  — non-supervised active master, pending sub-plans, no postmortem → NOT blocked
  (b) blacklisted — blacklist-class postmortem within backoff window        → blocked
  (c) stale-running — sentinel state=running + dead PID                    → blocked

Then invokes ``status_all --json`` and asserts AC-1..AC-3, AC-5.
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
STATUS_ALL = SCRIPTS_DIR / "status_all.py"


# ── fixture builders ────────────────────────────────────────────────────────

def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _master_md(status: str = "active", supervised: bool = False) -> str:
    sup = "\nsupervised_only: true" if supervised else ""
    return (
        f"---\n"
        f"master_plan: test-batch\n"
        f"batch_date: 2026-06-17\n"
        f"status: {status}\n"
        f"created: 2026-06-17T10:00:00+08:00\n"
        f"{sup}\n"
        f"---\n\n"
        f"# MASTER plan\n\n"
        f"## Sub-plan registry\n\n"
        f"| # | Slug | Steps | Status |\n"
        f"|---|---|---|---|\n"
        f"| 1 | [2026-06-17-sp-a.md](./2026-06-17-sp-a.md) | 2 | pending |\n"
    )


def _subplan_md(slug: str = "sp-a", status: str = "pending") -> str:
    return (
        f"---\n"
        f"plan: {slug}\n"
        f"status: {status}\n"
        f"current_step: 0\n"
        f"estimated_steps: 2\n"
        f"last_updated: 2026-06-17\n"
        f"depends_on: []\n"
        f"---\n\n"
        f"# Sub-plan: {slug}\n"
    )


def _sentinel_json(pid: int, state: str = "running") -> str:
    return json.dumps({"pid": pid, "state": state})


def _postmortem_md(classification: str, generated_at: str) -> str:
    return (
        f"---\n"
        f"classification: {classification}\n"
        f"generated_at: {generated_at}\n"
        f"---\n\n"
        f"# Postmortem\n\n"
        f"Classification: {classification}\n"
    )


def _dead_pid() -> int:
    """Return a PID that is almost certainly not alive."""
    return 99999


def _build_project(
    base: Path,
    key: str,
    *,
    master_status: str = "active",
    supervised: bool = False,
    subplan_status: str = "pending",
    sentinel_pid: int | None = None,
    sentinel_state: str = "running",
    postmortem_class: str | None = None,
    postmortem_time: str | None = None,
) -> None:
    """Create a synthetic project under base/projects/<key>/."""
    proj = base / "projects" / key
    plans = proj / "plans"
    runtime = proj / "runtime"
    launcher = runtime / "launcher"

    # Plans
    _write_file(plans / "MASTER-2026-06-17-batch.md", _master_md(master_status, supervised))
    _write_file(plans / "2026-06-17-sp-a.md", _subplan_md("sp-a", subplan_status))

    # Sentinel (optional)
    if sentinel_pid is not None:
        (runtime / "launcher").mkdir(parents=True, exist_ok=True)
        _write_file(runtime / "launcher" / "last-exit.json", _sentinel_json(sentinel_pid, sentinel_state))

    # Postmortem (optional)
    if postmortem_class and postmortem_time:
        _write_file(
            launcher / "postmortems" / "run-001.md",
            _postmortem_md(postmortem_class, postmortem_time),
        )


def _build_fixture(tmp_path: Path) -> None:
    """Build the full synthetic ILK_DATA_HOME."""
    now = dt.datetime.now()
    recent = (now - dt.timedelta(minutes=10)).isoformat(timespec="seconds")

    # (a) plain-idle — active master, pending sub-plans, no postmortem → NOT blocked
    _build_project(tmp_path, "proj-idle", subplan_status="pending")

    # (b) blacklisted — blacklist-class postmortem within backoff → blocked
    _build_project(
        tmp_path,
        "proj-blacklisted",
        subplan_status="pending",
        postmortem_class="local-checks-stuck",
        postmortem_time=recent,
    )

    # (c) stale-running — sentinel state=running + dead PID → blocked
    _build_project(
        tmp_path,
        "proj-stale",
        subplan_status="pending",
        sentinel_pid=_dead_pid(),
        sentinel_state="running",
    )


def _run_status_all(tmp_path: Path) -> list[dict]:
    """Run status_all --json with ILK_DATA_HOME=tmp_path, return parsed JSON."""
    env = os.environ.copy()
    env["ILK_DATA_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(STATUS_ALL), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"status_all failed: {result.stderr}"
    return json.loads(result.stdout)


# ── tests ───────────────────────────────────────────────────────────────────

class TestStatusAllBlocked:
    """AC-1..AC-3, AC-5: status_all --json blocked-surfacing fields."""

    def test_blacklisted_project_is_blocked(self, tmp_path):
        """AC-1: blacklist-class postmortem within backoff → blocked=true,
        correct classification, blocked_reason=within-backoff, non-null
        blocked_expiry and report_path."""
        _build_fixture(tmp_path)
        entries = _run_status_all(tmp_path)

        by_key = {e["project_key"]: e for e in entries}
        proj = by_key.get("proj-blacklisted")
        assert proj is not None, "proj-blacklisted missing from status_all output"

        # These fields don't exist yet → RED
        assert proj["blocked"] is True
        assert proj["classification"] == "local-checks-stuck"
        assert proj["blocked_reason"] == "within-backoff"
        assert proj["blocked_expiry"] is not None
        assert proj["report_path"] is not None

    def test_plain_idle_is_not_blocked(self, tmp_path):
        """AC-2: plain-idle project (no postmortem) → blocked=false,
        classification=null. Also verifies the project appears in output
        (it has a non-supervised active master with pending sub-plans)."""
        _build_fixture(tmp_path)
        entries = _run_status_all(tmp_path)

        by_key = {e["project_key"]: e for e in entries}
        proj = by_key.get("proj-idle")
        assert proj is not None, "proj-idle missing from status_all output"

        assert proj["blocked"] is False
        assert proj["classification"] is None

    def test_stale_running_is_blocked(self, tmp_path):
        """AC-3: sentinel state=running + dead PID → blocked=true,
        blocked_reason=stale-running."""
        _build_fixture(tmp_path)
        entries = _run_status_all(tmp_path)

        by_key = {e["project_key"]: e for e in entries}
        proj = by_key.get("proj-stale")
        assert proj is not None, "proj-stale missing from status_all output"

        assert proj["blocked"] is True
        assert proj["blocked_reason"] == "stale-running"

    def test_resolve_ack_clears_blocked(self, tmp_path):
        """AC-4: blacklist-class postmortem but ack with cleared_at >= generated_at
        → blocked=false (resolved-by-ack)."""
        now = dt.datetime.now()
        generated = (now - dt.timedelta(minutes=20)).isoformat(timespec="seconds")
        cleared = (now - dt.timedelta(minutes=5)).isoformat(timespec="seconds")

        _build_project(
            tmp_path,
            "proj-acked",
            subplan_status="pending",
            postmortem_class="local-checks-stuck",
            postmortem_time=generated,
        )
        # Write the ack sentinel.
        ack_path = tmp_path / "projects" / "proj-acked" / "runtime" / "launcher" / "blacklist-cleared.json"
        _write_file(ack_path, json.dumps({"cleared_at": cleared}))

        entries = _run_status_all(tmp_path)
        by_key = {e["project_key"]: e for e in entries}
        proj = by_key.get("proj-acked")
        assert proj is not None
        assert proj["blocked"] is False

    def test_expiry_clears_blocked(self, tmp_path):
        """AC-4: blacklist-class postmortem that has expired (now >= expiry)
        → blocked=false."""
        # Postmortem generated 120 minutes ago → backoff (60 min) expired.
        expired_time = (dt.datetime.now() - dt.timedelta(minutes=120)).isoformat(timespec="seconds")

        _build_project(
            tmp_path,
            "proj-expired",
            subplan_status="pending",
            postmortem_class="local-checks-stuck",
            postmortem_time=expired_time,
        )

        entries = _run_status_all(tmp_path)
        by_key = {e["project_key"]: e for e in entries}
        proj = by_key.get("proj-expired")
        assert proj is not None
        assert proj["blocked"] is False

    def test_existing_keys_preserved(self, tmp_path):
        """AC-5: existing --json keys are unchanged (back-compat for render_tray)."""
        _build_fixture(tmp_path)
        entries = _run_status_all(tmp_path)

        # All keys that the current render_tray / dashboard depend on.
        required_keys = {
            "project_key", "path", "active_master", "next_subplan", "step",
            "sentinel", "last_class",
        }
        for entry in entries:
            for key in required_keys:
                assert key in entry, f"Missing key '{key}' in {entry['project_key']}"
            # sentinel must have the expected sub-keys.
            s = entry["sentinel"]
            for sk in ("pid", "state", "alive"):
                assert sk in s, f"Missing sentinel key '{sk}' in {entry['project_key']}"

    def test_json_is_ascii_safe(self, tmp_path):
        """AC-5: JSON output contains no non-ASCII characters (no emoji)."""
        _build_fixture(tmp_path)
        env = os.environ.copy()
        env["ILK_DATA_HOME"] = str(tmp_path)
        result = subprocess.run(
            [sys.executable, str(STATUS_ALL), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            encoding="utf-8", errors="replace",
        )
        raw = result.stdout
        for i, ch in enumerate(raw):
            assert ord(ch) < 128, (
                f"Non-ASCII char at position {i}: {ch!r} (U+{ord(ch):04X})"
            )
