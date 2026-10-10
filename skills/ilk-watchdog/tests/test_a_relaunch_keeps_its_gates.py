"""RED-first pins — a watchdog relaunch keeps the run's gates decision.

A run is dispatched gates ON when ``launch.sh`` auto-detects local_checks on a
queued sub-plan (``launch.sh:993-999``) and forwards ``--run-local-checks`` to
the runner (``launch.sh:711-712``).  That decision is never persisted: the
``last-launch.json`` dict written at ``launch.sh:769-788`` records
``worker_engine`` but not ``run_local_checks``.

``watchdog.sh``'s ``_relaunch_with_engine`` (``watchdog.sh:635-659``) replays
only ``worker_engine`` from that file and calls::

    bash "$launch_script" --project-path "$project" $engine_flag "${extra_args[@]}"

with no gates flag, so the respawn falls back to ``launch.sh``'s auto-detect.
A relaunch can therefore come out gates OFF for the very batch that was
dispatched gates ON — the gates decision is recomputed instead of replayed,
and the recomputation is not the one that dispatched the dead run.

The fix (step 1):

* ``launch.sh`` writes ``"run_local_checks": true|false`` to
  ``last-launch.json`` — the FINAL decision after auto-detect, exactly the
  value that decides ``launch.sh:711-712``.
* ``_relaunch_with_engine`` reads ``run_local_checks`` the way it reads
  ``worker_engine`` and passes ``--run-local-checks`` when true,
  ``--no-local-checks`` when false.  Key absent (a launch from an older
  release) -> pass nothing, which is today's auto-detect.

AC-1: a stub ``launch.sh`` that records its argv; a ``last-launch.json`` with
``"run_local_checks": true``; ``_detect_local_checks`` stubbed to report false
(so nothing in the relaunch path may lean on re-detection).  The watchdog
relaunch passes ``--run-local-checks``.

AC-2: ``"run_local_checks": false`` -> ``--no-local-checks``; key absent ->
neither flag.

AC-3: ``launch.sh`` writes the key — ``true`` when a queued sub-plan declares
``local_checks`` (the auto-detect default), ``false`` with ``--no-local-checks``.

**This file is deliberately RED at step 0.**  It goes green at step 1, when
the decision is persisted and replayed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SKILLS = _HERE.parent.parent  # skills/
_WATCHDOG_SH = _SKILLS / "ilk-watchdog" / "scripts" / "watchdog.sh"
_LAUNCH_SH = _SKILLS / "ilk-launcher" / "scripts" / "launch.sh"


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_git_project(tmp_path: Path) -> Path:
    """Create a minimal git repo so ilk_paths.py can resolve a project key."""
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "-q"], cwd=project, check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=project, check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return project


def _write_last_launch(
    launcher_dir: Path,
    *,
    engine: str = "claude-manager",
    run_local_checks: object = ...,
) -> None:
    """Write a fixture ``last-launch.json``.

    ``run_local_checks`` of ``...`` (Ellipsis) means the key is left out —
    the shape a launch from an older release produces.
    """
    launcher_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "project_path": str(launcher_dir.parent.parent),
        "worker_engine": engine,
        "pid": 99999,
        "started_at": "2026-10-10T00:00:00+0800",
    }
    if run_local_checks is not ...:
        meta["run_local_checks"] = run_local_checks
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
    if log_file.exists():
        return log_file.read_text(encoding="utf-8")
    return ""


def _run_relaunch(
    fake_launcher: Path,
    launcher_dir: Path,
) -> subprocess.CompletedProcess:
    """Drive the real ``_relaunch_with_engine`` from watchdog.sh.

    Stubs ``get_ilk_launcher_dir`` to the fixture dir, ``write_log`` /
    ``write_banner`` so no live log file is required, and — critically —
    ``queued_subplans_declare_local_checks`` to report FALSE.  The relaunch
    must replay the recorded decision; it may not fall back to re-detecting
    what the queue declares right now.
    """
    project = str(launcher_dir.parent.parent)
    launcher_dir_str = str(launcher_dir)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        _SKILL_ROOT=""
        PYTHON="$(command -v python3)"
        write_log() {{ echo "[LOG] $*" >&2; }}
        write_banner() {{ echo "[BANNER] $*" >&2; }}
        invoke_ilk_notify() {{ true; }}
        project="{project}"
        proj_name="test-proj"
        LAUNCH_SCRIPT="{fake_launcher}"
        get_ilk_launcher_dir() {{ echo "{launcher_dir_str}"; }}
        # AC-1's premise: auto-detect would say OFF.  Anything relying on it
        # loses the recorded gates decision.
        queued_subplans_declare_local_checks() {{ return 1; }}
        eval "$(sed -n '/^_relaunch_with_engine()/,/^}}/p' "{_WATCHDOG_SH}")"
        _relaunch_with_engine "$project" "$LAUNCH_SCRIPT" "--force"
    """)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )


