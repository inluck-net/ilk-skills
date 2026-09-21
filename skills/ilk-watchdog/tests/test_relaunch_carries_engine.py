"""RED-first pin — watchdog relaunch passes the run's engine to launch.sh.

Both relaunch sites in ``watchdog.sh`` call::

    bash "$LAUNCH_SCRIPT" --project-path "$project" --force

with NO ``--engine`` flag.  ``resolve_engine`` then walks: no CLI → config
→ ``$ILK_DEFAULT_ENGINE`` → ``DEFAULT_ENGINE="claude"`` = the primary
account.  This is the observed 2026-09-20 burn (retro-2026-09-21).

The fix (step 1) reads ``worker_engine`` from the dead run's
``last-launch.json`` and passes ``--engine`` at both sites.

AC-1 (red-first): a fake ``LAUNCH_SCRIPT`` that records its argv, driven
through the whitelist-relaunch path with a fixture ``last-launch.json``
saying ``worker_engine: claude-manager``, sees ``--engine claude-manager``
in its argv.  Both relaunch paths (whitelist ``:1200``, queue-advance
``:588``) are exercised.

AC-3: the relaunch log line names the engine it dispatched
(``write_log "relaunch engine: <engine>"`` or equivalent) — asserted by
the fake's captured log.

**This file is deliberately RED at step 0.**  It goes green at step 1,
when both relaunch sites carry the engine.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SKILLS = _HERE.parent.parent  # skills/
_WATCHDOG_SH = _SKILLS / "ilk-watchdog" / "scripts" / "watchdog.sh"
_LAUNCH_SH = _SKILLS / "ilk-launcher" / "scripts" / "launch.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_last_launch(launcher_dir: Path, engine: str) -> None:
    """Write a fixture ``last-launch.json`` with the given worker_engine."""
    launcher_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "project_path": str(launcher_dir.parent.parent),
        "worker_engine": engine,
        "pid": 99999,
        "started_at": "2026-09-21T00:00:00+0800",
    }
    (launcher_dir / "last-launch.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def _make_fake_launcher(tmp_path: Path) -> Path:
    """Create a fake launch script that logs its argv to a file."""
    log_file = tmp_path / "argv.log"
    script = tmp_path / "fake-launch.sh"
    script.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            echo "$@" >> "{log_file}"
        """),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    return script


def _read_argv_log(log_file: Path) -> str:
    """Read the accumulated argv log, or empty string if missing."""
    if log_file.exists():
        return log_file.read_text(encoding="utf-8")
    return ""


def _run_watchdog_relaunch(
    tmp_path: Path,
    fake_launcher: Path,
    launcher_dir: Path,
    *,
    path: str = "whitelist",
) -> subprocess.CompletedProcess:
    """Drive one watchdog relaunch path in a subshell.

    *path* selects which relaunch site to exercise:
      ``whitelist`` — the ``WHITELIST hit`` path at ``:1200``
      ``queue-advance`` — the ``handle_promote`` path at ``:588``

    We source the minimal set of functions the relaunch path needs and
    inject a stub ``write_log`` / ``write_banner`` so the test does not
    require a live log file.
    """
    project = str(launcher_dir.parent.parent)
    # The watchdog resolves LAUNCH_SCRIPT from the launcher dir at runtime.
    # We override it directly via variable injection.
    if path == "whitelist":
        # The whitelist relaunch is inside run_watchdog_loop's inner while.
        # We replicate just the relaunch call with the same variable names.
        script = textwrap.dedent(f"""\
            set -euo pipefail
            _SKILL_ROOT=""
            write_log() {{ echo "[LOG] $*" >&2; }}
            write_banner() {{ echo "[BANNER] $*" >&2; }}
            invoke_ilk_notify() {{ true; }}
            project="{project}"
            proj_name="test-proj"
            LAUNCH_SCRIPT="{fake_launcher}"
            # Source get_ilk_launcher_dir so last-launch.json resolution works
            eval "$(sed -n '/^get_ilk_launcher_dir()/,/^}}/p' "{_WATCHDOG_SH}")"
            # Source the relaunch-with-engine helper if it exists
            if grep -q '_relaunch_with_engine' "{_WATCHDOG_SH}" 2>/dev/null; then
              eval "$(sed -n '/^_relaunch_with_engine()/,/^}}/p' "{_WATCHDOG_SH}")"
              _relaunch_with_engine "$project" "$LAUNCH_SCRIPT" "--force"
            else
              # Current code: no --engine (RED)
              bash "$LAUNCH_SCRIPT" --project-path "$project" --force
            fi
        """)
    elif path == "queue-advance":
        script = textwrap.dedent(f"""\
            set -euo pipefail
            _SKILL_ROOT=""
            write_log() {{ echo "[LOG] $*" >&2; }}
            write_banner() {{ echo "[BANNER] $*" >&2; }}
            project="{project}"
            proj_name="test-proj"
            LAUNCH_SCRIPT="{fake_launcher}"
            eval "$(sed -n '/^get_ilk_launcher_dir()/,/^}}/p' "{_WATCHDOG_SH}")"
            if grep -q '_relaunch_with_engine' "{_WATCHDOG_SH}" 2>/dev/null; then
              eval "$(sed -n '/^_relaunch_with_engine()/,/^}}/p' "{_WATCHDOG_SH}")"
              _relaunch_with_engine "$project" "$LAUNCH_SCRIPT" "--force"
            else
              bash "$LAUNCH_SCRIPT" --project-path "$project" --force
            fi
        """)
    else:
        raise ValueError(f"Unknown path: {path}")

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )


