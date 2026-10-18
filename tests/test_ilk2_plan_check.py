"""Pin skills/ilk2-plan/scripts/plan_check.py as plan_lint without V1's runner.

docs/07-retiring-v1.md §7.4.2 ("plan_lint.py, a hybrid") splits V1's
``plan_lint.py`` three ways: structural checks into ilk2, authoring heuristics
standalone in the planner's folder, V1-runner checks dropped.  This file is
that standalone checker, so it must:

* import nothing from the V1 loop's script folder;
* read nothing under V1's data home;
* keep every plan-quality check, and still fire one of them on a bad plan.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_CHECK = REPO_ROOT / "skills" / "ilk2-plan" / "scripts" / "plan_check.py"

# V1-loop script-folder modules plan_lint imported.  plan_check must inline
# their behaviour, not import them.
V1_SIBLING_MODULES = (
    "run_local_checks",
    "plan_slug",
    "test_importers",
    "ilk_paths",
    "ship_config",
    "gate_cost",
)

MASTER = """\
---
master_plan: 2026-10-18-demo-batch
base_branch: main
---

# MASTER: demo batch

| # | sub-plan | purpose | tier | priority | notes | steps |
|---|----------|---------|------|----------|-------|-------|
| 1 | 2026-10-18-demo-step | one step | compile-only | P0 | demo | 1 |
"""

# A sub-plan whose gate keeps its exit status.  Every kept check is quiet on
# this shape, so plan_check must exit 0.
CLEAN_SUBPLAN = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

## Steps

### Step 0

Edit `tools/demo.py`.

```yaml
local_checks:
  - command: python3 -c "print(1)"
    timeout: 30
```
"""

# Same shape, but the gate pipes to `| tail` and so discards its exit status.
# ``lint_exit_status_discarded`` is one of the kept plan-quality checks.
RED_SUBPLAN = """\
---
plan: demo-step
---

# Sub-plan: demo-step

## Steps

### Step 0

Edit `tools/demo.py`.

```yaml
local_checks:
  - command: python3 -c "print(1)" | tail -5
    timeout: 30
```
"""


def _source() -> str:
    assert PLAN_CHECK.is_file(), f"{PLAN_CHECK} is missing"
    return PLAN_CHECK.read_text(encoding="utf-8")


def _run(home: Path, master: str, subplan: str, name: str) -> subprocess.CompletedProcess:
    """Run plan_check.py --master on a fixture pair under *home*.

    ``HOME`` is set to *home* (a fresh temp dir), never the real home, and
    ``cwd`` is the fixture dir so no git repository is in scope.
    """
    d = home / name
    d.mkdir(parents=True, exist_ok=True)
    master_path = d / "MASTER.md"
    master_path.write_text(master, encoding="utf-8")
    sub_path = d / "2026-10-18-demo-step.md"
    sub_path.write_text(subplan, encoding="utf-8")
    env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
    }
    cmd = [
        sys.executable,
        "-I",
        str(PLAN_CHECK),
        "--master",
        str(master_path),
        "--project-root",
        str(d),
        str(sub_path),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(d))


def test_clean_master_and_subplan_exits_zero(tmp_path: Path) -> None:
    """A small MASTER + sub-plan passes: exit code 0 and no traceback."""
    home = tmp_path / "home"
    home.mkdir()
    assert str(home.resolve()).startswith(str(tmp_path.resolve()) + os.sep)

    result = _run(home, MASTER, CLEAN_SUBPLAN, "clean")

    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "Traceback" not in result.stdout, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "OK: plan_check clean" in result.stdout, result.stdout


def test_source_has_no_ilk_loop_import() -> None:
    """The copy imports nothing from the V1 loop's script folder."""
    source = _source()

    import_re = re.compile(r"^\s*(?:from|import)\s+\S+", re.MULTILINE)
    for match in import_re.finditer(source):
        line = match.group(0)
        assert "ilk-loop" not in line, f"imports the V1 loop folder: {line!r}"
        for module in V1_SIBLING_MODULES:
            assert module not in line, (
                f"imports V1 sibling {module!r}: {line!r} — plan_check must "
                f"inline the behaviour instead"
            )

    # And no runtime `__import__` / importlib pull of them either.
    for module in V1_SIBLING_MODULES:
        assert f'"{module}"' not in source, f"string import of {module!r}"
        assert f"'{module}'" not in source, f"string import of {module!r}"


def test_source_never_reads_the_v1_data_home() -> None:
    """The copy reads nothing under V1's data home."""
    source = _source()
    assert ".ilk-data" not in source, (
        "plan_check.py must not read (or even name) V1's data home"
    )


def test_kept_check_fires_on_a_gate_that_discards_its_exit_status(
    tmp_path: Path,
) -> None:
    """One kept plan-quality check still fires: exit-status discard."""
    home = tmp_path / "home"
    home.mkdir()

    result = _run(home, MASTER, RED_SUBPLAN, "red")

    assert result.returncode == 1, (
        f"expected a finding, got exit {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Traceback" not in result.stdout, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "discards exit status" in result.stdout, result.stdout
    assert "2026-10-18-demo-step" in result.stdout, result.stdout
