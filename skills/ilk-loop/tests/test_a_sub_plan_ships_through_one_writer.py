"""Pin that a sub-plan has exactly one ship writer.

AC-1: commands/ilk.md and SKILL.md contain no instruction to hand-commit
      a #ship marker or set status: shipped by hand.  They name
      ship_transition.py.
AC-2: the hook denies a Bash git commit whose message contains #ship].
      It does the same for -am, --message=, and -F <file> where the file
      contains the trailer.
AC-3 (controls): allowed — a step commit, ship_transition.py, git log --grep.
AC-4 (end to end): ship_transition.ship twice in a row produces exactly 1
      commit whose message contains [plan:x#ship].
AC-5: ship_transition.ship for a batch_verification: true slug, with
      ILK_ITERATION_SUBPLAN set, raises ShipTransitionError.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-live-clone-edit.py"
SCRIPTS = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
COMMANDS_ILK = REPO_ROOT / "commands" / "ilk.md"
SKILL_MD = REPO_ROOT / "skills" / "ilk-loop" / "SKILL.md"


# ── helpers ──────────────────────────────────────────────────────────────────


def _event(tool_name: str, tool_input: dict | None = None) -> str:
    return json.dumps({"tool_name": tool_name, "tool_input": tool_input or {}})


def _run_hook(event: str, env: dict[str, str]) -> dict:
    if not HOOK_PATH.exists():
        return {"allowed": True, "missing": True}
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=event,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, f"hook exited {result.returncode}: {result.stderr}"
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return cp.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


SUBPLAN_TEMPLATE = """---
plan: {slug}
status: {status}
current_step: 0
tickets: []
priority: P1
estimated_steps: 2
last_updated: 2026-10-01
verification_tier: loop-verified
batch_verification: {batch}
local_checks: []
---

# Sub-plan: {slug}
"""


def _make_plans_dir(
    tmp_path: Path,
    slug: str = "test-slug",
    status: str = "in-progress",
    batch: bool = False,
) -> Path:
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / f"2026-10-01-{slug}.md").write_text(
        SUBPLAN_TEMPLATE.format(slug=slug, status=status, batch=str(batch).lower()),
        encoding="utf-8",
    )
    return plans


@pytest.fixture()
def _fake_home(tmp_path: Path):
    """Minimal fake home for hook tests (same layout as test_no_live_clone_edit_hook)."""
    clone = tmp_path / "clone"
    (clone / "skills" / "ilk-loop" / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(clone), capture_output=True, timeout=10)

    home = tmp_path / "home"
    link_target = clone / "skills" / "ilk-loop"
    (home / "skills").mkdir(parents=True)
    (home / "skills" / "ilk-loop").symlink_to(link_target)

    return {"home": home, "clone": clone}


# ── AC-1: ilk.md and SKILL.md name ship_transition.py, not hand commits ──────


class TestAC1OneWriterInDocs:
    """commands/ilk.md and SKILL.md must not tell the worker to hand-commit
    a #ship marker or set status: shipped by hand."""

    def test_ilk_md_no_hand_ship_commit(self) -> None:
        """AC-1: no line in §7 matches a hand #ship commit instruction."""
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        # §7 is "Boundary: stop and hand back"
        section_start = text.find("## 7. Boundary:")
        assert section_start != -1, "§7 not found in commands/ilk.md"
        section_end = text.find("\n## 8.", section_start)
        section = text[section_start:section_end] if section_end != -1 else text[section_start:]
        for line in section.splitlines():
            stripped = line.strip()
            # Must not match "Commit: ...#ship" patterns
            if "Commit:" in stripped and "#ship" in stripped:
                pytest.fail(f"ilk.md §7 still has hand ship commit: {stripped!r}")

    def test_ilk_md_no_set_status_shipped(self) -> None:
        """AC-1: no line in §7 says to set status: shipped by hand."""
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        section_start = text.find("## 7. Boundary:")
        assert section_start != -1
        section_end = text.find("\n## 8.", section_start)
        section = text[section_start:section_end] if section_end != -1 else text[section_start:]
        for line in section.splitlines():
            stripped = line.strip()
            if "Set" in stripped and "status" in stripped and "shipped" in stripped:
                pytest.fail(f"ilk.md §7 still says set status shipped by hand: {stripped!r}")

    def test_ilk_md_names_ship_transition(self) -> None:
        """AC-1: §7 names ship_transition.py."""
        text = COMMANDS_ILK.read_text(encoding="utf-8")
        section_start = text.find("## 7. Boundary:")
        assert section_start != -1
        section_end = text.find("\n## 8.", section_start)
        section = text[section_start:section_end] if section_end != -1 else text[section_start:]
        assert "ship_transition.py" in section, (
            "ilk.md §7 does not mention ship_transition.py"
        )

    def test_skill_md_step6_names_ship_transition(self) -> None:
        """AC-1: SKILL.md "The loop" step 6 names ship_transition.py."""
        text = SKILL_MD.read_text(encoding="utf-8")
        # Find "The loop" section
        loop_start = text.find("## The loop")
        assert loop_start != -1, "## The loop not found in SKILL.md"
        loop_end = text.find("\n## ", loop_start + 10)
        loop_section = text[loop_start:loop_end] if loop_end != -1 else text[loop_start:]
        # Step 6 is the shipping step
        step6_start = loop_section.find("6.")
        if step6_start == -1:
            pytest.fail("step 6 not found in The loop section")
        assert "ship_transition.py" in loop_section[step6_start:], (
            "SKILL.md The loop step 6 does not mention ship_transition.py"
        )


# ── AC-2: hook denies Bash git commit with #ship] ────────────────────────────