def _external_plans_dir(project: Path, data_home: Path) -> Path:
    """Resolve the external plans dir the way the launcher does."""
    sys.path.insert(0, str(_SKILLS / "ilk-loop" / "scripts"))
    from ilk_paths import ilk_data_root, project_key  # noqa: PLC0415

    os.environ["ILK_DATA_HOME"] = str(data_home)
    os.environ["ILK_DATA_DIR"] = str(data_home)
    plans = ilk_data_root() / "projects" / project_key(project) / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    return plans


def _write_queued_plan_with_local_checks(plans_dir: Path) -> None:
    """A master with one non-shipped sub-plan that declares per-step gates."""
    (plans_dir / "MASTER-2026-10-10-execution-plan.md").write_text(
        textwrap.dedent("""\
            ---
            master_plan: 2026-10-10-execution
            batch_date: 2026-10-10
            total_tickets: 1
            status: active
            current_subplan: 2026-10-10-probe
            ---
            # Master

            | # | Order | Slug | Status |
            |---|---|---|---|
            | 1 | 0 | [2026-10-10-probe.md](./2026-10-10-probe.md) | pending |
        """),
        encoding="utf-8",
    )
    (plans_dir / "2026-10-10-probe.md").write_text(
        textwrap.dedent("""\
            ---
            plan: probe
            status: pending
            current_step: 0
            estimated_steps: 2
            local_checks: []
            ---
            # Sub-plan: probe

            ### Step 0 — Do the thing
            ```yaml
            local_checks:
              - command: bash some/test.sh
                timeout: 60
            ```
            - Do the thing.
        """),
        encoding="utf-8",
    )


def _run_launch_main(
    tmp_path: Path,
    project: Path,
    extra_args: list[str],
) -> subprocess.CompletedProcess:
    """Source launch.sh (ILK_SKIP_MAIN) and drive its real ``main()``.

    ``start_detached_session`` is stubbed so no real process is spawned;
    everything else — arg parsing, auto-detect, the last-launch.json write —
    is the real code path.
    """
    data_home = tmp_path / ".ilk-data"
    argv = " ".join(extra_args)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        export ILK_DATA_HOME="{data_home}"
        export ILK_DATA_DIR="{data_home}"
        export HOME="{tmp_path}"
        export ILK_SKIP_MAIN=1
        source "{_LAUNCH_SH}"
        unset ILK_SKIP_MAIN
        start_detached_session() {{ echo "4242"; }}
        main --project-path "{project}" --force --engine claude \
