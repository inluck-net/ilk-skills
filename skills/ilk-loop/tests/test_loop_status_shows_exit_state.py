"""AC-1..AC-6: loop_status surfaces the run's exit state.

The defect (MEASURED 2026-09-16, gh-resolve): a run that parked for a
``ship_integrity_violation`` writes its outcome to ``last-exit.json`` but
``loop_status`` never reads it.  A human sees a normal status table and
reads it as ordinary outstanding work.

This test pins the parked-project case and asserts the exit state appears
in the output.

AC-1: loop_status prints the last run's exit state when it is a terminal
      or parked state, resolved via ``ilk_paths.sentinel_path``.
AC-2: the line carries ``run_id`` and ``iterations``.
AC-3: blocking states are prominent; ordinary ones are quiet or omitted.
AC-4: staleness is stated, not implied.
AC-5: a missing, empty, or unparseable sentinel prints nothing.
AC-6: the state appears in ``--json`` under its own key.
"""
from __future__ import annotations

import json
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the scripts dir is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from loop_status import resolve_status, main as loop_status_main  # noqa: E402


# ── sentinel states ──────────────────────────────────────────────────────────

BLOCKING_STATES = {
    "ship_integrity_violation",
    "blocked-no-runnable",
    "budget-exhausted",
    "local_checks_failed",
    "no-progress",
    "interrupted",
}

ORDINARY_STATES = {
    "all-shipped",
    "max-iterations",
    "timeout",
    "already-shipped",
}


# ── fixtures ─────────────────────────────────────────────────────────────────

MASTER_TEXT = textwrap.dedent("""\
    ---
    master_plan: 2026-09-16-test
    batch_date: 2026-09-16
    status: active
    total_tickets: 1
    ---

    ## Sub-plan registry

    | # | Sub-plan | Status |
    |---|---|---|
    | 1 | 2026-09-16-alpha.md | shipped |
""")

SUB_ALPHA = textwrap.dedent("""\
    ---
    plan: alpha
    status: shipped
    current_step: 3
    estimated_steps: 3
    ---

    # Alpha
""")


def _project_key(root: Path) -> str:
    """Derive the same key that ilk_paths.project_key would."""
    from ilk_paths import project_key as _pk
    return _pk(root)


