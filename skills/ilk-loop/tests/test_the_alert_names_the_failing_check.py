"""The alert names the sub-plan and step whose check failed.

AC-1: When a run exits ``local_checks_failed`` or
      ``ship_integrity_violation``, ``last-exit.json`` gains
      ``failed_check: {slug, step, command}`` taken from the last
      failing gate row.
AC-2: ``status_all.py`` passes it through as ``failed_check``.  Absent
      or null when the sentinel has none; older sentinels unchanged.
AC-3: ``render_xbar.py``: when ``failed_check`` is present, the row's
      ilk-ref uses ``failed_check.slug``'s sub-plan file, and the
      submenu shows ``--failed: <slug> step <N> — <command>``.
      Otherwise it keeps today's ``next_subplan_file`` ref.
AC-4: A pre-field payload (no ``failed_check`` key) renders
      byte-identically to today.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tools" / "xbar"))

import status_all  # noqa: E402
import render_xbar  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_sentinel(runtime_dir: Path, payload: dict) -> None:
    """Atomically write a sentinel JSON file."""
    target = runtime_dir / "last-exit.json"
    target.write_text(json.dumps(payload), encoding="utf-8")


def _make_sentinel(*, state: str = "local_checks_failed",
                   failed_check: dict | None = None) -> dict:
    """Build a sentinel dict with optional ``failed_check``."""
    d = {
        "state": state,
        "pid": 12345,
        "run_id": "20260101-120000",
        "started_at": "2026-01-01T12:00:00+0800",
        "ended_at": "2026-01-01T12:30:00+0800",
        "iterations": 3,
        "project_path": "/tmp/test-proj",
        "cli": "claude",
        "jsonl_log": "/tmp/test.jsonl",
    }
    if failed_check is not None:
        d["failed_check"] = failed_check
    return d


def _setup_project(tmp_path: Path, monkeypatch, *,
                   sentinel: dict | None = None,
                   master_text: str | None = None,
                   subplan_text: str | None = None) -> Path:
    """Create a minimal project data dir for status_all.

    Returns the project dir.
    """
    data = tmp_path / "data"
    proj = data / "projects" / "test-proj"
    plans = proj / "plans"
    plans.mkdir(parents=True)
    runtime = proj / "runtime" / "launcher"
    runtime.mkdir(parents=True)

    if sentinel:
        _write_sentinel(runtime, sentinel)

    if master_text is None:
        master_text = (
            "---\n"
            "master_plan: 2026-09-29c-a-ship-needs-its-own-green-gate\n"
            "status: active\n"
            "current_subplan: 2026-09-29c-the-alert-names-the-failing-check\n"
            "---\n"
            "# MASTER\n"
        )
    (plans / "MASTER-2026-09-29c.md").write_text(master_text, encoding="utf-8")

    if subplan_text is None:
        subplan_text = (
            "---\n"
            "plan: the-alert-names-the-failing-check\n"
            "status: in-progress\n"
            "current_step: 1\n"
            "estimated_steps: 3\n"
            "---\n"
            "# Sub-plan\n"
        )
    (plans / "2026-09-29c-the-alert-names-the-failing-check.md").write_text(
        subplan_text, encoding="utf-8"
    )

    monkeypatch.setenv("ILK_DATA_HOME", str(data))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)
    return proj


def _resolve_status(monkeypatch, tmp_path: Path, *,
                    sentinel: dict | None = None,
                    master_text: str | None = None,
                    subplan_text: str | None = None) -> dict:
    """Run ``resolve_project_status`` and return the dict."""
    proj = _setup_project(tmp_path, monkeypatch,
                          sentinel=sentinel,
                          master_text=master_text,
                          subplan_text=subplan_text)
    return status_all.resolve_project_status(proj)


# ---------------------------------------------------------------------------
# AC-2: status_all passes through failed_check
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="failed_check not carried")
def test_status_all_passthrough_with_failed_check(monkeypatch, tmp_path):
    """AC-2: resolve_project_status includes failed_check when present."""
    fc = {"slug": "my-slug", "step": 2,
          "command": "python3 -m pytest tests/ -q"}
    sentinel = _make_sentinel(state="local_checks_failed",
                              failed_check=fc)
    entry = _resolve_status(monkeypatch, tmp_path, sentinel=sentinel)
    assert entry.get("sentinel", {}).get("failed_check") == fc


def test_status_all_passthrough_without_failed_check(monkeypatch, tmp_path):
    """AC-2: absent failed_check stays absent/None (no crash)."""
    sentinel = _make_sentinel(state="local_checks_failed")
    entry = _resolve_status(monkeypatch, tmp_path, sentinel=sentinel)
    # The sentinel dict should still have the core fields.
    s = entry.get("sentinel", {})
    assert s.get("state") == "local_checks_failed"
    assert s.get("pid") == 12345
    # failed_check absent → not in sentinel output.
    assert "failed_check" not in s


def test_status_all_older_sentinel_unchanged(monkeypatch, tmp_path):
    """AC-4: a sentinel without failed_check key renders as before."""
    sentinel = _make_sentinel(state="local_checks_failed")
    entry = _resolve_status(monkeypatch, tmp_path, sentinel=sentinel)
    s = entry.get("sentinel", {})
    assert s.get("state") == "local_checks_failed"
    assert s.get("pid") == 12345
    assert s.get("alive") is False


# ---------------------------------------------------------------------------
# AC-3: render_xbar uses failed_check for ilk-ref
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="failed_check not carried")
def test_xbar_uses_failed_check_slug_for_ref(monkeypatch, tmp_path):
    """AC-3: ilk-ref uses failed_check.slug when present."""
    fc = {"slug": "the-alert-names-the-failing-check", "step": 1,
          "command": "python3 -m pytest ..."}
    sentinel = _make_sentinel(state="local_checks_failed",
                              failed_check=fc)
    proj = _setup_project(tmp_path, monkeypatch, sentinel=sentinel)

    entry = status_all.resolve_project_status(proj)
    entry["sentinel"]["failed_check"] = fc

    output = render_xbar.render_xbar([entry])
    assert "the-alert-names-the-failing-check" in output


@pytest.mark.xfail(strict=True, reason="failed_check not carried")
def test_xbar_submenu_shows_failed_line(monkeypatch, tmp_path):
    """AC-3: submenu shows --failed: <slug> step <N> — <command>."""
    fc = {"slug": "my-slug", "step": 2,
          "command": "python3 -m pytest tests/ -q"}
    sentinel = _make_sentinel(state="local_checks_failed",
                              failed_check=fc)
    proj = _setup_project(tmp_path, monkeypatch, sentinel=sentinel)
    entry = status_all.resolve_project_status(proj)
    entry["sentinel"]["failed_check"] = fc

    output = render_xbar.render_xbar([entry])
    assert "--failed:" in output
    assert "my-slug" in output
    assert "step 2" in output


def test_xbar_without_failed_check_unchanged(monkeypatch, tmp_path):
    """AC-4: no failed_check key → no --failed line in output."""
    sentinel = _make_sentinel(state="local_checks_failed")
    proj = _setup_project(tmp_path, monkeypatch, sentinel=sentinel)
    entry = status_all.resolve_project_status(proj)
    output = render_xbar.render_xbar([entry])
    assert "--failed:" not in output
