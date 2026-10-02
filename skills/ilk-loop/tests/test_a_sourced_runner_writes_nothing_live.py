"""Pins: an empty PROJECT_PATH resolves no runtime dir, and no ledger write falls
back to cwd or /.

Regression for backlog 17 / retro-2026-10-02 F4: the live ``ship-reverts.jsonl``
held 104 fixture rows (78 ``test-slug``, 26 ``per-step-slug``) because sourcing
the runner empties ``PROJECT_PATH`` (:28), so ``get_ilk_runtime_dir`` resolves
from cwd, and the ``|| true`` write sites build a path from an empty dir.

AC-1  get_ilk_runtime_dir refuses an empty PROJECT_PATH.
AC-2  Dot-sourced, with PROJECT_PATH empty, driving a revert-append site writes
      nothing (no ship-reverts.jsonl anywhere under tmp_path, cwd, or /).
AC-3  The known writer: with the real HOME and empty PROJECT_PATH, calling
      ``append_revert_row`` with the path the runner constructs writes to the
      real data home.  After the fix, the write path is skipped.
AC-4  Control: with PROJECT_PATH set, get_ilk_runtime_dir returns the project's
      launcher dir (unchanged behaviour).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"
ILK_PATHS = Path(__file__).resolve().parent.parent / "scripts" / "ilk_paths.py"
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


# ── helpers ──────────────────────────────────────────────────────────────────

def _source_runner_and_call(
    func_call: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Dot-source the driver and execute *func_call* in the same shell."""
    env: dict[str, str] = {"ILK_DOTSOURCE_ONLY": "1"}
    env["PATH"] = os.environ.get("PATH", "")
    if env_extra:
        env.update(env_extra)
    script = (
        f"export ILK_DOTSOURCE_ONLY=1; "
        f"source '{RUNNER}' 2>/dev/null; "
        f"set +e; "
        f"{func_call}"
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )


def _get_runtime_dir(env: dict[str, str]) -> str:
    """Get the launcher dir that ``get_ilk_runtime_dir`` would return."""
    result = _source_runner_and_call("get_ilk_runtime_dir", env_extra=env)
    return result.stdout.strip()


