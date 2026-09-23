"""Pins for the gate timeout summing and the gate-first marker repo.

Part of sub-plan `the-gate-reads-its-timeout-and-commits-where-it-runs` (step 0).

Defect A: get_step_declared_timeout uses GNU awk's three-argument match(),
which does not exist under /usr/bin/awk (macOS).  The function prints an
empty string instead of the sum, and the outer cap falls back to 180s.

Defect B: commit_gate_first_marker commits to REPOS[0] unconditionally.
In a selfmod batch with SELFMOD_ISOLATED=1 the commit should land on the
worktree, not the clone — otherwise merge-back refuses (BranchMovedError).

Harness: source the driver functions under bash and call them directly,
with a fake skill root and plan file.  The function extractor is copied
from test_sentinel_path_agreement.py:91-115.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent / "scripts"
_DRIVER = _SCRIPTS / "run_ilk_loop_claude.sh"


# ── helpers ──────────────────────────────────────────────────────────────────


def _function_body(path: Path, fn_name: str) -> list[tuple[int, str]]:
    """Return (line_number, line_text) for the body of `fn_name() {` ... `}`.

    Locates the function by name, not by line number.
    """
    lines = path.read_text().splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"{fn_name}() {{"):
            start = i
            break
    assert start is not None, f"{fn_name}() not found in {path.name}"
    body = []
    for i in range(start + 1, len(lines)):
        # A column-0 `}` closes the function; a column-0 `name() {` opens the
        # next one.  Both files indent nested blocks, so neither appears inside.
        if lines[i] == "}" or (
            lines[i].endswith("() {") and lines[i][:-4].isidentifier()
        ):
            break
        body.append((i + 1, lines[i]))
    return body


def _source_fns(*names: str) -> str:
    """Concatenate named function bodies from the driver into a single script."""
    parts = []
    for name in names:
        body = _function_body(_DRIVER, name)
        body_text = "\n".join(line for _, line in body)
        parts.append(f"{name}() {{\n{body_text}\n}}\n")
    return "\n".join(parts)


# ── Defect A: timeout summing under macOS awk ────────────────────────────────


def test_declared_timeouts_are_summed_under_system_awk(tmp_path: Path):
    """get_step_declared_timeout must sum timeout: values under /usr/bin/awk."""
    # Build a fake skill root with a resolver that prints the plans dir.
    sk = tmp_path / "sk" / "ilk-loop" / "scripts"
    sk.mkdir(parents=True)
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()
    (sk / "ilk_paths.py").write_text(
        f"import json; print(json.dumps({{'resolved_plans_dir': '{plans_dir}'}}))"
    )
    # Symlink to the real run_local_checks.py (needed after step 1).
    (sk / "run_local_checks.py").symlink_to(_SCRIPTS / "run_local_checks.py")

    # Write a plan with two timeout declarations.
    plan = plans_dir / "x.md"
    plan.write_text(
        "---\nplan: x\n---\n\n### Step 0\n\n```yaml\nlocal_checks:\n"
        "  - command: echo ok\n    timeout: 900\n  - command: echo ok\n    timeout: 30\n```\n"
    )

    src = _source_fns("get_step_declared_timeout")
    call = 'get_step_declared_timeout "%%PROJECT%%" "x" "0"'
    script = src + "\n" + call.replace("%%PROJECT%%", str(tmp_path))
    env = {**os.environ, "PATH": f"/usr/bin:/bin:{os.path.dirname(sys.executable)}",
           "_SKILL_ROOT": str(sk.parent.parent)}
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip() == "930", f"got {result.stdout.strip()!r}"


def test_timeoutless_gate_sums_to_zero(tmp_path: Path):
    """A gate with no timeout: must sum to 0, not empty string."""
    sk = tmp_path / "sk" / "ilk-loop" / "scripts"
    sk.mkdir(parents=True)
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()
    (sk / "ilk_paths.py").write_text(
        f"import json; print(json.dumps({{'resolved_plans_dir': '{plans_dir}'}}))"
    )
    (sk / "run_local_checks.py").symlink_to(_SCRIPTS / "run_local_checks.py")

    plan = plans_dir / "x.md"
    plan.write_text(
        "---\nplan: x\n---\n\n### Step 0\n\n```yaml\nlocal_checks:\n"
        "  - command: echo ok\n```\n"
    )

    src = _source_fns("get_step_declared_timeout")
    call = 'get_step_declared_timeout "%%PROJECT%%" "x" "0"'
    script = src + "\n" + call.replace("%%PROJECT%%", str(tmp_path))
    env = {**os.environ, "PATH": f"/usr/bin:/bin:{os.path.dirname(sys.executable)}",
           "_SKILL_ROOT": str(sk.parent.parent)}
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip() == "0", f"got {result.stdout.strip()!r}"


def test_step_declared_timeout_pure():
    """The pure Python function sums timeouts correctly."""
    sys.path.insert(0, str(_SCRIPTS))
    import run_local_checks as rlc
    body = (
        "### Step 0\n\n```yaml\nlocal_checks:\n"
        "  - command: echo ok\n    timeout: 900\n  - command: echo ok\n    timeout: 30\n```\n"
    )
    assert rlc.step_declared_timeout(body, 0) == 930


# ── Defect B: gate-first marker repo ─────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="commit_gate_first_marker ignores selfmod worktree")
def test_marker_lands_in_selfmod_worktree(tmp_path: Path):
    """With SELFMOD_ISOLATED=1 the marker commit must land on the worktree."""
    # Build real repos: clone + worktree.
    clone = tmp_path / "clone"
    clone.mkdir()
    subprocess.run(["git", "init", str(clone)], capture_output=True, check=True)
    (clone / "f.txt").write_text("init")
    subprocess.run(["git", "-C", str(clone), "add", "f.txt"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-m", "init"],
                   capture_output=True, check=True, env={**os.environ, "GIT_AUTHOR_NAME": "test",
                                                         "GIT_AUTHOR_EMAIL": "t@t",
                                                         "GIT_COMMITTER_NAME": "test",
                                                         "GIT_COMMITTER_EMAIL": "t@t"})

    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(clone), "worktree", "add", "--detach", str(wt)],
                   capture_output=True, check=True)

    clone_head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()

    src = _source_fns("selfmod_effective_repo", "commit_gate_first_marker")
    script = src + (
        f'\nREPOS=("{clone}")'
        f'\nPROJECT_PATH="{clone}"'
        f'\nSELFMOD_ISOLATED=1'
        f'\nSELFMOD_WORKTREE_PATH="{wt}"'
        f'\nSELFMOD_ORIGINAL_PROJECT_PATH="{clone}"'
        '\ncommit_gate_first_marker "s" "0"'
    )
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"}
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    # Worktree HEAD should be the marker commit.
    wt_subject = subprocess.run(["git", "-C", str(wt), "log", "-1", "--format=%s"],
                                capture_output=True, text=True, check=True).stdout.strip()
    assert wt_subject == "chore(loop): gate-first marker [plan:s#step-0]", f"wt subject: {wt_subject}"

    # Clone HEAD must be unchanged.
    clone_head_after = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"],
                                      capture_output=True, text=True, check=True).stdout.strip()
    assert clone_head_after == clone_head, f"clone HEAD moved: {clone_head} -> {clone_head_after}"


def test_marker_without_isolation_lands_in_repos0(tmp_path: Path):
    """Negative control: without SELFMOD_ISOLATED the marker commits to REPOS[0]."""
    clone = tmp_path / "clone"
    clone.mkdir()
    subprocess.run(["git", "init", str(clone)], capture_output=True, check=True)
    (clone / "f.txt").write_text("init")
    subprocess.run(["git", "-C", str(clone), "add", "f.txt"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-m", "init"],
                   capture_output=True, check=True, env={**os.environ, "GIT_AUTHOR_NAME": "test",
                                                         "GIT_AUTHOR_EMAIL": "t@t",
                                                         "GIT_COMMITTER_NAME": "test",
                                                         "GIT_COMMITTER_EMAIL": "t@t"})

    src = _source_fns("selfmod_effective_repo", "commit_gate_first_marker")
    script = src + (
        f'\nREPOS=("{clone}")'
        f'\nPROJECT_PATH="{clone}"'
        '\nSELFMOD_ISOLATED=0'
        '\ncommit_gate_first_marker "s" "0"'
    )
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"}
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    clone_subject = subprocess.run(["git", "-C", str(clone), "log", "-1", "--format=%s"],
                                   capture_output=True, text=True, check=True).stdout.strip()
    assert clone_subject == "chore(loop): gate-first marker [plan:s#step-0]"