class TestAC2HookDeniesShipCommit:
    """The hook must deny a Bash git commit whose message contains #ship]."""

    def test_deny_m_flag(self, _fake_home: dict) -> None:
        """AC-2: git commit -m '...#ship]' ⇒ deny."""
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        event = _event("Bash", {
            "command": 'git commit -m "chore(plans): x shipped [plan:x#ship]"',
        })
        result = _run_hook(event, env)
        assert result["allowed"] is False, "hook did not deny -m #ship commit"

    def test_deny_am_flag(self, _fake_home: dict) -> None:
        """AC-2: git commit -am '...#ship]' ⇒ deny."""
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        event = _event("Bash", {
            "command": 'git commit -am "chore(plans): x shipped [plan:x#ship]"',
        })
        result = _run_hook(event, env)
        assert result["allowed"] is False, "hook did not deny -am #ship commit"

    def test_deny_message_equals(self, _fake_home: dict) -> None:
        """AC-2: git commit --message='...#ship]' ⇒ deny."""
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        event = _event("Bash", {
            "command": 'git commit --message="chore(plans): x shipped [plan:x#ship]"',
        })
        result = _run_hook(event, env)
        assert result["allowed"] is False, "hook did not deny --message= #ship commit"

    def test_deny_file_flag(self, _fake_home: dict, tmp_path: Path) -> None:
        """AC-2: git commit -F <file> where file contains #ship] ⇒ deny."""
        msg_file = tmp_path / "msg.txt"
        msg_file.write_text("chore(plans): x shipped [plan:x#ship]\n", encoding="utf-8")
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        event = _event("Bash", {
            "command": f"git commit -F {msg_file}",
        })
        result = _run_hook(event, env)
        assert result["allowed"] is False, "hook did not deny -F #ship commit"


# ── AC-3: controls — allowed commands ────────────────────────────────────────


class TestAC3ControlsAllowed:
    """These must be allowed — no #ship marker in the message."""

    def test_step_commit_allowed(self, _fake_home: dict) -> None:
        """AC-3: git commit -m 'fix: y [plan:x#step-1]' ⇒ allow."""
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        event = _event("Bash", {
            "command": 'git commit -m "fix: y [plan:x#step-1]"',
        })
        result = _run_hook(event, env)
        assert result["allowed"] is True, "hook denied a step commit"

    def test_ship_transition_command_allowed(self, _fake_home: dict) -> None:
        """AC-3: python3 …/ship_transition.py --ship x … ⇒ allow."""
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        event = _event("Bash", {
            "command": f"python3 {SCRIPTS / 'ship_transition.py'} --ship x --plans-dir /tmp/p --repo /tmp/r",
        })
        result = _run_hook(event, env)
        assert result["allowed"] is True, "hook denied ship_transition.py command"

    def test_git_log_grep_allowed(self, _fake_home: dict) -> None:
        """AC-3: git log --grep '#ship]' ⇒ allow (read-only)."""
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        event = _event("Bash", {
            "command": "git log --grep '#ship]'",
        })
        result = _run_hook(event, env)
        assert result["allowed"] is True, "hook denied git log --grep"


# ── AC-4: ship_transition.ship twice ⇒ exactly 1 marker commit ───────────────


class TestAC4IdempotentShip:
    """ship() twice produces exactly 1 commit containing [plan:x#ship]."""

    def test_double_ship_single_commit(self, tmp_path: Path) -> None:
        """AC-4: calling ship() twice on the same slug yields 1 marker commit."""
        sys.path.insert(0, str(SCRIPTS))
        from importlib import import_module
        st = import_module("ship_transition")

        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(tmp_path, slug="x", status="in-progress")

        old_env = os.environ.pop("ILK_ITERATION_SUBPLAN", None)
        try:
            st.ship(plans, repo, "x")
            st.ship(plans, repo, "x")
        finally:
            if old_env is not None:
                os.environ["ILK_ITERATION_SUBPLAN"] = old_env

        # Count commits with [plan:x#ship] (use --fixed-strings to avoid
        # glob interpretation of the square brackets)
        log = _git(repo, "log", "--oneline", "--fixed-strings", "--grep", "[plan:x#ship]")
        lines = [l for l in log.splitlines() if l.strip()]
        assert len(lines) == 1, (
            f"expected 1 marker commit, found {len(lines)}:\n{log}"
        )


# ── AC-5: batch_verification slug refused in worker session ──────────────────


class TestAC5BatchVerificationRefused:
    """ship() for a batch_verification: true slug with ILK_ITERATION_SUBPLAN
    set must raise ShipTransitionError."""

    def test_batch_verification_refused_in_worker(self, tmp_path: Path) -> None:
        """AC-5: ship() raises ShipTransitionError for batch_verification slug
        when ILK_ITERATION_SUBPLAN is set."""
        sys.path.insert(0, str(SCRIPTS))
        from importlib import import_module
        st = import_module("ship_transition")

        repo = _make_repo(tmp_path)
        plans = _make_plans_dir(
            tmp_path, slug="batch-slug", status="in-progress", batch=True,
        )

        old_env = os.environ.get("ILK_ITERATION_SUBPLAN")
        try:
            os.environ["ILK_ITERATION_SUBPLAN"] = "batch-slug"
            with pytest.raises(st.ShipTransitionError, match="batch_verification|driver"):
                st.ship(plans, repo, "batch-slug")
        finally:
            if old_env is None:
                os.environ.pop("ILK_ITERATION_SUBPLAN", None)
            else:
                os.environ["ILK_ITERATION_SUBPLAN"] = old_env

        # Verify nothing was written
        assert st.find_marker_commit(repo, "batch-slug") is None, (
            "marker commit was created despite refusal"
        )