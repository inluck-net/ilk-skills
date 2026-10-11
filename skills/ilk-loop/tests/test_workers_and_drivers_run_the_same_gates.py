r"""Workers and drivers run the same collision-safe gates.

Part of sub-plan ``workers-and-drivers-run-the-same-gates`` (step 0 of 2).

Confirmed defect (handoff §2/§15): the synthesized mention gate joined
``tests/pins/test_43a_…py`` and ``tests/test_43a_…py`` into ONE pytest
invocation.  pytest's default ``prepend`` import mode cannot collect two
modules sharing a basename, so the gate died at collection — 112 collected /
1 error — and the batch stopped on a structural failure that said nothing
about the code under test.  Declared coverage was then decided by substring
matching against the command text (run_local_checks.py), so a comment, a
look-alike basename or a ``::node`` selector could waive a required test.

This file's production-entry tests drive the real ``run_local_checks`` CLI
against hermetic temporary git projects and assert the user-visible result.

AC-1  Colliding pytest basenames are partitioned into separate bounded
      invocations: every selected file runs, 0 collection errors on the
      reproduced prepend-mode shape, and a failure in EITHER partition
      blocks advancement.
AC-2  Declared coverage comes from actual pytest file/directory arguments.
      A ``::node`` selector does not cover its file; a quoted path does; a
      comment, a look-alike basename and a substring do not.  An unparseable
      command leaves coverage unresolved rather than omitting tests.
AC-3  One canonical inventory entry point supplies command/scope/timeout and
      committed-tree identity for every partition.
AC-4  Plan lint flags a declared pytest invocation that passes duplicate
      import basenames, unless the effective import strategy is proven
      compatible (importlib project config, importlib on the command, or
      vitest).
AC-5  The mention cap policy is unchanged: over the cap the gate is skipped
      with an explicit warning, and partitions are never dropped to fit it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_lint  # noqa: E402
import run_local_checks as rlc  # noqa: E402


# ── Hermetic project helpers ─────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


_SUBPLAN_FIXTURE = """\
---
plan: {slug}
status: in-progress
current_step: {step}
estimated_steps: 2
local_checks: []
---

# Sub-plan: test

### Step 0 — setup
```yaml
local_checks:
  - command: "true"
    timeout: 30
```

### Step 1 — work
```yaml
local_checks:
{declared}
```
"""

# Minimal pytest config: default "prepend" import mode (no importlib), with
# testpaths the way a real project declares them.
_PYTEST_INI = """\
[pytest]
testpaths = tests
norecursedirs = .git .pytest_cache __pycache__ fixtures logs
"""

_LAUNCH_DEFAULT = {
    "ship": {
        "suite": {
            "command": "python3 -m pytest",
            "flags": ["-v", "-p", "no:cacheprovider"],
        },
    },
}

# Same basename, two directories — the shape that broke gh-resolve.
_TWIN_NAME = "test_43a_a_held_claim_says_so.py"
_LIVE_TWIN = f"tests/{_TWIN_NAME}"
_PIN_TWIN = f"tests/pins/{_TWIN_NAME}"
_SRC = "src/calc.py"
_PIN_COMMENT = f"# see {_SRC}:1\n"


# Captured before any pin: the interpreter's own user site-packages.  pytest
# is installed there, and Python resolves that directory through HOME — so
# pinning HOME without re-declaring it would make every gated child report
# "No module named pytest", which is an environment fault, not a verdict.
_REAL_USER_SITE = ""
try:
    import site
    _REAL_USER_SITE = site.getusersitepackages() or ""
except Exception:
    _REAL_USER_SITE = ""


def _pin_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pin HOME / ILK_DATA_HOME / ILK_DATA_DIR together for every child."""
    home = tmp_path / "_home"
    home.mkdir(exist_ok=True)
    data = home / ".ilk-data"
    data.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(data))
    monkeypatch.setenv("ILK_DATA_DIR", str(data))
    if _REAL_USER_SITE and os.path.isdir(_REAL_USER_SITE):
        monkeypatch.setenv("PYTHONPATH", _REAL_USER_SITE)


