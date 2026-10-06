"""Pins: an empty PROJECT_PATH resolves no runtime dir, and no ledger write falls
back to cwd or /.

Regression for backlog 17 / retro-2026-10-02 F4: the live ``ship-reverts.jsonl``
held 104 fixture rows (78 ``test-slug``, 26 ``per-step-slug``) because sourcing
the runner empties ``PROJECT_PATH`` (:28), so ``get_ilk_runtime_dir`` resolves
from cwd, and the ``|| true`` write sites build a path from an empty dir.

AC-1  get_ilk_runtime_dir refuses an empty PROJECT_PATH.
AC-2  Dot-sourced, with PROJECT_PATH empty, driving a revert-append site writes
      nothing (no ship-reverts.jsonl anywhere under tmp_path, cwd, or /).
AC-3  The known writer: run test_ship_audit.py as a subprocess with HOME and
      ILK_DATA_HOME under tmp_path.  Afterwards, no ship-reverts.jsonl under
      the REAL data home has an mtime later than the subprocess start.
AC-4  Control: with PROJECT_PATH set, get_ilk_runtime_dir returns the project's
      launcher dir (unchanged behaviour).
"""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"
ILK_PATHS = Path(__file__).resolve().parent.parent / "scripts" / "ilk_paths.py"
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
TEST_SHIP_AUDIT = Path(__file__).resolve().parent / "test_ship_audit.py"


# ── helpers ──────────────────────────────────────────────────────────────────

