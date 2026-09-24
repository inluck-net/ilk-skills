"""Red-first pin — a real (non-dry) launch exits 0 and reaches its watchdog.

``launch.sh:777`` reads ``$pid_file``, but ``pid_file`` is assigned only
inside the ``if [[ "$dry_run" == "true" ]]`` branch (``:729-730``).
A real launch hits ``set -euo pipefail`` with ``pid_file`` unset and aborts:
``launch.sh: line 777: pid_file: unbound variable``.

``ilk-run.sh`` calls ``launch.sh`` then ``watchdog.sh``.  Because launch
dies, the watchdog step is never reached.

AC-1 (e2e, the incident): a non-dry launch into a tmp project, with a stub
``start_detached_session``, exits 0.  Its stdout has the ``PID file:``,
``Log file:`` and ``JSONL log:`` lines with non-empty values, and stderr has
no ``unbound variable``.

AC-2: ``ilk-run.sh`` against the same stub reaches its watchdog step (assert
on a stubbed watchdog marker file), instead of exiting after the launch.

AC-3: the dry-run output is unchanged.

**This file is deliberately RED at step 0.**  It goes green at step 1,
when ``pid_file`` is bound on every launch path.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
_SKILLS = _HERE.parent.parent  # skills/
sys.path.insert(0, str(_SKILLS / "ilk-loop" / "scripts"))
_LAUNCH_SH = _SKILLS / "ilk-launcher" / "scripts" / "launch.sh"
_ILK_RUN_SH = _SKILLS / "ilk-runner" / "scripts" / "ilk-run.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_git_project(tmp_path: Path) -> Path:
    """Create a minimal git repo so ilk_paths.py can resolve a project key."""
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=project, check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
    return project


def _run_start_ilk_window(
    tmp_path: Path,
    project: Path,
    *,
    dry_run: bool = False,
    engine: str = "claude",
) -> subprocess.CompletedProcess:
    """Source launch.sh (with ILK_SKIP_MAIN) and call start_ilk_window.

    Stubs ``start_detached_session`` so no real process is spawned.
    """
    dry = "true" if dry_run else "false"
    script = textwrap.dedent(f"""\
        set -euo pipefail
        export ILK_DATA_HOME="{tmp_path / '.ilk-data'}"
        export HOME="{tmp_path}"
        export ILK_SKIP_MAIN=1
        source "{_LAUNCH_SH}"
        unset ILK_SKIP_MAIN
        # Stub: don't actually spawn a process.
        start_detached_session() {{ echo "12345"; }}
        start_ilk_window \\
          "{project}" \\
          "test-project" \\
          5 30 "false" "{dry}" "" "{engine}" "" "false"
    """)
    env = {
        **os.environ,
        "ILK_DATA_HOME": str(tmp_path / ".ilk-data"),
        "HOME": str(tmp_path),
    }
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace", env=env,
    )


# ── AC-1: non-dry launch exits 0 with expected lines ────────────────────────

def test_non_dry_launch_exits_cleanly(tmp_path: Path) -> None:
    """AC-1: a non-dry launch exits 0, stdout has PID/Log/JSONL lines with
    non-empty values, and stderr has no 'unbound variable'."""
    project = _make_git_project(tmp_path)
    result = _run_start_ilk_window(tmp_path, project, dry_run=False)

    assert result.returncode == 0, (
        f"Non-dry launch exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "unbound variable" not in result.stderr, (
        f"Got 'unbound variable' error.\nstderr: {result.stderr}"
    )

    # Check expected output lines exist with non-empty values.
    for label in ["PID file:", "Log file:", "JSONL log:"]:
        assert label in result.stdout, (
            f"Missing '{label}' in stdout.\nstdout: {result.stdout}"
        )
        for line in result.stdout.splitlines():
            if label in line:
                value = line.split(label, 1)[1].strip()
                assert value, f"'{label}' has empty value.\nline: {line}"


# ── AC-2: ilk-run.sh reaches its watchdog step ──────────────────────────────

def test_ilk_run_reaches_watchdog(tmp_path: Path) -> None:
    """AC-2: ilk-run.sh reaches its watchdog step instead of exiting after
    the launch.

    We stub launch.sh (exit 0, print expected lines) and watchdog.sh (write
    a marker), then drive ilk-run.sh with those stubs injected via variable
    overrides.
    """
    project = _make_git_project(tmp_path)

    # Stub launch.sh: exit 0, write expected output lines.
    stub_launch = tmp_path / "stub-launch.sh"
    stub_launch.write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            echo "[test-project] launched. PID 12345."
            echo "[test-project] PID file: /tmp/running.pid"
            echo "[test-project] Log file: /tmp/test.log"
            echo "[test-project] JSONL log: /tmp/test.jsonl"
        """),
        encoding="utf-8",
    )
    os.chmod(stub_launch, 0o755)

    # Stub watchdog.sh: write a marker file instead of actually running.
    watchdog_marker = tmp_path / "watchdog-reached.txt"
    stub_watchdog = tmp_path / "stub-watchdog.sh"
    stub_watchdog.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            echo "watchdog reached" > "{watchdog_marker}"
        """),
        encoding="utf-8",
    )
    os.chmod(stub_watchdog, 0o755)

    env = {
        **os.environ,
        "ILK_DATA_HOME": str(tmp_path / ".ilk-data"),
        "HOME": str(tmp_path),
    }

    # Create a minimal plans dir so loop_status doesn't exit 2.
    import ilk_paths
    with patch.dict(
        os.environ, {"ILK_DATA_HOME": str(tmp_path / ".ilk-data")}, clear=False
    ):
        key = ilk_paths.project_key(project)
    plans = tmp_path / ".ilk-data" / "projects" / key / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-test.md").write_text(
        "---\nmaster_plan: test\nbatch_date: 2026-09-24\nstatus: active\n"
        "supervised_only: false\n---\n\n# MASTER\n\n## Sub-plan registry\n\n"
        "| # | File |\n|---|---|\n| 1 | 2026-09-24-sub-a.md |\n",
        encoding="utf-8",
    )
    (plans / "2026-09-24-sub-a.md").write_text(
        "---\nplan: sub-a\nstatus: pending\ncurrent_step: 0\n"
        "estimated_steps: 2\n---\n\n# sub-a\n",
        encoding="utf-8",
    )

    # Stub preflight.sh: always succeed (must be a bash script, not a binary).
    stub_preflight = tmp_path / "stub-preflight.sh"
    stub_preflight.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(stub_preflight, 0o755)

    # Drive ilk-run.sh with stubs injected via env-var overrides.
    # ilk-run.sh respects LAUNCH_SH, WATCHDOG_SH, PREFLIGHT_SH when set.
    script = textwrap.dedent(f"""\
        export LAUNCH_SH="{stub_launch}"
        export WATCHDOG_SH="{stub_watchdog}"
        export PREFLIGHT_SH="{stub_preflight}"
        source "{_ILK_RUN_SH}"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace", env=env, cwd=str(project),
    )

    assert result.returncode == 0, (
        f"ilk-run.sh exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert watchdog_marker.exists(), (
        "watchdog.sh was not reached by ilk-run.sh.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── AC-3: dry-run output is unchanged ───────────────────────────────────────

def test_dry_run_output_unchanged(tmp_path: Path) -> None:
    """AC-3: the dry-run path still works (no regression from the fix)."""
    project = _make_git_project(tmp_path)
    result = _run_start_ilk_window(tmp_path, project, dry_run=True)

    assert result.returncode == 0, (
        f"Dry-run exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "DRY RUN" in result.stdout, (
        f"Missing 'DRY RUN' in dry-run output.\nstdout: {result.stdout}"
    )
    assert "PID file:" in result.stdout, (
        f"Missing 'PID file:' in dry-run output.\nstdout: {result.stdout}"
    )
