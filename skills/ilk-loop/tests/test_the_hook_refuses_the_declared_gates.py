"""Pin that the hook refuses the gates the driver owns.

AC-1: with ILK_DECLARED_GATES_FILE holding `bun run test:non-ui:convex`,
      the hook on that exact command and on `cd x && bun run test:non-ui:convex`
      ⇒ deny.

AC-2: each of the 7 formerly-allowed commands ⇒ deny.
      `python3 -m pytest tests/test_x.py -q`, `bun test src/a.test.ts`,
      `npm run lint` ⇒ allow.

AC-3: with no ILK_DECLARED_GATES_FILE, a non-runner command
      (`ls`, `git status`) ⇒ allow, unchanged.

AC-4 (runtime): the real main() with a stub claude that writes
      $ILK_DECLARED_GATES_FILE and that file's contents to a capture file,
      over a fixture sub-plan whose step 0 declares one gate and whose
      frontmatter declares another ⇒ the variable was set during the agent
      call and the file held exactly those two commands.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-full-suite.sh"

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_DRIVER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ilk_paths  # noqa: E402
import shlex  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _run_hook(command: str, env: dict[str, str] | None = None) -> dict:
    """Run the hook with a synthetic Bash event and return the parsed JSON output."""
    event = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
    run_env = os.environ.copy()
    run_env.pop("ILK_ALLOW_FULL_SUITE", None)
    # Clear declared gates unless caller sets it
    run_env.pop("ILK_DECLARED_GATES_FILE", None)
    if env:
        run_env.update(env)
    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=event,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        env=run_env,
        timeout=10,
    )
    assert result.returncode == 0, f"hook exited {result.returncode}: {result.stderr}"
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


def _make_gates_file(tmp_path: Path, commands: list[str]) -> Path:
    """Write a declared-gates file and return its path."""
    p = tmp_path / "declared-gates-0.txt"
    p.write_text("\n".join(commands) + "\n", encoding="utf-8")
    return p


# ── AC-1: declared gate ⇒ deny ───────────────────────────────────────────────

class TestAC1DeclaredGateDenies:
    """With ILK_DECLARED_GATES_FILE, a command matching a declared gate is denied."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_exact_match_denied(self, tmp_path: Path) -> None:
        """AC-1: `bun run test:non-ui:convex` in gates file ⇒ deny."""
        gates = _make_gates_file(tmp_path, ["bun run test:non-ui:convex"])
        result = _run_hook(
            "bun run test:non-ui:convex",
            env={"ILK_DECLARED_GATES_FILE": str(gates)},
        )
        assert result["allowed"] is False
        assert "driver runs" in result["payload"]["hookSpecificOutput"]["permissionDecisionReason"]

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_cd_prefix_match_denied(self, tmp_path: Path) -> None:
        """AC-1: `cd x && bun run test:non-ui:convex` ⇒ deny."""
        gates = _make_gates_file(tmp_path, ["bun run test:non-ui:convex"])
        result = _run_hook(
            "cd x && bun run test:non-ui:convex",
            env={"ILK_DECLARED_GATES_FILE": str(gates)},
        )
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_whitespace_normalized_match(self, tmp_path: Path) -> None:
        """AC-1: extra whitespace in command still matches gate."""
        gates = _make_gates_file(tmp_path, ["bun run test:non-ui:convex"])
        result = _run_hook(
            "bun  run   test:non-ui:convex",
            env={"ILK_DECLARED_GATES_FILE": str(gates)},
        )
        assert result["allowed"] is False


# ── AC-2: formerly-allowed commands now denied ───────────────────────────────

_FORMERLY_ALLOWED = [
    "bun run test",
    "bun run test:unit",
    "npm run test:unit",
    "npx vitest run",
    "vitest run",
    "make test",
    "cd tests && pytest -q",
]

# Already caught by the existing runner list (no xfail needed).
_ALREADY_CAUGHT = [
    "bun test",
]

# Commands that should still be allowed even with declared gates
_STILL_ALLOWED = [
    "python3 -m pytest tests/test_x.py -q",
    "bun test src/a.test.ts",
    "npm run lint",
]