def _source_runner_and_call(
    func_call: str, env_extra: dict[str, str] | None = None,
    *, project_path: str | None = None,
) -> subprocess.CompletedProcess:
    """Dot-source the driver and execute *func_call* in the same shell.

    *project_path* is set AFTER sourcing (the runner clobbers ``PROJECT_PATH``
    at its line 28).  ``HOME``, ``ILK_DATA_HOME``, and ``PATH`` are always
    included so the sourced functions resolve paths correctly.
    """
    env: dict[str, str] = {
        "ILK_DOTSOURCE_ONLY": "1",
        "PATH": os.environ.get("PATH", ""),
    }
    if env_extra:
        env.update(env_extra)
    pp_line = f"PROJECT_PATH='{project_path}'; " if project_path else ""
    script = (
        f"export ILK_DOTSOURCE_ONLY=1; "
        f"source '{RUNNER}' 2>/dev/null; "
        f"{pp_line}"
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


def _capture_revert_sizes(data_home: Path) -> dict[Path, int]:
    """Snapshot the byte size of every ``ship-reverts.jsonl`` under *data_home*.

    Returns a mapping ``{path: size}`` for files that exist at call time.
    Missing files are omitted (the caller treats absence as size 0).
    """
    sizes: dict[Path, int] = {}
    for sr in data_home.rglob("ship-reverts.jsonl"):
        sizes[sr] = sr.stat().st_size
    return sizes


def _check_subprocess_leak(
    data_home: Path,
    marker_path: Path,
    pre_run_sizes: dict[Path, int],
) -> list[str]:
    """Check whether a subprocess leaked rows attributable to *marker_path*.

    For every ``ship-reverts.jsonl`` under *data_home*, reads only the bytes
    appended after *pre_run_sizes* and inspects each new row for a field
    whose value contains *marker_path*.  Returns a list of human-readable
    violation descriptions (empty ⇒ no attributable leak).

    This helper exists so AC-2 pins can inject a fixture root instead of
    walking the real data home.
    """
    import json

    violations: list[str] = []
    marker_str = str(marker_path)

    for sr in data_home.rglob("ship-reverts.jsonl"):
        prev_size = pre_run_sizes.get(sr, 0)
        current_size = sr.stat().st_size
        if current_size <= prev_size:
            continue

        # Read only the bytes appended after the subprocess started.
        with open(sr, "rb") as f:
            f.seek(prev_size)
            new_bytes = f.read()

        # Parse each new line; skip unparseable (fail-closed, same as
        # Contract 2b invariant 5).
        for line in new_bytes.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            # A row names marker_path if any string value contains it.
            for val in row.values():
                if isinstance(val, str) and marker_str in val:
                    violations.append(
                        f"Attributable row in {sr}: {line}"
                    )
                    break

    return violations


# ── AC-1: get_ilk_runtime_dir refuses an empty PROJECT_PATH ──────────────────

def test_ac1_empty_project_path_refused(tmp_path: Path) -> None:
    """get_ilk_runtime_dir must exit non-zero with nothing on stdout when
    PROJECT_PATH is empty.
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
    assert "PROJECT_PATH is empty" in result.stderr, (
        f"Expected refusal message on stderr, got: {result.stderr!r}"
    )


# ── AC-2: no writes from a revert-append site ───────────────────────────────

def test_ac2_no_writes_when_empty_project_path(tmp_path: Path) -> None:
    """With PROJECT_PATH empty, the runner's ``_runtime_file`` helper fails
    closed, so no ``ship-reverts.jsonl`` path is ever constructed.

    The runner builds: ``$(_runtime_file ship-reverts.jsonl) || true``
    When ``get_ilk_runtime_dir`` refuses empty PROJECT_PATH, ``_runtime_file``
    returns non-zero and the variable stays empty — the write is skipped.
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

    # Verify get_ilk_runtime_dir returns nothing.
    runtime_dir = _get_runtime_dir(env)
    assert runtime_dir == "", (
        f"Expected empty runtime dir, got: {runtime_dir!r}"
    )

    # Verify _runtime_file fails closed (returns non-zero, empty stdout).
    result = _source_runner_and_call(
        "_runtime_file ship-reverts.jsonl",
        env_extra=env,
    )
    assert result.returncode != 0, (
        f"_runtime_file should fail with empty PROJECT_PATH, got exit {result.returncode}"
    )
    assert result.stdout.strip() == "", (
        f"Expected empty stdout from _runtime_file, got: {result.stdout!r}"
    )


# ── AC-3: the known writer (test_ship_audit.py) writes nothing live ──────────

@pytest.mark.timeout(90)  # measured 17.07-17.15 s kills under -n 8 (2026-10-06)
def test_ac3_known_writer_writes_no_live_rows(tmp_path: Path) -> None:
    """Run test_ship_audit.py as a subprocess with HOME and ILK_DATA_HOME
    under tmp_path.  Afterwards, no ship-reverts.jsonl under the REAL data
    home gained a row attributable to this test's subprocess.

    AC-1: the check counts only rows whose value contains tmp_path (the
    subprocess's project / cwd / data paths all live under it).  Concurrent
    writers appending unrelated rows to the real data home do NOT fail this
    test.
    """
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    tmp_data = tmp_path / "data"
    tmp_data.mkdir()

    # Resolve the Python that has pytest installed (same as current process).
    python = sys.executable
    # Changing HOME breaks user site-packages resolution; pass it explicitly.
    import site as _site
    user_site = _site.getusersitepackages()

    # Snapshot sizes of every ship-reverts.jsonl under the REAL data home
    # before the subprocess runs.
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    real_data = Path(os.environ.get("ILK_DATA_HOME", real_home / ".ilk-data"))
    pre_sizes = _capture_revert_sizes(real_data)

    result = subprocess.run(
        [python, "-m", "pytest", str(TEST_SHIP_AUDIT),
         "-q", "--tb=no", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "HOME": str(tmp_home),
            "ILK_DATA_HOME": str(tmp_data),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": user_site,
        },
        cwd=str(tmp_path),
    )

    # The subprocess should succeed (all tests pass).
    assert result.returncode == 0, (
        f"test_ship_audit.py failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}"
    )

    # Check only rows attributable to this test's subprocess.
    violations = _check_subprocess_leak(
        data_home=real_data,
        marker_path=tmp_path,
        pre_run_sizes=pre_sizes,
    )
    assert violations == [], (
        f"Found {len(violations)} attributable live row(s) after subprocess:\n"
        + "\n".join(violations)
    )


# ── AC-2 pins: attributable-only leak detection ─────────────────────────────

def test_ac2_attributable_only_leak_detection(tmp_path: Path) -> None:
    """Pin: the leak check counts only rows naming the marker path.

    AC-2 from sub-plan the-leak-test-counts-only-its-own-writes.
    Appends two rows to a fixture ``ship-reverts.jsonl`` — one unrelated,
    one whose slug contains the marker path — and asserts exactly 1 violation.
    The stub returns ``[]`` (0 violations), so this xfail fires.
    """
    fixture_data = tmp_path / "fixture-data"
    fixture_data.mkdir()
    reverts_dir = fixture_data / "launcher"
    reverts_dir.mkdir()
    reverts_file = reverts_dir / "ship-reverts.jsonl"

    marker = tmp_path / "my-project"

    # Pre-run snapshot: file does not exist yet.
    pre_sizes = _capture_revert_sizes(fixture_data)

    # Row 1: unrelated — should be exonerated.
    _call_append_revert_row(str(reverts_file), slug="unrelated-slug")
    # Row 2: names marker_path — should be detected.
    _call_append_revert_row(str(reverts_file), slug=str(marker))

    violations = _check_subprocess_leak(
        data_home=fixture_data,
        marker_path=marker,
        pre_run_sizes=pre_sizes,
    )
    assert len(violations) == 1, (
        f"Expected exactly 1 attributable leak (the marker row), "
        f"got {len(violations)}: {violations}"
    )


# ── Pin: runner e2e tests carry a load-sized timeout ────────────────────────

def test_runner_e2e_tests_carry_a_load_sized_timeout() -> None:
    """Pin: the seven runner-driving e2e tests must carry @pytest.mark.timeout(90).

    AC-2 from sub-plan runner-e2e-tests-carry-a-load-sized-timeout.
    Reads markers via pytestmark attributes (no subprocess).
    """
    from . import test_verify_without_a_worker
    from . import test_a_worker_cannot_remove_its_own_gate

    # The seven test ids that need timeout markers (AC-1).
    test_ids: list[tuple[object, str]] = [
        (test_verify_without_a_worker, "test_red_step1_gate_falls_through_to_agent"),
        (test_verify_without_a_worker, "test_batch_verification_green_ships_without_worker"),
        (test_verify_without_a_worker, "test_no_batch_verification_final_gate_first_step_is_shipped_by_the_driver"),
        (test_a_worker_cannot_remove_its_own_gate, "test_worker_editing_gate_timeout_is_restored_and_parked"),
        (test_a_worker_cannot_remove_its_own_gate, "test_worker_deleting_gate_is_restored_and_parked"),
        (test_a_worker_cannot_remove_its_own_gate, "test_worker_editing_findings_only_no_violation"),
        (None, "test_ac3_known_writer_writes_no_live_rows"),  # this module
    ]

    missing: list[str] = []
    short_timeout: list[str] = []

    for module, func_name in test_ids:
        func = globals()[func_name] if module is None else getattr(module, func_name)
        timeout_markers = [
            mark for mark in getattr(func, "pytestmark", [])
            if mark.name == "timeout"
        ]
        if not timeout_markers:
            missing.append(func_name)
        elif timeout_markers[0].args[0] < 90:
            short_timeout.append(f"{func_name} (timeout={timeout_markers[0].args[0]})")

    parts: list[str] = []
    if missing:
        parts.append(f"missing timeout marker: {', '.join(missing)}")
    if short_timeout:
        parts.append(f"timeout < 90: {', '.join(short_timeout)}")
    assert not parts, "; ".join(parts)


# ── AC-4 (control): with PROJECT_PATH set, returns the project's launcher dir ─

def test_ac4_project_path_returns_launcher_dir(tmp_path: Path) -> None:
    """With PROJECT_PATH set to a tmp project, get_ilk_runtime_dir returns that
    project's launcher dir, as today.  This is a control — it should pass now
    and after the fix.
    """
    # Create a git repo so ilk_paths.py can resolve a project root.
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)

    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    tmp_data = tmp_path / "data"
    tmp_data.mkdir()

    result = _source_runner_and_call(
        "get_ilk_runtime_dir",
        env_extra={
            "HOME": str(tmp_home),
            "ILK_DATA_HOME": str(tmp_data),
        },
        project_path=str(tmp_path),
    )
    assert result.returncode == 0, (
        f"Expected success (exit 0), got {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert result.stdout.strip() != "", (
        "Expected a launcher dir path on stdout, got empty."
    )