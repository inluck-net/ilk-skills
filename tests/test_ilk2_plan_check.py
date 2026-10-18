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

# V1-loop script-folder modules plan_lint imported.  plan_check must not
# import them from the V1 folder.  ``test_importers`` is deliberately NOT in
# this list: plan_check now ships its own copy beside it
# (``skills/ilk2-plan/scripts/test_importers.py``) and calls it the way
# plan_lint does — see ``test_calls_its_own_test_importers``.
V1_SIBLING_MODULES = (
    "run_local_checks",
    "plan_slug",
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


LOCAL_TEST_IMPORTERS = (
    REPO_ROOT / "skills" / "ilk2-plan" / "scripts" / "test_importers.py"
)


def _load_plan_check():
    """Import plan_check.py as a module (it is a script, not a package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "plan_check_under_test", PLAN_CHECK,
    )
    assert spec is not None and spec.loader is not None, PLAN_CHECK
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_import_fixture(root: Path) -> None:
    """``pkg/cli.py`` plus two tests that name ``cli`` in a dotted import."""
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "cli.py").write_text("def f():\n    return 1\n",
                                         encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    tests.joinpath("test_x.py").write_text("from pkg.cli import f\n",
                                          encoding="utf-8")
    tests.joinpath("test_y.py").write_text("from pkg.cli import f\n",
                                          encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


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


def test_local_test_importers_copy_is_present() -> None:
    """The helper lives beside plan_check, marked with where it came from."""
    assert LOCAL_TEST_IMPORTERS.is_file(), f"{LOCAL_TEST_IMPORTERS} is missing"
    head = LOCAL_TEST_IMPORTERS.read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("#"), head
    assert "ilk-loop/scripts/test_importers.py" in head, head


def test_calls_its_own_test_importers() -> None:
    """Git-repo discovery goes through the helper copied beside plan_check,
    the way plan_lint calls it."""
    source = _source()
    assert "from test_importers import importer_tests" in source, (
        "plan_check must call its own scripts/test_importers.py, as plan_lint does"
    )
    assert "importer_tests(project_root" in source, (
        "plan_check must pass the project root and the module file to the helper"
    )


def test_discover_tests_by_import_git_repo_tracks_only(
    tmp_path: Path,
) -> None:
    """A committed test that names the module in a non-first dotted segment
    is found; the untracked twin is not.

    ``from pkg.cli import f`` names ``cli`` in the LAST segment.  Only
    git-tracked test files count (``test_importers.importer_tests``).
    """
    mod = _load_plan_check()
    repo = tmp_path / "repo"
    _write_import_fixture(repo)
    _git(repo, "init", "--quiet")
    _git(repo, "add", "pkg", "tests/test_x.py")
    _git(repo, "commit", "--quiet", "-m", "init")
    assert (repo / "tests" / "test_y.py").exists()

    found = mod._discover_tests_by_import("cli", repo)

    assert str(repo / "tests" / "test_x.py") in found, sorted(found)
    assert str(repo / "tests" / "test_y.py") not in found, sorted(found)


def test_discover_tests_by_import_fallback_matches_any_segment(
    tmp_path: Path,
) -> None:
    """Not a git repo: the filesystem fallback still finds a
    package-qualified import."""
    mod = _load_plan_check()
    repo = tmp_path / "plain"
    _write_import_fixture(repo)
    assert not (repo / ".git").exists()

    found = mod._discover_tests_by_import("cli", repo)

    assert str(repo / "tests" / "test_x.py") in found, sorted(found)


# ── UI-promise: standalone "hint" in prose is not an affordance ──────────────
#
# gh-resolve trial 18b/18c found that ``_UI_ADVERTISEMENT_RE``'s bare
# ``hint\b`` alternative matched ordinary prose like "the master lookup is a
# hint" or "a hint, not a guarantee".  The regex was narrowed to UI-context
# forms only: ``key hint``, ``tooltip``, and ``hint`` next to a UI noun/verb
# (hint text, status bar displays a hint, etc.).

# Sub-plans whose body contains "hint" in prose but no UI affordance.
# These must NOT trigger the UI-promise wiring warning.

HINT_PROSE_SUBPLAN = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

The master lookup is a hint, not a guarantee.

## Steps

### Step 0

Edit `tools/demo.py`.

```yaml
local_checks:
  - command: python3 -c "print(1)"
    timeout: 30
```
"""

HINT_PROSE_LOOKUP_SUBPLAN = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

The lookup is only a hint for the resolver.

## Steps

### Step 0

Edit `tools/demo.py`.

```yaml
local_checks:
  - command: python3 -c "print(1)"
    timeout: 30
```
"""

# Sub-plans that DO contain a UI affordance and should still fire.

KEY_HINT_SUBPLAN = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

A key hint shows Ctrl+K to the user.

## Steps

### Step 0

Edit `tools/demo.py`.

```yaml
local_checks:
  - command: python3 -c "print(1)"
    timeout: 30
```
"""

STATUS_BAR_HINT_SUBPLAN = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

The status bar displays a hint for the current mode.

## Steps

### Step 0

Edit `tools/demo.py`.

```yaml
local_checks:
  - command: python3 -c "print(1)"
    timeout: 30
```
"""

TOOLTIP_SUBPLAN = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

Show a tooltip on hover.

## Steps

### Step 0

Edit `tools/demo.py`.

```yaml
local_checks:
  - command: python3 -c "print(1)"
    timeout: 30
```
"""


def test_hint_prose_no_guarantee_is_not_ui_affordance(tmp_path: Path) -> None:
    """Prose "a hint, not a guarantee" must not trigger UI-promise."""
    home = tmp_path / "home"
    home.mkdir()

    result = _run(home, MASTER, HINT_PROSE_SUBPLAN, "hint-prose")

    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "UI affordance" not in result.stdout, result.stdout
    assert "promise-without-wiring" not in result.stdout, result.stdout


def test_hint_prose_lookup_is_not_ui_affordance(tmp_path: Path) -> None:
    """Prose "the lookup is only a hint" must not trigger UI-promise."""
    home = tmp_path / "home"
    home.mkdir()

    result = _run(home, MASTER, HINT_PROSE_LOOKUP_SUBPLAN, "hint-lookup")

    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "UI affordance" not in result.stdout, result.stdout
    assert "promise-without-wiring" not in result.stdout, result.stdout


def test_key_hint_still_fires_ui_promise(tmp_path: Path) -> None:
    """"A key hint shows Ctrl+K" is a real UI affordance: exit 1."""
    home = tmp_path / "home"
    home.mkdir()

    result = _run(home, MASTER, KEY_HINT_SUBPLAN, "key-hint")

    assert result.returncode == 1, (
        f"expected a finding, got exit {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "UI affordance" in result.stdout, result.stdout


def test_status_bar_displays_hint_fires_ui_promise(tmp_path: Path) -> None:
    """"The status bar displays a hint" is a real UI affordance: exit 1."""
    home = tmp_path / "home"
    home.mkdir()

    result = _run(home, MASTER, STATUS_BAR_HINT_SUBPLAN, "status-hint")

    assert result.returncode == 1, (
        f"expected a finding, got exit {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "UI affordance" in result.stdout, result.stdout


def test_tooltip_still_fires_ui_promise(tmp_path: Path) -> None:
    """"tooltip" is a UI affordance: exit 1."""
    home = tmp_path / "home"
    home.mkdir()

    result = _run(home, MASTER, TOOLTIP_SUBPLAN, "tooltip")

    assert result.returncode == 1, (
        f"expected a finding, got exit {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "UI affordance" in result.stdout, result.stdout


# ── D9 importer coverage: HARD, or INFO when the batch is verified ───────────
#
# ``lint_shared_module_gate`` (V1's D9: a step's last gate must also run the
# tests of every module that imports what the step changes) is HARD when the
# batch has no verification sub-plan — nothing else catches an importer's
# breakage.  When the batch HAS one, ilk2's verification step runs the full
# suite at the batch head and attributes any new failure to the step that
# caused it, so the finding is advisory: ``INFO:``, exit 0.

# A step whose scope_path changes a shared module while its gate runs
# nothing about it.  ``pkg/cli.py`` is imported by ``pkg/server.py``, and the
# committed ``tests/test_server.py`` exercises that import; the gate below
# runs none of those tests.
SHARED_MODULE_SUBPLAN = """\
---
plan: demo-step
verification_tier: compile-only
scope_paths:
  - pkg/cli.py
---

# Sub-plan: demo-step

## Steps

### Step 0

Edit `pkg/cli.py`.

```yaml
local_checks:
  - command: python3 -c "print(1)"
    timeout: 30
```
"""

# The batch's verification sub-plan: exactly one step, the full suite, and
# the ``batch_verification: true`` marker the auto-detect keys on.
VERIFY_SUBPLAN = """\
---
plan: demo-verify
verification_tier: loop-verified
batch_verification: true
---

# Sub-plan: demo-verify

## Steps

### Step 0

Run the full suite at the batch head.

```yaml
local_checks:
  - command: python3 -m pytest -q
    timeout: 120
```
"""

MASTER_WITH_VERIFY = """\
---
master_plan: 2026-10-18-demo-batch
base_branch: main
---

# MASTER: demo batch

| # | sub-plan | purpose | tier | priority | notes | steps |
|---|----------|---------|------|----------|-------|-------|
| 1 | 2026-10-18-demo-step | one step | compile-only | P0 | demo | 1 |
| 2 | 2026-10-18-demo-verify | batch verification | loop-verified | P0 | owed | 1 |
"""


def _write_shared_fixture(root: Path) -> None:
    """``pkg/cli.py`` (changed) imported by ``pkg/server.py``, with a
    committed test of that import — the D9 shape, on branch ``main`` so the
    scope-path branch checks stay quiet and only D9 can speak."""
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "cli.py").write_text("def f():\n    return 1\n",
                                         encoding="utf-8")
    # A production importer: ``from cli import`` is what _find_importers greps.
    (root / "pkg" / "server.py").write_text("from cli import f\n",
                                            encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    # Committed, and it imports the changed module.
    tests.joinpath("test_server.py").write_text("from pkg.cli import f\n",
                                               encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "add", "pkg", "tests")
    _git(root, "commit", "--quiet", "-m", "init")
    _git(root, "branch", "-M", "main")


def _run_shared(
    home: Path,
    name: str,
    *,
    verified: bool = False,
    with_verify_subplan: bool = False,
    master: str = MASTER,
) -> subprocess.CompletedProcess:
    """Run plan_check against the shared-module fixture under *home/name*.

    ``HOME`` is set to *home* (a fresh temp dir), never the real home, and
    ``cwd`` is the fixture dir.
    """
    d = home / name
    d.mkdir(parents=True, exist_ok=True)
    _write_shared_fixture(d)
    master_text = MASTER_WITH_VERIFY if with_verify_subplan else master
    master_path = d / "MASTER.md"
    master_path.write_text(master_text, encoding="utf-8")
    sub_path = d / "2026-10-18-demo-step.md"
    sub_path.write_text(SHARED_MODULE_SUBPLAN, encoding="utf-8")
    paths = [str(sub_path)]
    if with_verify_subplan:
        verify_path = d / "2026-10-18-demo-verify.md"
        verify_path.write_text(VERIFY_SUBPLAN, encoding="utf-8")
        paths.append(str(verify_path))
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
    ]
    if verified:
        cmd.append("--verified")
    cmd.extend(paths)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(d))


def test_shared_module_gate_is_hard_without_verified(tmp_path: Path) -> None:
    """A step gate that misses a committed importer test is HARD: exit 1.

    No verification sub-plan in the batch, so nothing else would catch the
    importer's breakage — the rule must stay hard.
    """
    home = tmp_path / "home"
    home.mkdir()

    result = _run_shared(home, "hard", verified=False)

    assert result.returncode == 1, (
        f"expected a finding, got exit {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Traceback" not in result.stdout, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "WARN: HARD 2026-10-18-demo-step" in result.stdout, result.stdout
    assert "tests/test_server.py" in result.stdout, result.stdout
    assert "INFO:" not in result.stdout, result.stdout


def test_shared_module_gate_is_info_with_verified(tmp_path: Path) -> None:
    """``--verified`` demotes the same finding to INFO and clears the exit."""
    home = tmp_path / "home"
    home.mkdir()

    result = _run_shared(home, "info", verified=True)

    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "Traceback" not in result.stdout, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "INFO:" in result.stdout, result.stdout
    assert "tests/test_server.py" in result.stdout, result.stdout
    assert "WARN:" not in result.stdout, result.stdout
    assert "HARD" not in result.stdout, result.stdout


def test_verified_autodetects_a_batch_verification_subplan(
    tmp_path: Path,
) -> None:
    """``--master`` plus a linted sub-plan declaring
    ``batch_verification: true`` behaves as ``--verified``: INFO, exit 0."""
    home = tmp_path / "home"
    home.mkdir()

    result = _run_shared(home, "auto", verified=False, with_verify_subplan=True)

    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "Traceback" not in result.stdout, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "INFO:" in result.stdout, result.stdout
    assert "tests/test_server.py" in result.stdout, result.stdout
    assert "WARN:" not in result.stdout, result.stdout
    assert "HARD" not in result.stdout, result.stdout


# ── Directory-arg coverage: a gate that runs a directory covers all tests ────
#
# ``_is_whole_suite_command`` treats ``pytest tests`` as a whole-suite gate
# (the directory arg runs every test under it).  ``_gate_covers_module_and_callers``
# also handles directory args for edge cases that bypass the whole-suite check.

def test_is_whole_suite_command_directory_arg() -> None:
    """``pytest tests`` and ``pytest tests/`` are whole-suite commands."""
    mod = _load_plan_check()
    assert mod._is_whole_suite_command("pytest tests") is True
    assert mod._is_whole_suite_command("pytest tests/") is True
    assert mod._is_whole_suite_command("python -m pytest -q tests") is True
    assert mod._is_whole_suite_command("python3 -m pytest -q tests/") is True
    # A file-scoped gate is NOT a whole-suite command.
    assert mod._is_whole_suite_command("pytest tests/test_x.py") is False
    # A sub-directory gate IS a whole-suite command.
    assert mod._is_whole_suite_command("pytest tests/pins") is True


def test_gate_covers_directory_arg(tmp_path: Path) -> None:
    """A directory gate covers every test file under it."""
    mod = _load_plan_check()
    # Create test files on disk.
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("pass\n")
    sub = tests / "sub"
    sub.mkdir()
    (sub / "test_y.py").write_text("pass\n")
    test_x = str(tests / "test_x.py")
    test_y = str(sub / "test_y.py")
    # _gate_covers_module_and_callers requires covering BOTH module AND callers.
    # Directory gate covers both.
    assert mod._gate_covers_module_and_callers(
        "python -m pytest -q tests", {test_x}, {test_y}, tmp_path,
    ) is True
    # Sub-directory gate covers only the sub-directory test.
    assert mod._gate_covers_module_and_callers(
        "pytest tests/sub", {test_y}, {test_y}, tmp_path,
    ) is True
    assert mod._gate_covers_module_and_callers(
        "pytest tests/sub", {test_x}, {test_y}, tmp_path,
    ) is False
    # File gate covers only that file.
    assert mod._gate_covers_module_and_callers(
        "pytest tests/test_x.py", {test_x}, {test_x}, tmp_path,
    ) is True
    assert mod._gate_covers_module_and_callers(
        "pytest tests/test_x.py", {test_x}, {test_y}, tmp_path,
    ) is False


def test_directory_gate_whole_suite_exempts_shared_module(
    tmp_path: Path,
) -> None:
    """A gate running ``pytest tests`` is a whole-suite command — D9 exits
    early without firing, even when the changed module has importers."""
    home = tmp_path / "home"
    home.mkdir()
    d = home / "dir"
    d.mkdir(parents=True, exist_ok=True)
    _write_shared_fixture(d)
    master_path = d / "MASTER.md"
    master_path.write_text(MASTER, encoding="utf-8")
    sub = """\
---
plan: demo-step
verification_tier: compile-only
scope_paths:
  - pkg/cli.py
---

# Sub-plan: demo-step

## Steps

### Step 0

Edit `pkg/cli.py`.

```yaml
local_checks:
  - command: python3 -m pytest -q tests
    timeout: 30
```
"""
    sub_path = d / "2026-10-18-demo-step.md"
    sub_path.write_text(sub, encoding="utf-8")
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}
    cmd = [
        sys.executable, "-I", str(PLAN_CHECK),
        "--master", str(master_path),
        "--project-root", str(d),
        str(sub_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(d))
    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "Traceback" not in result.stdout, result.stdout
    assert "HARD" not in result.stdout, result.stdout


# ── _strip_dot_slash_prefix: ./ removed as a prefix, not as a char set ────
#
# ``str.lstrip("./")`` strips a SET of characters (``.`` and ``/``), so
# ``../tests`` becomes ``tests`` and ``.github/tests`` becomes
# ``github/tests``.  The helper must strip only the literal prefix ``./``.


def test_strip_dot_slash_prefix_basic() -> None:
    """``./tests`` → ``tests``; idempotent on repeated calls."""
    mod = _load_plan_check()
    assert mod._strip_dot_slash_prefix("./tests") == "tests"
    assert mod._strip_dot_slash_prefix("tests") == "tests"
    assert mod._strip_dot_slash_prefix("./tests/pins/") == "tests/pins/"


def test_strip_dot_slash_prefix_preserves_parent() -> None:
    """``../tests`` is NOT stripped — it references a parent directory."""
    mod = _load_plan_check()
    assert mod._strip_dot_slash_prefix("../tests") == "../tests"
    assert mod._strip_dot_slash_prefix("../../tests") == "../../tests"


def test_strip_dot_slash_prefix_preserves_dotdir() -> None:
    """``.github/tests`` is NOT stripped — the dot is part of the dir name."""
    mod = _load_plan_check()
    assert mod._strip_dot_slash_prefix(".github/tests") == ".github/tests"
    assert mod._strip_dot_slash_prefix("./.hidden/dir") == ".hidden/dir"


def test_extract_directory_args_mixed_prefixes() -> None:
    """The real-world command from the defect report returns correct dirs."""
    mod = _load_plan_check()
    result = mod._extract_directory_args(
        "python -m pytest ../tests .github/tests ./tests/pins/"
    )
    assert result == ["../tests", ".github/tests", "tests/pins"], result


def test_extract_directory_args_dot_slash_only() -> None:
    """``./tests`` normalises to ``tests``."""
    mod = _load_plan_check()
    assert mod._extract_directory_args("pytest ./tests") == ["tests"]
    assert mod._extract_directory_args("pytest ./tests/") == ["tests"]


def test_test_file_under_any_dir_parent_dir_does_not_cover() -> None:
    """A gate on ``../tests`` does NOT cover ``tests/test_x.py`` in the project."""
    mod = _load_plan_check()
    assert mod._test_file_under_any_dir(
        "tests/test_x.py", ["../tests"],
    ) is False


def test_test_file_under_any_dir_dotdir_covers() -> None:
    """A gate on ``.github/tests`` covers ``.github/tests/test_a.py``."""
    mod = _load_plan_check()
    assert mod._test_file_under_any_dir(
        ".github/tests/test_a.py", [".github/tests"],
    ) is True
    # But NOT ``github/tests/test_a.py`` (the old bug would match this).
    assert mod._test_file_under_any_dir(
        "github/tests/test_a.py", [".github/tests"],
    ) is False


def test_test_file_under_any_dir_dot_slash_covers() -> None:
    """``./tests`` normalised to ``tests`` still covers ``tests/test_x.py``."""
    mod = _load_plan_check()
    # The dir arg is already normalised by _extract_directory_args, so
    # _test_file_under_any_dir receives ``tests``, not ``./tests``.
    assert mod._test_file_under_any_dir(
        "tests/test_x.py", ["tests"],
    ) is True


# ── Red-first step 0: "pinned" in heading alone is not red-first ─────────
#
# ``lint_redfirst_step0_per_step_gate_demands_green`` checked heading +
# commit lines for ``\bpin(?:s|ned|ning)?\b`` and treated a match as
# red-first on its own.  A coverage-restoring step whose heading is
# "restore the pinned assertions" was flagged HARD although it writes no
# failing test.  The fix: pin/reproduce words count as red-first only when
# the step body also carries a failing-test signal (must fail, xfail, etc.).

REDFIRST_PINNED_ADJECTIVE = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

## Steps

### Step 0 — test(claim): restore the pinned assertions 18a deleted

The existing tests all pass at commit.  This step restores coverage that
was removed in an earlier edit.

```yaml
local_checks:
  - command: pytest tests/test_restore.py -q
    timeout: 30
```
"""

REDFIRST_PIN_WITH_FAIL = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

## Steps

### Step 0 — test: pin the bug with a failing test

The new test must fail — the fix lands in step 1.

```yaml
local_checks:
  - command: pytest tests/test_pin.py -q
    timeout: 30
```
"""

REDFIRST_RED_FIRST_HEADING = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

## Steps

### Step 0 — red-first: pin D-12

Write the failing reproducer.

```yaml
local_checks:
  - command: pytest tests/test_d12.py -q
    timeout: 30
```
"""

REDFIRST_PINNED_XFAIL_EXEMPT = """\
---
plan: demo-step
verification_tier: compile-only
---

# Sub-plan: demo-step

## Steps

### Step 0 — test: pin the expected failure

The pinned test uses xfail(strict=True) so the gate stays green.

```yaml
local_checks:
  - command: pytest tests/test_pin_xfail.py -q
    timeout: 30
```
"""


def test_redfirst_pinned_adjective_no_finding(tmp_path: Path) -> None:
    """"pinned" as an adjective in a step-0 heading must not flag red-first."""
    mod = _load_plan_check()
    findings = mod.lint_redfirst_step0_per_step_gate_demands_green(
        REDFIRST_PINNED_ADJECTIVE, "demo-step",
    )
    assert findings == [], findings


def test_redfirst_pin_with_failing_signal_finds(tmp_path: Path) -> None:
    """pin + "must fail" in the body is red-first: HARD finding."""
    mod = _load_plan_check()
    findings = mod.lint_redfirst_step0_per_step_gate_demands_green(
        REDFIRST_PIN_WITH_FAIL, "demo-step",
    )
    assert len(findings) == 1, findings
    assert "HARD" in findings[0], findings[0]
    assert "red-first" in findings[0].lower() or "demands green" in findings[0].lower(), findings[0]


def test_redfirst_red_first_heading_still_finds(tmp_path: Path) -> None:
    """"red-first" in the heading is sufficient on its own: HARD finding."""
    mod = _load_plan_check()
    findings = mod.lint_redfirst_step0_per_step_gate_demands_green(
        REDFIRST_RED_FIRST_HEADING, "demo-step",
    )
    assert len(findings) == 1, findings
    assert "HARD" in findings[0], findings[0]


def test_redfirst_pinned_with_xfail_exempt(tmp_path: Path) -> None:
    """xfail(strict=True) exempts even when the heading says "pin"."""
    mod = _load_plan_check()
    # The xfail exemption is checked against the full step body, not the
    # heading.  Add it to the body text.
    text_with_xfail = REDFIRST_PINNED_XFAIL_EXEMPT.replace(
        "The pinned test uses",
        "The pinned test uses\nxfail(strict=True) so that",
    )
    findings = mod.lint_redfirst_step0_per_step_gate_demands_green(
        text_with_xfail, "demo-step",
    )
    assert findings == [], findings


# ── Off-base branch: a file deleted on main and re-created is not off-base ──
#
# ``lint_scope_path_off_base_branch`` used ``git branch --contains <commit>``
# to find which refs carry a path.  For a file deleted on main, the deletion
# commit is on main, so main was listed as "carrying" a file main's tip tree
# does not have.  The fix: use ``git cat-file -e <ref>:<path>`` to test tree
# presence instead of commit containment.


def _scope_subplan(*paths: str) -> str:
    """Build a minimal sub-plan text with the given scope_paths."""
    items = "\n".join(f"    - {p}" for p in paths)
    return f"""\
---
plan: demo-step
scope_paths:
{items}
---

# Sub-plan: demo-step
"""


def _off_base(repo: Path, *paths: str) -> list[str]:
    """Run lint_scope_path_off_base_branch in *repo* with the given paths."""
    mod = _load_plan_check()
    old = mod._GIT_CWD
    try:
        mod._GIT_CWD = repo
        return mod.lint_scope_path_off_base_branch(
            _scope_subplan(*paths), "demo-step",
        )
    finally:
        mod._GIT_CWD = old


def test_off_base_deleted_file_not_flagged(tmp_path: Path) -> None:
    """A file committed then deleted on main, absent everywhere → 0 findings."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    (repo / "gone.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "gone.py")
    _git(repo, "commit", "--quiet", "-m", "add gone.py")
    (repo / "gone.py").unlink()
    _git(repo, "rm", "gone.py")
    _git(repo, "commit", "--quiet", "-m", "delete gone.py")

    findings = _off_base(repo, "gone.py")

    assert findings == [], findings


def test_off_base_file_only_on_feature_flagged(tmp_path: Path) -> None:
    """A file present only on branch `feature` → 1 HARD finding."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    (repo / "init.txt").write_text("init\n", encoding="utf-8")
    _git(repo, "add", "init.txt")
    _git(repo, "commit", "--quiet", "-m", "init")
    _git(repo, "checkout", "-b", "feature", "--quiet")
    (repo / "feature_only.py").write_text("y = 2\n", encoding="utf-8")
    _git(repo, "add", "feature_only.py")
    _git(repo, "commit", "--quiet", "-m", "add feature_only.py")
    _git(repo, "checkout", "main", "--quiet")

    findings = _off_base(repo, "feature_only.py")

    assert len(findings) == 1, findings
    assert "HARD" in findings[0], findings[0]
    # "feature" must appear as a ref that carries the file.
    assert "feature" in findings[0], findings[0]
    # "main" appears in "base ref 'main (default)'" but must NOT appear in the
    # "exists on:" list — only feature carries the file.
    exists_on = findings[0].split("exists on:")[1]
    assert "main" not in exists_on, findings[0]


def test_off_base_file_on_main_ok(tmp_path: Path) -> None:
    """A file present on main → 0 findings."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    (repo / "exists.py").write_text("z = 3\n", encoding="utf-8")
    _git(repo, "add", "exists.py")
    _git(repo, "commit", "--quiet", "-m", "add exists.py")

    findings = _off_base(repo, "exists.py")

    assert findings == [], findings


def test_off_base_path_never_in_history_ok(tmp_path: Path) -> None:
    """A path never in git history → 0 findings (new file)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    (repo / "init.txt").write_text("init\n", encoding="utf-8")
    _git(repo, "add", "init.txt")
    _git(repo, "commit", "--quiet", "-m", "init")

    findings = _off_base(repo, "brand_new.py")

    assert findings == [], findings