def _setup_repo(
    tmp_path: Path,
    *,
    launch: dict | None = None,
    slug: str = "alpha",
    step: int = 1,
    base_files: dict[str, str] | None = None,
    commit_files: dict[str, str] | None = None,
    declared: str = '  - command: "true"\n    timeout: 30',
    pytest_ini: str = _PYTEST_INI,
) -> Path:
    """Hermetic temporary git project with plans, config and a step commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    (repo / "pytest.ini").write_text(pytest_ini, encoding="utf-8")
    (repo / ".ilk-launch.json").write_text(
        json.dumps(launch if launch is not None else _LAUNCH_DEFAULT, indent=2),
        encoding="utf-8",
    )

    for rel, content in (base_files or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: setup")

    for rel, content in (commit_files or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"feat(x): change [plan:{slug}#step-{step}]")

    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / f"2026-10-11-{slug}.md").write_text(
        _SUBPLAN_FIXTURE.format(slug=slug, step=step, declared=declared),
        encoding="utf-8",
    )
    (plans / "MASTER-2026-10-11-execution-plan.md").write_text(
        f"---\nstatus: active\n---\n# m\n- [x](./2026-10-11-{slug}.md)\n",
        encoding="utf-8",
    )
    return repo


def _run_cli(repo: Path, slug: str = "alpha", step: int = 1) -> dict:
    """Drive the REAL CLI entry point and return its JSON result."""
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        rlc.main(["--project", str(repo), "--slug", slug, "--step", str(step)])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return json.loads(output)


def _mention_results(result: dict) -> list[dict]:
    return [c for c in result.get("results", []) if c.get("scope") == "mention"]


def _twin_repo(tmp_path: Path, live_body: str, pin_body: str) -> Path:
    """The reproduced prepend-mode shape: pins/ and live twins of one basename."""
    return _setup_repo(
        tmp_path,
        base_files={
            "pytest.ini": _PYTEST_INI,
            _SRC: "def add(a, b): return a + b\n",
            _LIVE_TWIN: _PIN_COMMENT + live_body,
            _PIN_TWIN: _PIN_COMMENT + pin_body,
        },
        commit_files={_SRC: "def add(a, b): return a + b + 0\n"},
    )


# ── AC-1: partitioning through the production CLI ────────────────────────────


class TestCollidingBasenamesPartition:
    def test_pins_and_live_twins_run_in_separate_invocations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both twins execute, in two bounded invocations, 0 collection errors."""
        _pin_env(monkeypatch, tmp_path)
        repo = _twin_repo(
            tmp_path,
            live_body="def test_live(): assert True\n",
            pin_body="def test_pin(): assert True\n",
        )
        result = _run_cli(repo)

        mentions = _mention_results(result)
        assert result["mention_check_count"] == 2, (
            f"expected 2 partitions for a pins/live basename collision, got "
            f"{result['mention_check_count']}: {result}"
        )
        assert len(mentions) == 2
        assert result["all_passed"] is True, result

        # Every selected file is preserved — none dropped to dodge the collision.
        gated = sorted(f for c in mentions for f in c.get("files", []))
        assert gated == sorted([_LIVE_TWIN, _PIN_TWIN]), gated

        # 0 collection errors: pytest exit 2 is a collection error, never a pass.
        for chk in mentions:
            assert chk["exit_code"] != 2, chk
            assert chk["passed"] is True, chk
            assert chk.get("partition_count") == 2, chk

        # Both tests actually execute (user-visible result, not just counts).
        live = next(c for c in mentions if _LIVE_TWIN in c["command"])
        pin = next(c for c in mentions if _PIN_TWIN in c["command"])
        assert "test_live" in (live["stdout_tail"] or ""), live
        assert "test_pin" in (pin["stdout_tail"] or ""), pin

    def test_failure_in_the_pin_partition_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A red in the pins/ partition blocks advancement."""
        _pin_env(monkeypatch, tmp_path)
        repo = _twin_repo(
            tmp_path,
            live_body="def test_live(): assert True\n",
            pin_body="def test_pin(): assert False, 'deliberate'\n",
        )
        result = _run_cli(repo)

        assert result["all_passed"] is False, result
        mentions = _mention_results(result)
        assert len(mentions) == 2
        pin = next(c for c in mentions if _PIN_TWIN in c["command"])
        live = next(c for c in mentions if _LIVE_TWIN in c["command"])
        assert pin["passed"] is False, pin
        assert live["passed"] is True, live

    def test_failure_in_the_regular_partition_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A red in the live partition blocks advancement too."""
        _pin_env(monkeypatch, tmp_path)
        repo = _twin_repo(
            tmp_path,
            live_body="def test_live(): assert False, 'deliberate'\n",
            pin_body="def test_pin(): assert True\n",
        )
        result = _run_cli(repo)

        assert result["all_passed"] is False, result
        mentions = _mention_results(result)
        assert len(mentions) == 2
        pin = next(c for c in mentions if _PIN_TWIN in c["command"])
        live = next(c for c in mentions if _LIVE_TWIN in c["command"])
        assert live["passed"] is False, live
        assert pin["passed"] is True, pin

    def test_control_unpartitioned_invocation_is_the_measured_collision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Control: one invocation carrying both twins dies at collection.

        This is the gh-resolve shape reproduced directly — it is what the
        partition exists to prevent.  A green here would mean the fixture no
        longer reproduces the defect and the other AC-1 cases prove nothing.
        """
        _pin_env(monkeypatch, tmp_path)
        repo = _twin_repo(
            tmp_path,
            live_body="def test_live(): assert True\n",
            pin_body="def test_pin(): assert True\n",
        )
        r = subprocess.run(
            ["python3", "-m", "pytest", _LIVE_TWIN, _PIN_TWIN, "-p", "no:cacheprovider"],
            cwd=str(repo), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        assert r.returncode != 0, (
            "the unpartitioned pair no longer collides — the prepend-mode "
            f"reproduction is gone\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
        combined = (r.stdout or "") + (r.stderr or "")
        assert (
            "import file mismatch" in combined
            or "import file" in combined.lower()
            or "error" in combined.lower()
        ), combined[-2000:]


# ── AC-2: declared coverage is parsed, not substring-matched ─────────────────


class TestDeclaredCoverage:
    def test_node_selector_does_not_cover_the_whole_file(self) -> None:
        whole, node_only, parseable = rlc._declared_command_coverage(
            "python3 -m pytest tests/test_a.py::test_one -q"
        )
        assert parseable is True
        assert whole == set(), whole
        assert node_only == {"tests/test_a.py"}, node_only

    def test_quoted_path_does_cover(self) -> None:
        whole, node_only, parseable = rlc._declared_command_coverage(
            'python3 -m pytest "tests/test_a.py" -q'
        )
        assert parseable is True
        assert whole == {"tests/test_a.py"}, whole

    def test_comment_does_not_cover(self) -> None:
        whole, _node, parseable = rlc._declared_command_coverage(
            "python3 -m pytest tests/test_b.py -q  # already runs tests/test_a.py"
        )
        assert parseable is True
        assert whole == {"tests/test_b.py"}, whole
        assert not rlc._covered_by("tests/test_a.py", whole)

    def test_lookalike_basename_does_not_cover(self) -> None:
        whole, _node, parseable = rlc._declared_command_coverage(
            "python3 -m pytest tests/test_a_longer.py -q"
        )
        assert parseable is True
        assert not rlc._covered_by("tests/test_a.py", whole), whole

    def test_substring_does_not_cover(self) -> None:
        whole, _node, parseable = rlc._declared_command_coverage(
            "python3 -m pytest skills/ilk-loop/tests/test_a.py -q"
        )
        assert parseable is True
        assert not rlc._covered_by("tests/test_a.py", whole), whole
        assert not rlc._covered_by("ilk-loop/tests/test_a.py", whole), whole

    def test_directory_arg_covers_its_subtree(self) -> None:
        whole, _node, parseable = rlc._declared_command_coverage(
            "python3 -m pytest tests/ -q"
        )
        assert parseable is True
        assert rlc._covered_by("tests/test_a.py", whole), whole
        assert not rlc._covered_by("skills/tests/test_a.py", whole), whole

    def test_bare_directory_arg_is_still_a_path(self) -> None:
        """``pytest tests`` names a directory — no separator needed."""
        whole, _node, parseable = rlc._declared_command_coverage(
            "python3 -m pytest tests -q"
        )
        assert parseable is True
        assert whole == {"tests"}, whole
        assert rlc._covered_by("tests/test_a.py", whole), whole

    def test_unparseable_command_leaves_coverage_unresolved(self) -> None:
        # Unbalanced quotes: we cannot know what it runs.
        assert rlc._declared_command_coverage(
            'python3 -m pytest "tests/test_a.py -q'
        ) == (set(), set(), False)
        # Unrecognised long option may swallow the next token.
        assert rlc._declared_command_coverage(
            "python3 -m pytest tests/test_a.py --sw -q"
        ) == (set(), set(), False)

    def test_unparseable_declared_command_does_not_omit_tests(
        self, tmp_path: Path,
    ) -> None:
        """Conservative end to end: an unparseable gate waives nothing."""
        repo = _setup_repo(
            tmp_path,
            base_files={
                _SRC: "def add(a, b): return a + b\n",
                "tests/test_a.py": _PIN_COMMENT + "def test_a(): assert True\n",
            },
            commit_files={_SRC: "def add(a, b): return a + b + 0\n"},
        )
        inventory = rlc.build_mention_gate_inventory(
            repo,
            changed_files=[_SRC],
            # The declared gate names the file, but cannot be parsed.
            existing_commands={'python3 -m pytest "tests/test_a.py --sw'},
        )
        files = [f for c in inventory for f in c.get("files", [])]
        assert "tests/test_a.py" in files, (
            f"an unparseable declared gate omitted the test it might run: {files}"
        )

    def test_whole_file_declared_gate_waives_only_that_file(
        self, tmp_path: Path,
    ) -> None:
        repo = _setup_repo(
            tmp_path,
            base_files={
                _SRC: "def add(a, b): return a + b\n",
                "tests/test_a.py": _PIN_COMMENT + "def test_a(): assert True\n",
                "tests/test_b.py": _PIN_COMMENT + "def test_b(): assert True\n",
            },
            commit_files={_SRC: "def add(a, b): return a + b + 0\n"},
        )
        inventory = rlc.build_mention_gate_inventory(
            repo,
            changed_files=[_SRC],
            existing_commands={"python3 -m pytest tests/test_a.py -q"},
        )
        files = [f for c in inventory for f in c.get("files", [])]
        assert "tests/test_a.py" not in files, files
        assert "tests/test_b.py" in files, files

    def test_node_selector_gate_keeps_the_file_gated(
        self, tmp_path: Path,
    ) -> None:
        repo = _setup_repo(
            tmp_path,
            base_files={
                _SRC: "def add(a, b): return a + b\n",
                "tests/test_a.py": _PIN_COMMENT + (
                    "def test_one(): assert True\n"
                    "def test_two(): assert True\n"
                ),
            },
            commit_files={_SRC: "def add(a, b): return a + b + 0\n"},
        )
        inventory = rlc.build_mention_gate_inventory(
            repo,
            changed_files=[_SRC],
            existing_commands={"python3 -m pytest tests/test_a.py::test_one -q"},
        )
        files = [f for c in inventory for f in c.get("files", [])]
        assert "tests/test_a.py" in files, (
            f"a ::node selector waived the whole file: {files}"
        )


# ── AC-3: one canonical inventory entry point ────────────────────────────────


class TestCanonicalInventory:
    def test_inventory_carries_command_scope_timeout_and_tree_identity(
        self, tmp_path: Path,
    ) -> None:
        repo = _twin_repo(
            tmp_path,
            live_body="def test_live(): assert True\n",
            pin_body="def test_pin(): assert True\n",
        )
        head = _git(repo, "rev-parse", "HEAD")
        inventory = rlc.build_mention_gate_inventory(
            repo, changed_files=[_SRC], existing_commands=set(),
        )
        assert len(inventory) == 2, inventory
        for chk in inventory:
            assert set(chk) >= {"command", "scope", "timeout", "tree_sha"}, chk
            assert chk["scope"] == "mention", chk
            assert chk["timeout"] == 300, chk
            assert chk["tree_sha"] == head, (chk, head)
            assert chk["partition_count"] == 2, chk

    def test_cli_result_agrees_with_the_inventory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Driver result and builder inventory name the same partitions."""
        _pin_env(monkeypatch, tmp_path)
        repo = _twin_repo(
            tmp_path,
            live_body="def test_live(): assert True\n",
            pin_body="def test_pin(): assert True\n",
        )
        result = _run_cli(repo)
        inventory = rlc.build_mention_gate_inventory(
            repo, changed_files=[_SRC], existing_commands=set(),
        )
        assert result["mention_check_count"] == len(inventory), (
            result["mention_check_count"], len(inventory)
        )
        cli_cmds = sorted(c["command"] for c in _mention_results(result))
        inv_cmds = sorted(c["command"] for c in inventory)
        assert cli_cmds == inv_cmds, (cli_cmds, inv_cmds)

    def test_alias_is_the_same_builder(self) -> None:
        """The compatibility alias is the canonical entry point, not a fork."""
        assert rlc._synthesize_mention_check is rlc.build_mention_gate_inventory