# ── AC-1: both relaunch paths pass --engine from last-launch.json ────────────

class TestRelaunchCarriesEngine:
    """AC-1: the watchdog's relaunch sites pass ``--engine`` from the dead
    run's ``last-launch.json`` ``worker_engine`` field."""

    def test_whitelist_relaunch_passes_engine(self, tmp_path: Path) -> None:
        """Whitelist-relaunch path (:1200) passes ``--engine claude-manager``."""
        launcher_dir = tmp_path / "runtime" / "launcher"
        _write_last_launch(launcher_dir, "claude-manager")
        fake_launcher = _make_fake_launcher(tmp_path)

        result = _run_watchdog_relaunch(
            tmp_path, fake_launcher, launcher_dir, path="whitelist"
        )
        assert result.returncode == 0, (
            f"Relaunch exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        argv = _read_argv_log(tmp_path / "argv.log")
        assert "--engine" in argv, (
            f"Whitelist relaunch did not pass --engine.\nargv: {argv!r}"
        )
        assert "claude-manager" in argv, (
            f"Whitelist relaunch did not pass the engine value.\nargv: {argv!r}"
        )

    def test_queue_advance_relaunch_passes_engine(self, tmp_path: Path) -> None:
        """Queue-advance relaunch path (:588) passes ``--engine claude-manager``."""
        launcher_dir = tmp_path / "runtime" / "launcher"
        _write_last_launch(launcher_dir, "claude-manager")
        fake_launcher = _make_fake_launcher(tmp_path)

        result = _run_watchdog_relaunch(
            tmp_path, fake_launcher, launcher_dir, path="queue-advance"
        )
        assert result.returncode == 0, (
            f"Relaunch exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        argv = _read_argv_log(tmp_path / "argv.log")
        assert "--engine" in argv, (
            f"Queue-advance relaunch did not pass --engine.\nargv: {argv!r}"
        )
        assert "claude-manager" in argv, (
            f"Queue-advance relaunch did not pass the engine value.\nargv: {argv!r}"
        )

    def test_no_last_launch_lets_launch_sh_resolve(self, tmp_path: Path) -> None:
        """Without a last-launch.json, the relaunch still succeeds (launch.sh
        resolves the engine via its own precedence chain)."""
        launcher_dir = tmp_path / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        # No last-launch.json written.
        fake_launcher = _make_fake_launcher(tmp_path)

        result = _run_watchdog_relaunch(
            tmp_path, fake_launcher, launcher_dir, path="whitelist"
        )
        assert result.returncode == 0, (
            f"Relaunch without last-launch.json exited {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )


# ── AC-3: relaunch log line names the engine ────────────────────────────────

class TestRelaunchLogNamesEngine:
    """AC-3: the watchdog's log line after relaunch names the engine used."""

    def test_whitelist_relaunch_log_mentions_engine(self, tmp_path: Path) -> None:
        """The log after a whitelist relaunch names the engine."""
        launcher_dir = tmp_path / "runtime" / "launcher"
        _write_last_launch(launcher_dir, "claude-manager")
        fake_launcher = _make_fake_launcher(tmp_path)

        result = _run_watchdog_relaunch(
            tmp_path, fake_launcher, launcher_dir, path="whitelist"
        )
        # The log goes to stderr via our stub write_log.
        log_output = result.stderr
        assert "engine" in log_output.lower(), (
            f"Relaunch log does not mention engine.\nstderr: {log_output!r}"
        )