def _setup_project(
    tmp_path: Path,
    *,
    sentinel_data: dict | None = None,
    sentinel_text: str | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Create a minimal project with plans + sentinel.

    When *sentinel_data* is given, writes it as JSON.
    When *sentinel_text* is given, writes it verbatim (for malformed tests).
    When both are None, no sentinel is written (absent case).
    """
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / ".git").mkdir()

    plans_dir = proj / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "MASTER-2026-09-16-test.md").write_text(MASTER_TEXT, encoding="utf-8")
    (plans_dir / "2026-09-16-alpha.md").write_text(SUB_ALPHA, encoding="utf-8")

    # Point ILK_DATA_HOME at a temp dir so external resolution works.
    data_home = tmp_path / "ilk-data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))

    key = _project_key(proj)
    launcher_dir = data_home / "projects" / key / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)

    sentinel = launcher_dir / "last-exit.json"
    if sentinel_data is not None:
        sentinel.write_text(json.dumps(sentinel_data), encoding="utf-8")
    elif sentinel_text is not None:
        sentinel.write_text(sentinel_text, encoding="utf-8")
    # else: absent — don't create the file.

    return proj


# Mock ship_audit so we don't need real step commits in the tmp repo.
@pytest.fixture(autouse=True)
def _mock_ship_audit():
    """Make ship_audit report all shipped sub-plans as proven."""
    import ship_audit

    def fake_audit_ship(*args, **kwargs):
        return {"proven": True, "reasons": []}

    with patch.object(ship_audit, "audit_ship", side_effect=fake_audit_ship):
        yield


# ── the measured fixture ─────────────────────────────────────────────────────

PARKED_SENTINEL = {
    "state": "ship_integrity_violation",
    "pid": 18066,
    "run_id": "20260915-231529",
    "started_at": "2026-09-15T23:15:29+0800",
    "ended_at": "2026-09-16T01:05:43+0800",
    "iterations": 11,
    "project_path": "/Users/chad/Projects/github/keyreply/gh-resolve",
    "cli": "claude",
}


class TestParkedProjectShowsExitState:
    """AC-1: the exit state appears in the status output."""

    def test_blocking_state_in_text_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        proj = _setup_project(tmp_path, sentinel_data=PARKED_SENTINEL, monkeypatch=monkeypatch)
        monkeypatch.chdir(proj)
        monkeypatch.setattr(sys, "argv", ["loop_status.py"])
        loop_status_main()
        out = capsys.readouterr().out
        assert "ship_integrity_violation" in out

    def test_blocking_state_in_json_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proj = _setup_project(tmp_path, sentinel_data=PARKED_SENTINEL, monkeypatch=monkeypatch)
        data = resolve_status(proj)
        assert "exit_state" in data
        assert data["exit_state"]["state"] == "ship_integrity_violation"


class TestExitStateCarriesRunInfo:
    """AC-2: the line carries run_id and iterations."""

    def test_run_id_and_iterations_in_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proj = _setup_project(tmp_path, sentinel_data=PARKED_SENTINEL, monkeypatch=monkeypatch)
        data = resolve_status(proj)
        es = data["exit_state"]
        assert es["run_id"] == "20260915-231529"
        assert es["iterations"] == 11

    def test_run_id_and_iterations_in_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        proj = _setup_project(tmp_path, sentinel_data=PARKED_SENTINEL, monkeypatch=monkeypatch)
        monkeypatch.chdir(proj)
        monkeypatch.setattr(sys, "argv", ["loop_status.py"])
        loop_status_main()
        out = capsys.readouterr().out
        assert "20260915-231529" in out
        assert "11" in out  # iterations


class TestStateRanking:
    """AC-3: blocking states are prominent; ordinary ones are quiet or omitted."""

    def test_blocking_state_is_prominent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        proj = _setup_project(tmp_path, sentinel_data=PARKED_SENTINEL, monkeypatch=monkeypatch)
        monkeypatch.chdir(proj)
        monkeypatch.setattr(sys, "argv", ["loop_status.py"])
        loop_status_main()
        out = capsys.readouterr().out
        # Blocking state must appear — it is the whole point.
        assert "ship_integrity_violation" in out

    def test_ordinary_state_is_quiet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        sentinel = {**PARKED_SENTINEL, "state": "max-iter-bound"}
        proj = _setup_project(tmp_path, sentinel_data=sentinel, monkeypatch=monkeypatch)
        monkeypatch.chdir(proj)
        monkeypatch.setattr(sys, "argv", ["loop_status.py"])
        loop_status_main()
        out = capsys.readouterr().out
        # Ordinary state may appear quietly or be omitted — but if it
        # appears, it must NOT use a prominent/warning phrasing.
        if "max-iter-bound" in out:
            # Should not be in a warning-style banner.
            assert "PARKED" not in out.upper()


class TestStaleness:
    """AC-4: staleness is stated, not implied."""

    def test_stale_sentinel_is_dated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        old_time = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S%z")
        sentinel = {**PARKED_SENTINEL, "ended_at": old_time}
        proj = _setup_project(tmp_path, sentinel_data=sentinel, monkeypatch=monkeypatch)
        data = resolve_status(proj)
        es = data.get("exit_state", {})
        assert es.get("stale") is True or "stale" in str(es)


class TestResilience:
    """AC-5: missing, empty, or unparseable sentinel prints nothing."""

    def test_absent_sentinel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proj = _setup_project(tmp_path, sentinel_data=None, monkeypatch=monkeypatch)
        data = resolve_status(proj)
        # Must not crash; exit_state should be None or absent.
        es = data.get("exit_state")
        assert es is None

    def test_empty_sentinel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proj = _setup_project(tmp_path, sentinel_text="", monkeypatch=monkeypatch)
        data = resolve_status(proj)
        es = data.get("exit_state")
        assert es is None

    def test_malformed_sentinel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proj = _setup_project(tmp_path, sentinel_text="NOT JSON", monkeypatch=monkeypatch)
        data = resolve_status(proj)
        es = data.get("exit_state")
        assert es is None


class TestJsonPayload:
    """AC-6: the state appears in --json under its own key."""

    def test_json_has_exit_state_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proj = _setup_project(tmp_path, sentinel_data=PARKED_SENTINEL, monkeypatch=monkeypatch)
        data = resolve_status(proj)
        assert "exit_state" in data
        es = data["exit_state"]
        assert isinstance(es, dict)
        assert "state" in es
        assert es["state"] == "ship_integrity_violation"