# ── AC-4: plan lint on declared pytest commands ──────────────────────────────


def _plan_with_declared(command: str) -> str:
    return f"""---
plan: sample
status: pending
estimated_steps: 1
---

# Sub-plan: sample

### Step 0 — work
```yaml
local_checks:
  - command: "{command}"
    timeout: 30
```
"""


class TestDuplicateImportBasenameLint:
    def test_flags_duplicate_basenames_in_prepend_project(self, tmp_path: Path) -> None:
        findings = plan_lint.lint_duplicate_import_basename(
            _plan_with_declared(
                "python3 -m pytest tests/pins/test_a.py tests/test_a.py -q"
            ),
            "sample",
            project_root=tmp_path,
        )
        assert len(findings) == 1, findings
        assert "test_a.py" in findings[0], findings[0]
        assert "Do not drop a file" in findings[0], findings[0]

    def test_accepts_importlib_project_config(self, tmp_path: Path) -> None:
        (tmp_path / "pytest.ini").write_text(
            "[pytest]\naddopts = --import-mode=importlib --timeout=60\n",
            encoding="utf-8",
        )
        findings = plan_lint.lint_duplicate_import_basename(
            _plan_with_declared(
                "python3 -m pytest tests/pins/test_a.py tests/test_a.py -q"
            ),
            "sample",
            project_root=tmp_path,
        )
        assert findings == [], findings

    def test_accepts_importlib_on_the_command(self, tmp_path: Path) -> None:
        findings = plan_lint.lint_duplicate_import_basename(
            _plan_with_declared(
                "python3 -m pytest tests/pins/test_a.py tests/test_a.py "
                "--import-mode=importlib -q"
            ),
            "sample",
            project_root=tmp_path,
        )
        assert findings == [], findings

    def test_command_import_mode_override_still_flags(self, tmp_path: Path) -> None:
        """An explicit prepend on the command beats a compatible project config."""
        (tmp_path / "pytest.ini").write_text(
            "[pytest]\naddopts = --import-mode=importlib\n", encoding="utf-8"
        )
        findings = plan_lint.lint_duplicate_import_basename(
            _plan_with_declared(
                "python3 -m pytest tests/pins/test_a.py tests/test_a.py "
                "--import-mode=prepend -q"
            ),
            "sample",
            project_root=tmp_path,
        )
        assert len(findings) == 1, findings

    def test_accepts_vitest(self, tmp_path: Path) -> None:
        findings = plan_lint.lint_duplicate_import_basename(
            _plan_with_declared(
                "npx vitest run tests/pins/test_a.ts tests/test_a.ts"
            ),
            "sample",
            project_root=tmp_path,
        )
        assert findings == [], findings

    def test_distinct_basenames_are_not_flagged(self, tmp_path: Path) -> None:
        findings = plan_lint.lint_duplicate_import_basename(
            _plan_with_declared(
                "python3 -m pytest tests/pins/test_a.py tests/test_b.py -q"
            ),
            "sample",
            project_root=tmp_path,
        )
        assert findings == [], findings

    def test_this_toolkit_own_config_is_supported(self) -> None:
        """The toolkit's importlib pytest.ini must keep its own gates legal."""
        toolkit_root = Path(__file__).resolve().parents[3]
        assert plan_lint._project_allows_duplicate_basenames(toolkit_root) is True
        findings = plan_lint.lint_duplicate_import_basename(
            _plan_with_declared(
                "python3 -m pytest tests/test_a.py tests/pins/test_a.py -q"
            ),
            "sample",
            project_root=toolkit_root,
        )
        assert findings == [], findings


