"""Structural wiring tests for run_ilk_loop_claude.sh.

`bash -n` (test_ship_gap.py::TestDriverParses) validates syntax only: it
cannot see an unresolved function name, and it cannot see a python3
invocation resolving state from the launcher's cwd instead of the
project's. The 2026-08-30 batch shipped both shapes past it. These tests
assert driver *structure* — by reading its lines and by executing the
exact snippets the driver executes — rather than by parsing tokens.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_DRIVER = _SCRIPTS / "run_ilk_loop_claude.sh"
_LOOP_STATUS = _SCRIPTS / "loop_status.py"

sys.path.insert(0, str(_SCRIPTS))
from importlib import import_module  # noqa: E402

ilk_paths = import_module("ilk_paths")  # noqa: E402

# Minimal external plans tree, written under a sandboxed ILK_DATA_HOME so
# the resolution under test never reads the real ~/.ilk-data.
_MASTER_TEXT = """\
---
master_plan: 2026-09-01-wiring-probe
batch_date: 2026-09-01
status: active
current_subplan: 2026-09-01-wiring-probe
---

# MASTER plan: wiring probe

## Sub-plan registry

| # | Order | Slug | Status |
|---|---|---|---|
| 1 | 1 | [2026-09-01-wiring-probe.md](./2026-09-01-wiring-probe.md) | pending |
"""

_SUBPLAN_TEXT = """\
---
plan: wiring-probe
status: pending
current_step: 0
estimated_steps: 1
last_updated: 2026-09-01
verification_tier: loop-verified
---

# Sub-plan: wiring probe
"""


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """Hermetic loop project: a git repo whose plans live in a sandboxed
    ILK_DATA_HOME, keyed by the canonical project_key resolver."""
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(
        ["git", "init", "-q"], cwd=project, check=True, capture_output=True,
    )
    data_home = tmp_path / ".ilk-data"
    plans = data_home / "projects" / ilk_paths.project_key(project) / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-09-01-wiring-probe.md").write_text(
        _MASTER_TEXT, encoding="utf-8",
    )
    (plans / "2026-09-01-wiring-probe.md").write_text(
        _SUBPLAN_TEXT, encoding="utf-8",
    )
    return project, data_home


def _hermetic_env(data_home: Path) -> dict[str, str]:
    env = {**os.environ, "ILK_DATA_HOME": str(data_home)}
    # The back-compat alias must not override the primary var (AC-2 of the
    # data-home convention; precedence is ILK_DATA_HOME → ILK_DATA_DIR).
    env.pop("ILK_DATA_DIR", None)
    return env


def _run_status(cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LOOP_STATUS)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=30,
    )


def _final_status_snippet() -> str:
    """The driver's own final-status lines — everything between
    `echo "Final loop_status:"` and the next blank line."""
    lines = _DRIVER.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if 'echo "Final loop_status:"' in line:
            block = []
            for follow in lines[i + 1:]:
                if not follow.strip():
                    break
                block.append(follow)
            assert block, "no invocation follows 'Final loop_status:' in the driver"
            return "\n".join(block)
    raise AssertionError(
        "driver no longer prints 'Final loop_status:' — update this test"
    )


class TestLoopStatusCwd:
    """AC-4 + AC-5: every loop_status invocation resolves the project, not
    the launcher's cwd."""

    def test_every_loop_status_call_is_cwd_scoped(self) -> None:
        """AC-4: each `python3 "$LOOP_STATUS_SCRIPT"` execution sits inside a
        `cd "$PROJECT_PATH"` construct.

        `[[ -f "$LOOP_STATUS_SCRIPT" ]]` existence guards reference the
        variable without executing it, so they are skipped.
        """
        if not _DRIVER.exists():
            pytest.skip("driver script not found")
        text = _DRIVER.read_text(encoding="utf-8")
        referencing = [
            line for line in text.splitlines()
            if 'python3 "$LOOP_STATUS_SCRIPT"' in line
        ]
        executions = [
            line for line in referencing
            if not ("[[" in line and " -f " in line)
        ]
        assert executions, (
            'no `python3 "$LOOP_STATUS_SCRIPT"` executions found — the parser '
            "matches nothing; check the driver's invocation style"
        )
        unwrapped = [
            line for line in executions if 'cd "$PROJECT_PATH"' not in line
        ]
        assert not unwrapped, (
            f'{len(unwrapped)} of {len(executions)} `python3 '
            f'"$LOOP_STATUS_SCRIPT"` invocations resolve from the launcher\'s '
            f"cwd instead of $PROJECT_PATH: {unwrapped}"
        )

    def test_final_status_resolves_project_not_cwd(self, tmp_path: Path) -> None:
        """AC-5: the driver's final-status code path, run from a launcher cwd
        outside the project, resolves the *project's* plans dir.

        Pins the mechanism first: loop_status.py walks up from cwd, so the
        bare invocation from outside the project reports `no plans dir
        found` while the same invocation from the project root does not.
        Then executes the driver's own `Final loop_status:` lines verbatim
        from the outside cwd — that is the assertion the driver must satisfy.
        """
        project, data_home = _make_project(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        env = _hermetic_env(data_home)

        # The resolver is cwd-driven: bare from outside → exit 2, no plans.
        bare = _run_status(outside, env)
        assert bare.returncode == 2
        assert "no plans dir found" in bare.stdout + bare.stderr

        # The identical invocation from the project root finds the plans.
        wrapped = _run_status(project, env)
        assert wrapped.returncode != 2
        assert "no plans dir found" not in wrapped.stdout + wrapped.stderr

        # The driver's own final-status lines, executed verbatim from the
        # launcher's cwd. The driver declares PROJECT_PATH itself; the env
        # stands in for it exactly as the loop would have set it.
        proc = subprocess.run(
            ["bash", "-c", _final_status_snippet()],
            cwd=outside,
            env={
                **env,
                "LOOP_STATUS_SCRIPT": str(_LOOP_STATUS),
                "PROJECT_PATH": str(project),
            },
            capture_output=True, text=True, timeout=30,
        )
        assert "no plans dir found" not in proc.stdout + proc.stderr, (
            "the driver's final-status line resolved from the launcher's cwd "
            "and could not see the project's plans dir (defect C)"
        )
