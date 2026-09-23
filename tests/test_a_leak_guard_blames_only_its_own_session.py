r"""Red-first pins for the leak guard blaming only its own session.

Sub-plan: a-leak-guard-blames-only-its-own-session (step 0 of 2).

The DATA-ROOT LEAK guard in conftest.py fails a session when an existing
real-shaped key gains files.  But a key with a live ilk loop (another
project's runner) is ANOTHER WRITER, not this session's leak.  The guard
should report it, not fail.

AC-1: live loop pidfile at session start/end → reported, not failed.
AC-2: live at start, dead at end → reported, not failed.
AC-3: no pidfile → FAILS (quiet key keeps its protection).
AC-4: stale pidfile → FAILS.
AC-6: probe unavailable → FAILS.

AC-5 is covered by existing tests (test_entry_with_files_fails,
test_real_shaped_key_fails_even_when_empty, test_strict_env_fails_on_an_empty_tmp_entry).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_data_root_leak_guard import _FakeSession, _load_conftest, _run_guard


# ── helper ──────────────────────────────────────────────────────────────────


def _run_guard_with_pidfile(
    monkeypatch,
    tmp_path: Path,
    *,
    entries: dict[str, int],
    baseline: dict[str, tuple[int, float]] | None = None,
    pid: str,
    prefix: str = "private-var-tmp",
    strict: bool = False,
    session_start: float = 0.0,
) -> _FakeSession:
    """Run the guard with a running.pid file in each entry.

    Extends _run_guard to also create ``runtime/launcher/running.pid``
    in each entry directory.
    """
    mod = _load_conftest()
    projects = tmp_path / "projects"
    projects.mkdir(parents=True)
    for name, n_files in entries.items():
        d = projects / name / "runtime"
        d.mkdir(parents=True)
        for i in range(n_files):
            (d / f"f{i}.json").write_text("{}", encoding="utf-8")
        launcher = d / "launcher"
        launcher.mkdir(parents=True, exist_ok=True)
        (launcher / "running.pid").write_text(pid, encoding="utf-8")

    monkeypatch.setattr(mod, "_data_projects_dir", projects)
    monkeypatch.setattr(mod, "_data_snapshot", dict(baseline or {}))
    monkeypatch.setattr(mod, "_data_session_start", session_start)
    monkeypatch.setattr(mod, "_data_tmp_prefix", prefix)
    monkeypatch.setattr(mod, "_data_probe_error", None)
    monkeypatch.setenv("ILK_DATA_LEAK_STRICT", "1" if strict else "")

    session = _FakeSession()
    mod._enforce_no_data_root_leak(session)
    return session


_KEY = "users-chad-projects-github-inluck-net-gh-resolve-a7b5462"


# ── AC-1: live loop → reported, not failed ──────────────────────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_live_loop_at_start_is_reported_not_failed(
    monkeypatch, tmp_path, capsys,
):
    """AC-1: an existing key gains files, and its running.pid names a live
    process (the test process itself) ⇒ reported, not failed."""
    my_pid = os.getpid()
    s = _run_guard_with_pidfile(
        monkeypatch, tmp_path,
        entries={_KEY: 3},
        baseline={_KEY: (1, 0.0)},
        pid=str(my_pid),
    )
    # After the fix: exitstatus stays 0 (reported, not failed).
    assert s.exitstatus == 0, (
        "a key with a live loop is another writer, not this session's leak; "
        "the guard should report it, not fail the session"
    )
    err = capsys.readouterr().err
    assert "GAINED FILES" in err
    assert f"pid {my_pid}" in err, (
        "the report must name the live pid so a reader can identify the writer"
    )


# ── AC-2: live at start, gone at end → reported ─────────────────────────────


@pytest.mark.xfail(strict=True, reason="red-first")
def test_live_at_start_gone_at_end_is_reported(
    monkeypatch, tmp_path, capsys,
):
    """AC-2: live loop at session start, process exits before guard runs ⇒
    reported, not failed.  The writer was live during the session even if
    it exited before the guard checked."""
    proc = subprocess.Popen(["sleep", "30"])
    try:
        s = _run_guard_with_pidfile(
            monkeypatch, tmp_path,
            entries={_KEY: 3},
            baseline={_KEY: (1, 0.0)},
            pid=str(proc.pid),
        )
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
        raise

    assert s.exitstatus == 0, (
        "a key whose loop was live at session start should be reported, "
        "not failed — the writer existed even if it exited before the guard ran"
    )


# ── AC-3: quiet key keeps protection → FAILS ────────────────────────────────


def test_quiet_key_with_no_pidfile_still_fails(
    monkeypatch, tmp_path, capsys,
):
    """AC-3: same gain, no pidfile ⇒ FAILS.  A quiet key with no live loop
    is a test that wrote into a real project, which is the hazard."""
    s = _run_guard(
        monkeypatch, tmp_path,
        entries={_KEY: 3},
        baseline={_KEY: (1, 0.0)},
    )
    assert s.exitstatus == 1, (
        "a real key that gained files with no live loop must still fail — "
        "that is the accidentally-forged ship-proof row"
    )


# ── AC-4: stale pidfile → FAILS ─────────────────────────────────────────────


def test_stale_pidfile_fails(monkeypatch, tmp_path):
    """AC-4: pidfile naming a dead pid ⇒ FAILS.  A stale pidfile is not
    evidence of a live writer — the pid may have been recycled."""
    s = _run_guard_with_pidfile(
        monkeypatch, tmp_path,
        entries={_KEY: 3},
        baseline={_KEY: (1, 0.0)},
        pid="99999999",  # almost certainly dead
    )
    assert s.exitstatus == 1, (
        "a stale pidfile (dead pid) must not exempt the key — the pid "
        "may have been recycled to an unrelated process"
    )


# ── AC-6: probe unavailable → FAILS ─────────────────────────────────────────


def test_probe_unavailable_fails(monkeypatch, tmp_path):
    """AC-6: pid_health import fails ⇒ falls back to failing.  An
    unanswerable question never becomes a pass."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _fake_import(name, *args, **kwargs):
        if name == "pid_health":
            raise ImportError("pid_health unavailable for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    s = _run_guard(
        monkeypatch, tmp_path,
        entries={_KEY: 3},
        baseline={_KEY: (1, 0.0)},
    )
    assert s.exitstatus == 1, (
        "when the pid probe cannot run, the guard must fail — an "
        "unanswerable question never becomes a pass"
    )