# ── AC-5: the cap policy is unchanged ────────────────────────────────────────


class TestMentionPolicy:
    def test_over_cap_still_skips_with_an_explicit_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        base = {
            _SRC: "def add(a, b): return a + b\n",
        }
        for i in range(rlc._MENTION_GATE_CAP + 3):
            base[f"tests/test_many_{i:02d}.py"] = (
                _PIN_COMMENT + f"def test_{i}(): assert True\n"
            )
        repo = _setup_repo(
            tmp_path,
            base_files=base,
            commit_files={_SRC: "def add(a, b): return a + b + 0\n"},
        )
        inventory = rlc.build_mention_gate_inventory(
            repo, changed_files=[_SRC], existing_commands=set(),
        )
        assert inventory == [], inventory
        err = capsys.readouterr().err
        assert "over cap, not gated" in err, err

    def test_partitions_are_not_dropped_to_meet_the_cap(
        self, tmp_path: Path,
    ) -> None:
        """Three files sharing one basename stay three partitions under the cap."""
        base = {_SRC: "def add(a, b): return a + b\n"}
        bodies = {
            "tests/test_twins.py": "def test_live(): assert True\n",
            "tests/pins/test_twins.py": "def test_pin(): assert True\n",
            "tests/extra/test_twins.py": "def test_extra(): assert True\n",
        }
        for rel, body in bodies.items():
            base[rel] = _PIN_COMMENT + body
        repo = _setup_repo(
            tmp_path,
            base_files=base,
            commit_files={_SRC: "def add(a, b): return a + b + 0\n"},
        )
        inventory = rlc.build_mention_gate_inventory(
            repo, changed_files=[_SRC], existing_commands=set(),
        )
        assert len(inventory) == 3, inventory
        gated = sorted(f for c in inventory for f in c["files"])
        assert gated == sorted(bodies), gated
        # Each partition stays collision-free on its own.
        for chk in inventory:
            bases = [os.path.basename(f) for f in chk["files"]]
            assert len(bases) == len(set(bases)), chk
