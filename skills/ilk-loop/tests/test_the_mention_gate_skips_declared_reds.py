r"""Red-first pins: the mention gate excuses exactly the declared baseline_red nodes.

Part of sub-plan ``the-mention-gate-skips-declared-reds`` (step 0 of 2).

AC-1, AC-2, and AC-7 are ``xfail(strict=True)`` until step 1
implements the excuse logic in ``run_local_checks.py``.  AC-4 through
AC-6 are plain tests that verify the current control behavior.

AC-1: declared node excused end to end — the mention check passes and
      records ``excused_declared_reds``.
AC-2: whole-file declaration drops the file from the mention set.
AC-3: a prefix sibling (name starts with declared id) is NOT excused.
AC-4: an undeclared red fails (current behavior, no baseline_red).
AC-5: no baseline_red ⇒ the mention command has no ``-rfE``.
AC-6: exit 2 (collection error) is never excused.
AC-7: the excuse is visible in stdout.
"""
from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO
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


def _setup_repo(
    tmp_path: Path,
    launch_json: dict,
    slug: str = "alpha",
    step: int = 1,
    *,
    base_files: dict[str, str] | None = None,
    commit_files: dict[str, str] | None = None,
) -> Path:
    """Create a tmp git repo with .ilk-launch.json, plans, and a step commit.

    ``base_files`` are committed first (the "base" commit).
    ``commit_files`` are committed with the step trailer.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    # base commit
    if base_files:
        for rel, content in base_files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: setup")

    # step commit with trailer
    if commit_files:
        for rel, content in commit_files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"feat(x): change [plan:{slug}#step-{step}]")

    # .ilk-launch.json
    (repo / ".ilk-launch.json").write_text(
        json.dumps(launch_json, indent=2), encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: add launch config")

    # plans
    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / f"2026-09-29-{slug}.md").write_text(
        _SUBPLAN_FIXTURE.format(slug=slug, step=step),
        encoding="utf-8",
    )
    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        f"---\nstatus: active\n---\n# m\n- [x](./2026-09-29-{slug}.md)\n",
        encoding="utf-8",
    )

    return repo


def _run(repo: Path, slug: str = "alpha", step: int = 1) -> dict:
    """Run rlc.main and return the parsed JSON result."""
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        rlc.main(["--project", str(repo), "--slug", slug, "--step", str(step)])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return json.loads(output)


# ── AC-1: declared node excused, end to end ─────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="mention gate does not yet excuse declared baseline_red nodes",
)
def test_ac1_declared_node_excused(tmp_path: Path) -> None:
    """A declared baseline_red node is excused by the mention gate."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            },
            "baseline_red": [
                {"node_id": "test_foo.py::test_declared_red", "reason": "r"},
            ],
        },
    }
    repo = _setup_repo(
        tmp_path, launch,
        base_files={"test_foo.py": "def test_green(): assert True\n"},
        commit_files={
            "test_foo.py": (
                "def test_declared_red(): assert False\n"
                "def test_green(): assert True\n"
            ),
        },
    )

    result = _run(repo)
    checks = [c for c in result["results"] if c.get("scope") == "mention"]
    assert len(checks) == 1, f"expected 1 mention check, got {len(checks)}"
    assert checks[0]["passed"] is True
    assert checks[0]["excused_declared_reds"] == [
        "test_foo.py::test_declared_red"
    ]
    assert result["all_passed"] is True


# ── AC-2: whole-file declaration drops the file ─────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="mention gate does not yet drop whole-file declared reds",
)
def test_ac2_whole_file_drops_file(tmp_path: Path) -> None:
    """A whole-file baseline_red entry drops that file from the mention set."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            },
            "baseline_red": [
                {"node_id": "test_foo.py", "reason": "r"},
            ],
        },
    }
    repo = _setup_repo(
        tmp_path, launch,
        base_files={
            "test_foo.py": "def test_foo(): assert True\n",
            "test_bar.py": "def test_bar(): assert True\n",
        },
        commit_files={
            "test_foo.py": "def test_foo(): assert False\n",
            "test_bar.py": "def test_bar(): assert True\n",
        },
    )

    result = _run(repo)
    checks = [c for c in result["results"] if c.get("scope") == "mention"]
    assert len(checks) == 1
    # test_bar.py should be in the command, not test_foo.py
    cmd = checks[0]["command"]
    assert "test_bar.py" in cmd, f"test_bar.py should be in command: {cmd}"
    assert "test_foo.py" not in cmd, f"test_foo.py should be dropped: {cmd}"


@pytest.mark.xfail(
    strict=True,
    reason="mention gate does not yet drop whole-file declared reds",
)
def test_ac2_whole_file_only_changed_file(tmp_path: Path) -> None:
    """When the only changed file is a whole-file declaration, no mention check."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            },
            "baseline_red": [
                {"node_id": "test_foo.py", "reason": "r"},
            ],
        },
    }
    repo = _setup_repo(
        tmp_path, launch,
        base_files={"test_foo.py": "def test_foo(): assert True\n"},
        commit_files={"test_foo.py": "def test_foo(): assert False\n"},
    )

    result = _run(repo)
    assert result["mention_check_count"] == 0


