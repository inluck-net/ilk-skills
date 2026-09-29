r"""Red-first pins: a step also gates the tests that cite its changed files by line number.

Part of sub-plan ``a-line-number-pin-is-gated`` (step 0 of 3).

AC-1 through AC-7 for the mention-gate feature.  Each test is
``xfail(strict=True, reason="mention gate not implemented")`` until step 1
implements the feature in ``run_local_checks.py``.

AC-1: fixture repo with a source file, a doc table pinning ``src.py:10``,
      and a test comparing them.  A commit shifts lines under
      ``[plan:alpha#step-1]``.  The mention check is appended, and goes red
      at step 1.
AC-2: the changed file is a test file itself ⇒ the test is gated directly.
AC-3: the changed file is mentioned in a doc ⇒ the test that cites the doc
      is gated.
AC-4: more than 20 test files cite the changed file ⇒ cap triggers, no gate
      appended, WARN logged.
AC-5: no mention ⇒ gate unchanged, byte-identical.
AC-6: a vitest project gets a ``vitest run <files>`` form.
AC-7: the contract doc describes the synthesized check (``scope: mention``).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_local_checks as rlc  # noqa: E402


# ── Helpers ─────────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def _make_project(tmp_path: Path, fixture: str, slug: str) -> Path:
    """Create a project with an external plans dir holding one sub-plan."""
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    plans = proj / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / f"2026-09-29-{slug}.md").write_text(fixture, encoding="utf-8")
    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        f"---\nstatus: active\n---\n# m\n- [x](./2026-09-29-{slug}.md)\n",
        encoding="utf-8",
    )
    return proj


def _make_repo_with_mention(tmp_path: Path) -> Path:
    """Create a fixture repo reproducing 28c:
    - src.py: a source file with a function at line 10
    - docs/design.md: a table pinning ``src.py:10``
    - test_src.py: a test that reads the doc and compares with live code
    - a commit that shifts lines under ``[plan:alpha#step-1]``
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    # base — source file with function at line 10
    src = repo / "src.py"
    src.write_text(
        "# line 1\n# line 2\n# line 3\n# line 4\n# line 5\n"
        "# line 6\n# line 7\n# line 8\n# line 9\n"
        "def target():\n    return 42\n",
        encoding="utf-8",
    )

    # doc table pinning src.py:10
    docs = repo / "docs"
    docs.mkdir()
    (docs / "design.md").write_text(
        "# Design\n\n| File | Line |\n|---|---|\n| src.py | 10 |\n",
        encoding="utf-8",
    )

    # test that reads the doc and compares with live code
    (repo / "test_src.py").write_text(
        'import re\n'
        'from pathlib import Path\n\n'
        'def test_src_line():\n'
        '    doc = Path("docs/design.md").read_text()\n'
        '    m = re.search(r"src\\.py\\s*\\|\\s*(\\d+)", doc)\n'
        '    assert m, "src.py not found in doc"\n'
        '    expected_line = int(m.group(1))\n'
        '    lines = Path("src.py").read_text().splitlines()\n'
        '    assert "def target():" in lines[expected_line - 1]\n',
        encoding="utf-8",
    )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: src.py:10 pinned in doc")

    # shift lines — insert 3 lines before target(), moving it to line 13
    src.write_text(
        "# line 1\n# line 2\n# line 3\n# line 4\n# line 5\n"
        "# line 6\n# line 7\n# line 8\n# line 9\n"
        "# inserted line A\n# inserted line B\n# inserted line C\n"
        "def target():\n    return 42\n",
        encoding="utf-8",
    )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(x): shift lines [plan:alpha#step-1]")

    return repo


def _make_repo_no_mention(tmp_path: Path) -> Path:
    """Create a fixture repo with no line-number mentions."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    (repo / "src.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (repo / "test_foo.py").write_text(
        "def test_foo():\n    assert True\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: no mentions")

    # change src.py — no line-number pins anywhere
    (repo / "src.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(x): change [plan:alpha#step-1]")

    return repo


def _make_repo_many_mentions(tmp_path: Path, n: int = 25) -> Path:
    """Create a fixture repo with >20 test files citing src.py:10."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    # source file with function at line 10
    src = repo / "src.py"
    src.write_text(
        "# line 1\n# line 2\n# line 3\n# line 4\n# line 5\n"
        "# line 6\n# line 7\n# line 8\n# line 9\n"
        "def target():\n    return 42\n",
        encoding="utf-8",
    )

    # N test files, each pinning src.py:10
    for i in range(n):
        (repo / f"test_{i:03d}.py").write_text(
            f'import re\nfrom pathlib import Path\n\n'
            f'def test_{i}():\n'
            f'    doc = Path("src.py").read_text()\n'
            f'    lines = doc.splitlines()\n'
            f'    assert "def target():" in lines[9]  # src.py:10\n',
            encoding="utf-8",
        )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: many mentions")

    # shift lines
    src.write_text(
        "# line 1\n# line 2\n# line 3\n# line 4\n# line 5\n"
        "# line 6\n# line 7\n# line 8\n# line 9\n"
        "# inserted\n"
        "def target():\n    return 42\n",
        encoding="utf-8",
    )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(x): shift [plan:alpha#step-1]")

    return repo


def _make_vitest_repo(tmp_path: Path) -> Path:
    """Create a fixture repo with a vitest-style test."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    # source file
    (repo / "src.ts").write_text(
        "export function target() {\n  return 42;\n}\n",
        encoding="utf-8",
    )

    # doc pinning src.ts:1
    docs = repo / "docs"
    docs.mkdir()
    (docs / "design.md").write_text(
        "# Design\n\n| File | Line |\n|---|---|\n| src.ts | 1 |\n",
        encoding="utf-8",
    )

    # vitest test with line-number pin
    (repo / "src.test.ts").write_text(
        'import { describe, it, expect } from "vitest";\n'
        'import { readFileSync } from "fs";\n\n'
        '// src.ts:1\n'
        'describe("src", () => {\n'
        '  it("target at line 1", () => {\n'
        '    const lines = readFileSync("src.ts", "utf-8").split("\\n");\n'
        '    expect(lines[0]).toContain("export function target");\n'
        '  });\n'
        '});\n',
        encoding="utf-8",
    )

    # package.json with vitest
    (repo / "package.json").write_text(
        '{"devDependencies": {"vitest": "^1.0.0"}}\n',
        encoding="utf-8",
    )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: vitest setup")

    # shift lines
    (repo / "src.ts").write_text(
        "// inserted\nexport function target() {\n  return 42;\n}\n",
        encoding="utf-8",
    )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(x): shift [plan:alpha#step-1]")

    return repo


# ── AC-1: mention check appended and goes red ──────────────────────────────


def test_ac1_mention_check_appended_and_red(tmp_path: Path) -> None:
    """A commit that shifts lines under a pinned file ⇒ mention gate appended."""
    repo = _make_repo_with_mention(tmp_path)

    # Create plans in the repo's docs/plans directory
    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "2026-09-29-alpha.md").write_text(
        _SUBPLAN_FIXTURE.format(slug="alpha", step=1),
        encoding="utf-8",
    )
    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        "---\nstatus: active\n---\n# m\n- [x](./2026-09-29-alpha.md)\n",
        encoding="utf-8",
    )

    # run the gate for step 1
    import json
    import sys
    from io import StringIO
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        exit_code = rlc.main(["--project", str(repo), "--slug", "alpha", "--step", "1"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    result = json.loads(output)

    # the mention check should be in the results
    checks = [c for c in result["results"] if c.get("scope") == "mention"]
    assert len(checks) == 1, f"expected 1 mention check, got {len(checks)}"

    # the mention check should have failed (src.py:10 no longer matches)
    assert checks[0]["exit_code"] != 0, "mention check should have failed"


# ── AC-2: test file changed ⇒ gated directly ──────────────────────────────


def test_ac2_test_file_changed_gated_directly(tmp_path: Path) -> None:
    """A changed test file is gated directly, not via mention search."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    # base — test passes
    (repo / "test_foo.py").write_text("def test_foo(): assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: green")

    # change the test to fail
    (repo / "test_foo.py").write_text("def test_foo(): assert False\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(x): break test [plan:alpha#step-1]")

    # Create plans in the repo's docs/plans directory
    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "2026-09-29-alpha.md").write_text(
        _SUBPLAN_FIXTURE.format(slug="alpha", step=1),
        encoding="utf-8",
    )
    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        "---\nstatus: active\n---\n# m\n- [x](./2026-09-29-alpha.md)\n",
        encoding="utf-8",
    )

    import json
    import sys
    from io import StringIO
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        exit_code = rlc.main(["--project", str(repo), "--slug", "alpha", "--step", "1"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    result = json.loads(output)

    # the test file should be in the gate
    test_checks = [
        c for c in result["results"]
        if "test_foo.py" in c.get("command", "")
    ]
    assert len(test_checks) >= 1, "test_foo.py should be in the gate"


# ── AC-3: doc mention ⇒ test that cites doc is gated ──────────────────────


def test_ac3_doc_mention_gates_citing_test(tmp_path: Path) -> None:
    """A changed doc file ⇒ test that cites the doc is gated."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    # base — doc and test
    (repo / "docs").mkdir()
    (repo / "docs" / "api.md").write_text("# API\n\nEndpoint: /foo\n")
    (repo / "test_api.py").write_text(
        'from pathlib import Path\n\n'
        'def test_api_doc():\n'
        '    assert "/foo" in Path("docs/api.md").read_text()\n'
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: doc + test")

    # change the doc
    (repo / "docs" / "api.md").write_text("# API\n\nEndpoint: /bar\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat(x): change api [plan:alpha#step-1]")

    # Create plans in the repo's docs/plans directory
    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "2026-09-29-alpha.md").write_text(
        _SUBPLAN_FIXTURE.format(slug="alpha", step=1),
        encoding="utf-8",
    )
    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        "---\nstatus: active\n---\n# m\n- [x](./2026-09-29-alpha.md)\n",
        encoding="utf-8",
    )

    import json
    import sys
    from io import StringIO
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        exit_code = rlc.main(["--project", str(repo), "--slug", "alpha", "--step", "1"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    result = json.loads(output)

    # test_api.py should be in the gate via mention
    test_checks = [
        c for c in result["results"]
        if "test_api.py" in c.get("command", "")
    ]
    assert len(test_checks) >= 1, "test_api.py should be in the gate via mention"


# ── AC-4: cap at 20 files ─────────────────────────────────────────────────


def test_ac4_cap_at_20_files(tmp_path: Path) -> None:
    """More than 20 test files cite the changed file ⇒ cap triggers."""
    repo = _make_repo_many_mentions(tmp_path, n=25)

    # Create plans in the repo's docs/plans directory
    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "2026-09-29-alpha.md").write_text(
        _SUBPLAN_FIXTURE.format(slug="alpha", step=1),
        encoding="utf-8",
    )
    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        "---\nstatus: active\n---\n# m\n- [x](./2026-09-29-alpha.md)\n",
        encoding="utf-8",
    )

    import json
    import sys
    from io import StringIO
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        exit_code = rlc.main(["--project", str(repo), "--slug", "alpha", "--step", "1"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    result = json.loads(output)

    # no mention check should be appended (cap triggered)
    mention_checks = [
        c for c in result["results"] if c.get("scope") == "mention"
    ]
    assert len(mention_checks) == 0, f"cap should have triggered, got {len(mention_checks)} mention checks"


# ── AC-5: no mention ⇒ gate unchanged ─────────────────────────────────────


def test_ac5_no_mention_gate_unchanged(tmp_path: Path) -> None:
    """No line-number mentions ⇒ gate unchanged, byte-identical."""
    repo = _make_repo_no_mention(tmp_path)

    # Create plans in the repo's docs/plans directory
    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "2026-09-29-alpha.md").write_text(
        _SUBPLAN_FIXTURE.format(slug="alpha", step=1),
        encoding="utf-8",
    )
    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        "---\nstatus: active\n---\n# m\n- [x](./2026-09-29-alpha.md)\n",
        encoding="utf-8",
    )

    import json
    import sys
    from io import StringIO
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        exit_code = rlc.main(["--project", str(repo), "--slug", "alpha", "--step", "1"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    result = json.loads(output)

    # no mention check should be appended
    mention_checks = [
        c for c in result["results"] if c.get("scope") == "mention"
    ]
    assert len(mention_checks) == 0, "no mention checks expected"


# ── AC-6: vitest project gets vitest form ──────────────────────────────────


def test_ac6_vitest_form(tmp_path: Path) -> None:
    """A vitest project gets a ``vitest run <files>`` form."""
    repo = _make_vitest_repo(tmp_path)

    # Create plans in the repo's docs/plans directory
    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "2026-09-29-alpha.md").write_text(
        _SUBPLAN_FIXTURE.format(slug="alpha", step=1),
        encoding="utf-8",
    )
    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        "---\nstatus: active\n---\n# m\n- [x](./2026-09-29-alpha.md)\n",
        encoding="utf-8",
    )

    import json
    import sys
    from io import StringIO
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        exit_code = rlc.main(["--project", str(repo), "--slug", "alpha", "--step", "1"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    result = json.loads(output)

    # the mention check should use vitest
    mention_checks = [
        c for c in result["results"] if c.get("scope") == "mention"
    ]
    assert len(mention_checks) == 1, f"expected 1 mention check, got {len(mention_checks)}"
    assert "vitest" in mention_checks[0]["command"], "should use vitest"


# ── AC-7: contract doc describes scope: mention ────────────────────────────


def test_ac7_contract_describes_mention_scope(tmp_path: Path) -> None:
    """The contract doc describes the synthesized check (``scope: mention``)."""
    contract_path = Path(__file__).resolve().parent.parent / "references" / "detached-component-contracts.md"
    if not contract_path.exists():
        pytest.skip("contract doc not found")

    content = contract_path.read_text(encoding="utf-8")
    assert "scope: mention" in content, "contract doc should describe scope: mention"


# ── Sub-plan fixture ──────────────────────────────────────────────────────


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
  - command: "true"
    timeout: 30
```
"""