class TestAC2FormerlyAllowedNowDenied:
    """Each formerly-allowed command ⇒ deny when in declared gates."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    @pytest.mark.parametrize("cmd", _FORMERLY_ALLOWED)
    def test_formerly_allowed_denied(self, tmp_path: Path, cmd: str) -> None:
        """AC-2: `%(cmd)s` in gates file ⇒ deny."""
        gates = _make_gates_file(tmp_path, [cmd])
        result = _run_hook(cmd, env={"ILK_DECLARED_GATES_FILE": str(gates)})
        assert result["allowed"] is False, f"{cmd!r} should be denied but was allowed"

    @pytest.mark.parametrize("cmd", _STILL_ALLOWED)
    def test_scoped_still_allowed(self, tmp_path: Path, cmd: str) -> None:
        """AC-2: `%(cmd)s` ⇒ allow (scoped / non-gate)."""
        gates = _make_gates_file(tmp_path, _FORMERLY_ALLOWED)
        result = _run_hook(cmd, env={"ILK_DECLARED_GATES_FILE": str(gates)})
        assert result["allowed"] is True, f"{cmd!r} should be allowed but was denied"

    @pytest.mark.parametrize("cmd", _ALREADY_CAUGHT)
    def test_already_caught_by_runner_list(self, cmd: str) -> None:
        """`%(cmd)s` is already denied by the existing runner list — not a new denial."""
        result = _run_hook(cmd)
        assert result["allowed"] is False, f"{cmd!r} should already be denied by runner list"


# ── AC-3: no gates file ⇒ unchanged behaviour ───────────────────────────────

class TestAC3NoGatesFileUnchanged:
    """Without ILK_DECLARED_GATES_FILE, non-runner commands are allowed."""

    def test_ls_allowed(self) -> None:
        """AC-3: `ls` without gates file ⇒ allow."""
        result = _run_hook("ls -la")
        assert result["allowed"] is True

    def test_git_status_allowed(self) -> None:
        """AC-3: `git status` without gates file ⇒ allow."""
        result = _run_hook("git status")
        assert result["allowed"] is True

    def test_empty_gates_file_no_effect(self, tmp_path: Path) -> None:
        """AC-3: empty gates file ⇒ same as no file."""
        gates = _make_gates_file(tmp_path, [])
        result = _run_hook("ls", env={"ILK_DECLARED_GATES_FILE": str(gates)})
        assert result["allowed"] is True

    def test_runner_still_denied_without_gates(self) -> None:
        """AC-3: bare pytest without gates file ⇒ still denied by the runner list."""
        result = _run_hook("pytest")
        assert result["allowed"] is False

    def test_runner_still_denied_with_empty_gates(self, tmp_path: Path) -> None:
        """AC-3: bare pytest with empty gates file ⇒ still denied by runner list."""
        gates = _make_gates_file(tmp_path, [])
        result = _run_hook("pytest", env={"ILK_DECLARED_GATES_FILE": str(gates)})
        assert result["allowed"] is False


# ── AC-4 (runtime): runner exports ILK_DECLARED_GATES_FILE ──────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


class _scoped_data_home:
    def __init__(self, data_home: Path) -> None:
        self._data_home = data_home
        self._prev: str | None = None

    def __enter__(self) -> Path:
        self._prev = os.environ.get("ILK_DATA_HOME")
        os.environ["ILK_DATA_HOME"] = str(self._data_home)
        return self._data_home

    def __exit__(self, *exc: object) -> None:
        if self._prev is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = self._prev


SLUG = "declared-gates-test"
STEM = f"2026-09-29-{SLUG}"


def _build_world(root: Path) -> dict:
    """A project + isolated data home + a stub claude that captures env."""
    project = root / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    # Sub-plan: step 0 declares one gate, frontmatter declares another
    (plans / f"MASTER-{SLUG}-execution-plan.md").write_text(
        "---\n"
        f"master_plan: {SLUG}-execution\n"
        f"batch_date: 2026-09-29\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "verification_tier: loop-verified\n"
        "local_checks:\n"
        '  - command: "echo frontmatter-gate"\n'
        "    timeout: 30\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "## Steps\n\n"
        "### Step 0 — test step\n\n"
        "```yaml\n"
        "local_checks:\n"
        '  - command: "echo step0-gate"\n'
        "    timeout: 30\n"
        "```\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    capture_file = root / "captured-gates.txt"

    # Stub claude: captures ILK_DECLARED_GATES_FILE contents
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"# Capture ILK_DECLARED_GATES_FILE to {capture_file}\n"
        'if [[ -n "${ILK_DECLARED_GATES_FILE:-}" && -r "${ILK_DECLARED_GATES_FILE}" ]]; then\n'
        f'  cp "${{ILK_DECLARED_GATES_FILE}}" {shlex.quote(str(capture_file))}\n'
        "else\n"
        f'  echo "NO_GATES_FILE" > {shlex.quote(str(capture_file))}\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {
        "project": project,
        "plans": plans,
        "data_home": data_home,
        "key": key,
        "bin": bin_dir,
        "capture_file": capture_file,
        "root": root,
    }


def _run_one_iteration(world: dict) -> subprocess.CompletedProcess:
    """Source the driver under ILK_DOTSOURCE_ONLY=1 and run one iteration."""
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source {shlex.quote(str(_DRIVER))} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
main --project-path {shlex.quote(str(world["project"]))} \\
     --max-iterations 1 \\
     --iteration-timeout-min 1 \\
     --model test-model \\
     --run-local-checks
echo "MAIN_RC=$?"
"""
    env = {
        **os.environ,
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(world["root"]),
        "ILK_DATA_HOME": str(world["data_home"]),
    }
    env.pop("ILK_DATA_DIR", None)
    env.pop("ILK_DOTSOURCE_ONLY", None)
    (world["root"] / ".claude").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=env, cwd=str(world["root"]),
    )


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac4_runner_exports_declared_gates(tmp_path: Path) -> None:
    """AC-4: the runner exports ILK_DECLARED_GATES_FILE during the agent call,
    and the file holds exactly the two declared gate commands.
    """
    world = _build_world(tmp_path)
    proc = _run_one_iteration(world)
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])

    capture = world["capture_file"]
    assert capture.exists(), (
        "stub agent never wrote capture file.\n"
        f"last 40 lines:\n{tail}"
    )
    content = capture.read_text(encoding="utf-8").strip()
    assert content != "NO_GATES_FILE", (
        "ILK_DECLARED_GATES_FILE was not set during the agent call.\n"
        f"last 40 lines:\n{tail}"
    )
    lines = sorted(line.strip() for line in content.splitlines() if line.strip())
    expected = sorted(["echo frontmatter-gate", "echo step0-gate"])
    assert lines == expected, (
        f"gates file contents mismatch.\n"
        f"expected: {expected}\n"
        f"got: {lines}\n"
        f"last 40 lines:\n{tail}"
    )