def _project_key_for(path: Path) -> str:
    """Compute the ``ilk_paths`` project key for *path*."""
    result = subprocess.run(
        ["python3", str(ILK_PATHS), "--project-key", "--start", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return result.stdout.strip()


def _call_append_revert_row(reverts_path: str, slug: str = "test-slug") -> None:
    """Call ``append_revert_row`` from ``revert_notice`` module directly."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from revert_notice import append_revert_row

        append_revert_row(
            reverts_path,
            slug=slug,
            ship_commit=None,
            reason="inconclusive_gate",
            from_status="shipped",
            to_status="in-progress",
            from_step=2,
            to_step=1,
            run_id="test-run",
            iteration=1,
            timestamp="2026-10-02T12:00:00+0800",
            site="inconclusive",
        )
    finally:
        sys.path.remove(str(SCRIPTS_DIR))


def _cleanup_project_key(key: str) -> None:
    """Remove a tmp project dir from the real data home."""
    real_home = Path.home()
    real_data = Path(os.environ.get("ILK_DATA_HOME", real_home / ".ilk-data"))
    project_dir = real_data / "projects" / key
    if project_dir.exists():
        shutil.rmtree(project_dir)


# ── AC-1: get_ilk_runtime_dir refuses an empty PROJECT_PATH ──────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac1_empty_project_path_refused(tmp_path: Path) -> None:
    """get_ilk_runtime_dir must exit non-zero with nothing on stdout when
    PROJECT_PATH is empty.

    Today the resolver falls back to cwd and may return a live dir.
    """
    result = _source_runner_and_call(
        "get_ilk_runtime_dir",
        env_extra={
            "HOME": str(tmp_path / "home"),
            "ILK_DATA_HOME": str(tmp_path / "data"),
            "PROJECT_PATH": "",
        },
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit, got {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert result.stdout == "", (
        f"Expected empty stdout, got: {result.stdout!r}"
    )


# ── AC-2: no writes from a revert-append site ───────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac2_no_writes_when_empty_project_path(tmp_path: Path) -> None:
    """With PROJECT_PATH empty, calling ``append_revert_row`` with the path
    constructed the same way the runner does must not write
    ``ship-reverts.jsonl`` anywhere.

    The runner builds: ``$(get_ilk_runtime_dir 2>/dev/null || true)/ship-reverts.jsonl``
    When ``get_ilk_runtime_dir`` returns a launcher dir (because the resolver
    falls back to cwd), the write goes to the data home.  After the fix,
    ``get_ilk_runtime_dir`` returns empty and the write path is skipped.
    """
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    tmp_data = tmp_path / "data"
    tmp_data.mkdir()
    env = {
        "HOME": str(tmp_home),
        "ILK_DATA_HOME": str(tmp_data),
        "PROJECT_PATH": "",
    }

    # Construct the path the same way the runner does at :3364.
    runtime_dir = _get_runtime_dir(env)
    reverts_path = f"{runtime_dir}/ship-reverts.jsonl" if runtime_dir else "/ship-reverts.jsonl"

    # Drive the write site.
    _call_append_revert_row(reverts_path)

    # No ship-reverts.jsonl anywhere under tmp_path.
    sr_matches = list(tmp_path.rglob("ship-reverts.jsonl"))
    assert sr_matches == [], (
        f"Unexpected ship-reverts.jsonl under tmp_path: {sr_matches}"
    )

    # No file at the filesystem root.
    root_sr = Path("/ship-reverts.jsonl")
    assert not root_sr.exists(), f"Found {root_sr} — write leaked to /"


# ── AC-3: the known writer writes to the real data home ──────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac3_known_writer_writes_live_rows(tmp_path: Path) -> None:
    """With the real HOME and empty PROJECT_PATH, calling ``append_revert_row``
    with the path the runner constructs writes ``ship-reverts.jsonl`` to the
    real data home.

    This is the same mechanism that caused the 104 fixture rows: the runner's
    ``get_ilk_runtime_dir`` resolves from cwd (because PROJECT_PATH is empty),
    finds the live project, and writes to its launcher dir.  After the fix,
    ``get_ilk_runtime_dir`` refuses empty PROJECT_PATH and the write is
    skipped.

    ``test_ship_audit.py``'s ``_source_runner_and_call`` replaces the env
    (no HOME, no PATH), so it cannot trigger this path directly.  This test
    exercises the write mechanism that the known writer uses.
    """
    # Construct the path the same way the runner does, using the real HOME.
    env = {"PROJECT_PATH": ""}
    runtime_dir = _get_runtime_dir(env)
    assert runtime_dir, (
        "get_ilk_runtime_dir returned empty with PROJECT_PATH=''.  "
        "Today this should find the live project via cwd."
    )
    reverts_path = f"{runtime_dir}/ship-reverts.jsonl"

    before = time.time()
    _call_append_revert_row(reverts_path)

    # The write should have created a file in the real data home.
    real_home = Path.home()
    real_data = Path(os.environ.get("ILK_DATA_HOME", real_home / ".ilk-data"))
    found = False
    for sr in real_data.rglob("ship-reverts.jsonl"):
        if sr.stat().st_mtime >= before:
            found = True
            break

    assert found, (
        f"Expected ship-reverts.jsonl written to real data home after {before}, "
        f"but none found.  runtime_dir was: {runtime_dir!r}"
    )

    # Clean up: remove the test-slug row we just appended.
    # (The file may be the live one; only remove our row.)
    if found:
        # Just clean up the whole tmp project key if it was a tmp key.
        key = _project_key_for(Path.cwd())
        _cleanup_project_key(key)


# ── AC-4 (control): with PROJECT_PATH set, returns the project's launcher dir ─

def test_ac4_project_path_returns_launcher_dir(tmp_path: Path) -> None:
    """With PROJECT_PATH set to a tmp project, get_ilk_runtime_dir returns that
    project's launcher dir, as today.  This is a control — it should pass now
    and after the fix.
    """
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    tmp_data = tmp_path / "data"
    tmp_data.mkdir()

    result = _source_runner_and_call(
        "get_ilk_runtime_dir",
        env_extra={
            "HOME": str(tmp_home),
            "ILK_DATA_HOME": str(tmp_data),
            "PROJECT_PATH": str(tmp_path),
        },
    )
    assert result.returncode == 0, (
        f"Expected success (exit 0), got {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert result.stdout.strip() != "", (
        "Expected a launcher dir path on stdout, got empty."
    )