# ── AC-3: prefix sibling is NOT excused ─────────────────────────────────────


def test_ac3_prefix_sibling_not_excused(tmp_path: Path) -> None:
    """A failing test whose name starts with a declared id is NOT excused."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            },
            "baseline_red": [
                {"node_id": "test_foo.py::test_declared_red", "reason": "r"},
            ],
        },
    }
    repo = _setup_repo(
        tmp_path, launch,
        base_files={"test_foo.py": "def test_green(): assert True\n"},
        commit_files={
            "test_foo.py": (
                "def test_declared_red(): assert False\n"
                "def test_declared_red_too(): assert False\n"
                "def test_green(): assert True\n"
            ),
        },
    )

    result = _run(repo)
    checks = [c for c in result["results"] if c.get("scope") == "mention"]
    assert len(checks) == 1
    assert checks[0]["passed"] is False
    assert result["all_passed"] is False
    excused = checks[0].get("excused_declared_reds", [])
    assert "test_foo.py::test_declared_red_too" not in excused


# ── AC-4: undeclared red fails ──────────────────────────────────────────────


def test_ac4_undeclared_red_fails(tmp_path: Path) -> None:
    """An undeclared failing test causes the mention check to fail."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            },
        },
    }
    repo = _setup_repo(
        tmp_path, launch,
        base_files={"test_foo.py": "def test_foo(): assert True\n"},
        commit_files={
            "test_foo.py": (
                "def test_foo(): assert True\n"
                "def test_other(): assert False\n"
            ),
        },
    )

    result = _run(repo)
    checks = [c for c in result["results"] if c.get("scope") == "mention"]
    assert len(checks) == 1
    assert checks[0]["passed"] is False
    assert result["all_passed"] is False


# ── AC-5: no baseline_red ⇒ unchanged command ──────────────────────────────


def test_ac5_no_baseline_red_key(tmp_path: Path) -> None:
    """With no baseline_red key, the mention command has no -rfE."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            },
        },
    }
    repo = _setup_repo(
        tmp_path, launch,
        base_files={"test_foo.py": "def test_foo(): assert True\n"},
        commit_files={"test_foo.py": "def test_foo(): assert False\n"},
    )

    result = _run(repo)
    checks = [c for c in result["results"] if c.get("scope") == "mention"]
    assert len(checks) == 1
    assert checks[0]["command"] == "python3 -m pytest -q -p no:cacheprovider test_foo.py"


def test_ac5_empty_baseline_red(tmp_path: Path) -> None:
    """With an empty baseline_red list, the mention command has no -rfE."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            },
            "baseline_red": [],
        },
    }
    repo = _setup_repo(
        tmp_path, launch,
        base_files={"test_foo.py": "def test_foo(): assert True\n"},
        commit_files={"test_foo.py": "def test_foo(): assert False\n"},
    )

    result = _run(repo)
    checks = [c for c in result["results"] if c.get("scope") == "mention"]
    assert len(checks) == 1
    assert checks[0]["command"] == "python3 -m pytest -q -p no:cacheprovider test_foo.py"


# ── AC-6: exit 2 is never excused ──────────────────────────────────────────


def test_ac6_exit_2_not_excused(tmp_path: Path) -> None:
    """A collection error (exit 2) is never excused, even with a declared red."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            },
            "baseline_red": [],
        },
    }
    repo = _setup_repo(
        tmp_path, launch,
        base_files={"test_foo.py": "def test_foo(): assert True\n"},
        commit_files={
            "test_foo.py": "def test_foo(): assert True\n",
            "test_broken.py": "import nonexistent_module\n",
        },
    )

    result = _run(repo)
    checks = [c for c in result["results"] if c.get("scope") == "mention"]
    assert len(checks) == 1
    assert checks[0]["exit_code"] == 2
    assert result["all_passed"] is False


# ── AC-7: excuse visible in stdout ─────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="mention gate does not yet output excuse lines",
)
def test_ac7_excuse_visible_in_stdout(tmp_path: Path) -> None:
    """The excuse is printed to stdout."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            },
            "baseline_red": [
                {"node_id": "test_foo.py::test_declared_red", "reason": "r"},
            ],
        },
    }
    repo = _setup_repo(
        tmp_path, launch,
        base_files={"test_foo.py": "def test_green(): assert True\n"},
        commit_files={
            "test_foo.py": (
                "def test_declared_red(): assert False\n"
                "def test_green(): assert True\n"
            ),
        },
    )

    # capture both stdout and the rlc output
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        rlc.main(["--project", str(repo), "--slug", "alpha", "--step", "1"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert "mention: excused 1 declared baseline_red node(s):" in output
    assert "test_foo.py::test_declared_red" in output