--max-iterations 2 --iteration-timeout-min 5 {argv}
    """)
    env = {
        **os.environ,
        "ILK_DATA_HOME": str(data_home),
        "ILK_DATA_DIR": str(data_home),
        "HOME": str(tmp_path),
    }
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace", env=env,
    )


def _read_last_launch(data_home: Path) -> dict:
    """Read the one last-launch.json written under the tmp data home."""
    matches = sorted(data_home.rglob("last-launch.json"))
    assert len(matches) == 1, (
        f"expected exactly one last-launch.json under {data_home}, "
        f"found {[str(p) for p in matches]}"
    )
    text = matches[0].read_text(encoding="utf-8")
    try:
        meta = json.loads(text)
    except ValueError as exc:
        raise AssertionError(
            f"last-launch.json is not valid JSON: {exc}\n{text}"
        ) from exc
    assert isinstance(meta, dict), f"last-launch.json is not an object: {meta!r}"
    return meta


# ── AC-1: the relaunch replays a gates-ON decision ──────────────────────────


class TestRelaunchReplaysGatesOn:
    """AC-1: ``run_local_checks: true`` in last-launch.json -> the relaunch
    passes ``--run-local-checks``, even though auto-detect would say OFF."""

    @pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError), reason="not built yet")
    def test_relaunch_passes_run_local_checks_when_recorded_true(
        self, tmp_path: Path
    ) -> None:
        launcher_dir = tmp_path / "runtime" / "launcher"
        _write_last_launch(launcher_dir, run_local_checks=True)
        fake_launcher = _make_fake_launcher(tmp_path)

        result = _run_relaunch(fake_launcher, launcher_dir)
        assert result.returncode == 0, (
            f"Relaunch exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        argv = _read_argv_log(tmp_path / "argv.log")
        assert "--run-local-checks" in argv, (
            "Relaunch dropped the recorded gates-ON decision and did not pass "
            f"--run-local-checks.\nargv: {argv!r}"
        )
        assert "--no-local-checks" not in argv, (
            "Relaunch passed --no-local-checks for a gates-ON record.\n"
            f"argv: {argv!r}"
        )


# ── AC-2: the relaunch replays a gates-OFF decision, and an absent key ──────


class TestRelaunchReplaysGatesOff:
    """AC-2: ``run_local_checks: false`` -> ``--no-local-checks``; the key
    absent (a launch from an older release) -> neither flag."""

    @pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError), reason="not built yet")
    def test_relaunch_replays_a_gates_off_decision(self, tmp_path: Path) -> None:
        # (b) key absent -> neither flag: today's auto-detect fallback.
        launcher_dir = tmp_path / "absent" / "runtime" / "launcher"
        _write_last_launch(launcher_dir)  # no run_local_checks key
        fake_launcher = _make_fake_launcher(tmp_path / "absent")

        result = _run_relaunch(fake_launcher, launcher_dir)
        assert result.returncode == 0, (
            f"Relaunch (key absent) exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        argv_absent = _read_argv_log(tmp_path / "absent" / "argv.log")
        assert "--run-local-checks" not in argv_absent, (
            "An absent run_local_checks key must pass no gates flag "
            f"(today's auto-detect), got --run-local-checks.\nargv: {argv_absent!r}"
        )
        assert "--no-local-checks" not in argv_absent, (
            "An absent run_local_checks key must pass no gates flag "
            f"(today's auto-detect), got --no-local-checks.\nargv: {argv_absent!r}"
        )

        # (a) key false -> --no-local-checks.  This is the case that pins the
        # replay: without it a relaunch re-derives gates from the live queue.
        launcher_dir2 = tmp_path / "off" / "runtime" / "launcher"
        _write_last_launch(launcher_dir2, run_local_checks=False)
        fake_launcher2 = _make_fake_launcher(tmp_path / "off")

        result2 = _run_relaunch(fake_launcher2, launcher_dir2)
        assert result2.returncode == 0, (
            f"Relaunch (key false) exited {result2.returncode}.\n"
            f"stdout: {result2.stdout}\nstderr: {result2.stderr}"
        )
        argv_off = _read_argv_log(tmp_path / "off" / "argv.log")
        assert "--no-local-checks" in argv_off, (
            "Relaunch dropped the recorded gates-OFF decision and did not pass "
            f"--no-local-checks.\nargv: {argv_off!r}"
        )
        assert "--run-local-checks" not in argv_off, (
            "Relaunch passed --run-local-checks for a gates-OFF record.\n"
            f"argv: {argv_off!r}"
        )


# ── AC-3: launch.sh persists the decision ───────────────────────────────────


class TestLaunchPersistsItsGatesDecision:
    """AC-3: launch.sh writes ``run_local_checks`` to last-launch.json — true
    when a queued sub-plan declares local_checks, false with --no-local-checks."""

    @pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError), reason="not built yet")
    def test_launch_writes_run_local_checks(self, tmp_path: Path) -> None:
        project = _make_git_project(tmp_path)
        data_home = tmp_path / ".ilk-data"
        plans_dir = _external_plans_dir(project, data_home)
        _write_queued_plan_with_local_checks(plans_dir)

        # (a) auto-detect ON: a queued sub-plan declares local_checks.
        result = _run_launch_main(tmp_path, project, extra_args=[])
        assert result.returncode == 0, (
            f"launch.sh main() exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "Gates: ON" in result.stdout, (
            "Fixture did not trigger auto-detect; the scenario is not the one "
            f"AC-3 names.\nstdout: {result.stdout}"
        )
        meta_on = _read_last_launch(data_home)
        assert meta_on.get("run_local_checks") is True, (
            "launch.sh did not persist the gates decision as JSON true "
            "(auto-detect ON).\nlast-launch.json: "
            f"{json.dumps(meta_on, indent=2)}"
        )

        # (b) --no-local-checks wins over auto-detect.
        result2 = _run_launch_main(tmp_path, project, extra_args=["--no-local-checks"])
        assert result2.returncode == 0, (
            f"launch.sh main() with --no-local-checks exited {result2.returncode}.\n"
            f"stdout: {result2.stdout}\nstderr: {result2.stderr}"
        )
        meta_off = _read_last_launch(data_home)
        assert meta_off.get("run_local_checks") is False, (
            "launch.sh did not persist the gates decision as JSON false "
            "(--no-local-checks).\nlast-launch.json: "
            f"{json.dumps(meta_off, indent=2)}"
        )
