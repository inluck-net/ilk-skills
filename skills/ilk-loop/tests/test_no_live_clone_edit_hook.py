"""Pin that a repo-tracked hook refuses worker writes into the live clone.

AC-1: Edit/Write to <clone>/x, to <home>/skills/ilk-loop/x (via the
      symlink), and to a not-yet-existing <clone>/newdir/x ⇒ deny.
AC-2: a path in the fake selfmod worktree, a plans file under the fake
      ~/.ilk-data, <tmp>/x, and another repo ⇒ allow.
AC-3: Bash echo x > <clone>/f, sed -i '' s/a/b/ <home>/skills/ilk-loop/f,
      cp <tmp>/a <clone>/b, git -C <clone> commit -m x ⇒ deny.
      cat <clone>/f, git -C <clone> log, grep -r x <clone> ⇒ allow.
AC-4: garbage stdin, empty tool_input, a home with no skills/ilk-loop
      ⇒ allow, exit 0, no stdout.
AC-5: the install reconcile on a worker-home settings.json holding the
      stop-gap entry plus a foreign hook ⇒ one no-live-clone-edit entry
      per matcher, the foreign hook kept, no-full-suite.sh still under
      Bash; a second run changes nothing. The same reconcile on an
      interactive home ⇒ no no-live-clone-edit entry.

The hook file does not exist yet; deny tests are xfail(red-first).
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _fake_home(tmp_path: Path):
    """Create a fake home with a symlinked ilk-loop into a fake clone.

    Layout::

        tmp/
          clone/                    ← a git-init'd dir
            skills/ilk-loop/scripts/
          home/                     ← the fake CLAUDE_CONFIG_DIR
            skills/ilk-loop -> ../../clone/skills/ilk-loop  (symlink)
          selfmod/                  ← a fake selfmod worktree
          other_repo/               ← a different repo
          plans/                    ← a fake ~/.ilk-data/plans
    """
    clone = tmp_path / "clone"
    (clone / "skills" / "ilk-loop" / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(clone), capture_output=True,
                   timeout=10)

    home = tmp_path / "home"
    link_target = clone / "skills" / "ilk-loop"
    (home / "skills").mkdir(parents=True)
    (home / "skills" / "ilk-loop").symlink_to(link_target)

    selfmod = tmp_path / "selfmod"
    selfmod.mkdir()

    other_repo = tmp_path / "other_repo"
    other_repo.mkdir()

    plans = tmp_path / "plans"
    plans.mkdir()

    return {
        "home": home,
        "clone": clone,
        "selfmod": selfmod,
        "other_repo": other_repo,
        "plans": plans,
    }


def _event(tool_name: str, tool_input: dict | None = None) -> str:
    """Build a JSON event string as Claude Code would."""
    return json.dumps({"tool_name": tool_name,
                       "tool_input": tool_input or {}})


def _run_hook(event: str, env: dict[str, str]) -> dict:
    """Run the hook as a subprocess and return {allowed, payload?, stdout}.

    If the hook file is missing, returns {"allowed": True, "missing": True}.
    """
    if not HOOK_PATH.exists():
        return {"allowed": True, "missing": True}
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=event,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, f"hook exited {result.returncode}: {result.stderr}"
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


# ---------------------------------------------------------------------------
# AC-1: Edit/Write into clone ⇒ deny
# ---------------------------------------------------------------------------

class TestDenyEditClone:
    """Edit/Write/MultiEdit/NotebookEdit whose target resolves inside the
    clone root must be denied."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_edit_clone_file(self, _fake_home: dict) -> None:
        """AC-1: Edit <clone>/x ⇒ deny."""
        clone = _fake_home["clone"]
        (clone / "x").write_text("content\n")
        event = _event("Edit", {"file_path": str(clone / "x"),
                                "old_string": "content", "new_string": "new"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_write_clone_file(self, _fake_home: dict) -> None:
        """AC-1: Write <clone>/x ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Write", {"file_path": str(clone / "x"),
                                 "content": "data\n"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_edit_via_symlink(self, _fake_home: dict) -> None:
        """AC-1: Edit <home>/skills/ilk-loop/x (via symlink) ⇒ deny."""
        home = _fake_home["home"]
        clone = _fake_home["clone"]
        (clone / "skills" / "ilk-loop" / "x").write_text("content\n")
        event = _event("Edit", {
            "file_path": str(home / "skills" / "ilk-loop" / "x"),
            "old_string": "content", "new_string": "new",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(home)}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_write_not_yet_existing_clone_path(self, _fake_home: dict) -> None:
        """AC-1: Write to a not-yet-existing <clone>/newdir/x ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Write", {"file_path": str(clone / "newdir" / "x"),
                                 "content": "data\n"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_edit_home_skills_subpath(self, _fake_home: dict) -> None:
        """AC-1: Edit <home>/skills/ilk-loop/scripts/foo.py ⇒ deny."""
        home = _fake_home["home"]
        clone = _fake_home["clone"]
        (clone / "skills" / "ilk-loop" / "scripts" / "foo.py").write_text(
            "# test\n"
        )
        event = _event("Edit", {
            "file_path": str(home / "skills" / "ilk-loop" / "scripts" / "foo.py"),
            "old_string": "# test", "new_string": "# changed",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(home)}
        result = _run_hook(event, env)
        assert result["allowed"] is False


# ---------------------------------------------------------------------------
# AC-2: paths outside clone ⇒ allow
# ---------------------------------------------------------------------------

class TestAllowOutsideClone:
    """Edit/Write to paths that are NOT inside the clone must be allowed."""

    def test_selfmod_worktree(self, _fake_home: dict) -> None:
        """AC-2: Edit in the selfmod worktree ⇒ allow."""
        target = _fake_home["selfmod"] / "x.py"
        target.write_text("content\n")
        event = _event("Edit", {"file_path": str(target),
                                "old_string": "content", "new_string": "new"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_plans_dir(self, _fake_home: dict) -> None:
        """AC-2: Write to a plans file under fake ~/.ilk-data ⇒ allow."""
        target = _fake_home["plans"] / "MASTER.md"
        event = _event("Write", {"file_path": str(target),
                                 "content": "# plan\n"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_tmp_path(self, _fake_home: dict, tmp_path: Path) -> None:
        """AC-2: Write to <tmp>/x ⇒ allow."""
        target = tmp_path / "scratch" / "x"
        event = _event("Write", {"file_path": str(target),
                                 "content": "data\n"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_other_repo(self, _fake_home: dict) -> None:
        """AC-2: Edit in another repo ⇒ allow."""
        target = _fake_home["other_repo"] / "file.py"
        target.write_text("content\n")
        event = _event("Edit", {"file_path": str(target),
                                "old_string": "content", "new_string": "new"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True


# ---------------------------------------------------------------------------
# AC-3: Bash writes into clone ⇒ deny; reads ⇒ allow
# ---------------------------------------------------------------------------

class TestDenyBashCloneWrite:
    """Bash commands that WRITE into the clone must be denied."""

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_redirect_overwrite(self, _fake_home: dict) -> None:
        """AC-3: echo x > <clone>/f ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"echo x > {clone}/f"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_redirect_append(self, _fake_home: dict) -> None:
        """AC-3: echo x >> <clone>/f ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"echo x >> {clone}/f"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_sed_inplace(self, _fake_home: dict) -> None:
        """AC-3: sed -i '' s/a/b/ <home>/skills/ilk-loop/f ⇒ deny."""
        home = _fake_home["home"]
        clone = _fake_home["clone"]
        (clone / "skills" / "ilk-loop" / "f").write_text("abc\n")
        event = _event("Bash", {
            "command": f"sed -i '' s/a/b/ {home}/skills/ilk-loop/f",
        })
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(home)}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_cp_into_clone(self, _fake_home: dict, tmp_path: Path) -> None:
        """AC-3: cp <tmp>/a <clone>/b ⇒ deny."""
        (tmp_path / "a").write_text("content\n")
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"cp {tmp_path}/a {clone}/b"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_mv_into_clone(self, _fake_home: dict, tmp_path: Path) -> None:
        """mv <tmp>/a <clone>/b ⇒ deny."""
        (tmp_path / "a").write_text("content\n")
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"mv {tmp_path}/a {clone}/b"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_rm_clone_file(self, _fake_home: dict) -> None:
        """rm <clone>/f ⇒ deny."""
        clone = _fake_home["clone"]
        (clone / "f").write_text("content\n")
        event = _event("Bash", {"command": f"rm {clone}/f"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_mkdir_clone(self, _fake_home: dict) -> None:
        """mkdir <clone>/newdir ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"mkdir {clone}/newdir"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_touch_clone_file(self, _fake_home: dict) -> None:
        """touch <clone>/new ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"touch {clone}/new"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_ln_clone(self, _fake_home: dict, tmp_path: Path) -> None:
        """ln -s <tmp>/a <clone>/link ⇒ deny."""
        (tmp_path / "a").write_text("content\n")
        clone = _fake_home["clone"]
        event = _event("Bash",
                       {"command": f"ln -s {tmp_path}/a {clone}/link"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_git_commit_clone(self, _fake_home: dict) -> None:
        """AC-3: git -C <clone> commit -m x ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash",
                       {"command": f"git -C {clone} commit -m x"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_git_checkout_clone(self, _fake_home: dict) -> None:
        """git -C <clone> checkout main ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash",
                       {"command": f"git -C {clone} checkout main"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_git_reset_clone(self, _fake_home: dict) -> None:
        """git -C <clone> reset --hard ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash",
                       {"command": f"git -C {clone} reset --hard"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_git_stash_clone(self, _fake_home: dict) -> None:
        """git -C <clone> stash ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash",
                       {"command": f"git -C {clone} stash"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_git_merge_clone(self, _fake_home: dict) -> None:
        """git -C <clone> merge other ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash",
                       {"command": f"git -C {clone} merge other"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_git_rebase_clone(self, _fake_home: dict) -> None:
        """git -C <clone> rebase main ⇒ deny."""
        clone = _fake_home["clone"]
        event = _event("Bash",
                       {"command": f"git -C {clone} rebase main"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is False


class TestAllowBashCloneRead:
    """Bash commands that only READ from the clone must be allowed."""

    def test_cat_clone_file(self, _fake_home: dict) -> None:
        """AC-3: cat <clone>/f ⇒ allow."""
        clone = _fake_home["clone"]
        (clone / "f").write_text("content\n")
        event = _event("Bash", {"command": f"cat {clone}/f"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_git_log_clone(self, _fake_home: dict) -> None:
        """AC-3: git -C <clone> log ⇒ allow."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"git -C {clone} log"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_git_show_clone(self, _fake_home: dict) -> None:
        """git -C <clone> show ⇒ allow."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"git -C {clone} show"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_git_diff_clone(self, _fake_home: dict) -> None:
        """git -C <clone> diff ⇒ allow."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"git -C {clone} diff"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_git_status_clone(self, _fake_home: dict) -> None:
        """git -C <clone> status ⇒ allow."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"git -C {clone} status"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_grep_clone(self, _fake_home: dict) -> None:
        """AC-3: grep -r x <clone> ⇒ allow."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"grep -r x {clone}"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_ls_clone(self, _fake_home: dict) -> None:
        """ls <clone> ⇒ allow (read-only)."""
        clone = _fake_home["clone"]
        event = _event("Bash", {"command": f"ls {clone}"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True


# ---------------------------------------------------------------------------
# AC-4: garbage / edge cases ⇒ allow, exit 0, no stdout
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Garbage stdin, empty tool_input, no skills/ilk-loop ⇒ allow."""

    def test_garbage_stdin(self, _fake_home: dict) -> None:
        """AC-4: garbage stdin ⇒ allow, exit 0, no stdout."""
        event = "this is not json!!!"
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_empty_tool_input(self, _fake_home: dict) -> None:
        """AC-4: empty tool_input ⇒ allow, exit 0, no stdout."""
        event = json.dumps({"tool_name": "Edit"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(_fake_home["home"])}
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_no_skills_link(self, tmp_path: Path) -> None:
        """AC-4: a home with no skills/ilk-loop ⇒ allow, exit 0, no stdout."""
        bare_home = tmp_path / "bare_home"
        bare_home.mkdir()
        event = _event("Edit", {"file_path": str(tmp_path / "x"),
                                "old_string": "a", "new_string": "b"})
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(bare_home)}
        result = _run_hook(event, env)
        assert result["allowed"] is True


# ---------------------------------------------------------------------------
# AC-5: install reconcile
# ---------------------------------------------------------------------------

FOREIGN_HOOKS = [
    {"type": "command", "command": "/Users/chad/Projects/github/inluck-net/kr-sdlc/hooks/r1-prod-data.sh"},
    {"type": "command", "command": "/Users/chad/Projects/github/inluck-net/kr-sdlc/hooks/r2-self-merge.sh"},
    {"type": "command", "command": "/Users/chad/Projects/github/inluck-net/kr-sdlc/hooks/r3-secrets.sh"},
]


def _find_hook_command(hooks_dir: str) -> str:
    """Return the absolute path to the hook as install.sh would compute it."""
    return os.path.join(hooks_dir, "no-live-clone-edit.py")


def _run_reconcile(settings_path: str, hooks_dir: str, *,
                   apply: bool = True) -> str:
    """Run the reconciliation Python logic (extracted from install.sh).

    Returns stdout output.
    """
    hook_cmd = _find_hook_command(hooks_dir)
    script = r'''
import json, os, sys

settings_path = sys.argv[1]
hook_cmd = sys.argv[2]
dry_run = sys.argv[3] != "1"

if os.path.isfile(settings_path):
    with open(settings_path) as f:
        settings = json.load(f)
else:
    settings = {}

hooks = settings.get("hooks", {})
pre_tool = hooks.get("PreToolUse", [])
if not pre_tool:
    pre_tool = [{"matcher": "Bash", "hooks": []}]
    hooks["PreToolUse"] = pre_tool

bash_entry = pre_tool[0]
existing = bash_entry.get("hooks", [])
already = any(h.get("command") == hook_cmd for h in existing)

if already:
    print("skip: {} already has the hook".format(settings_path))
    sys.exit(0)

kept = [h for h in existing if h.get("command") != hook_cmd]
hook_entry = {"type": "command", "command": hook_cmd}
new_hooks = kept + [hook_entry]
bash_entry["hooks"] = new_hooks
hooks["PreToolUse"] = pre_tool
settings["hooks"] = hooks

if dry_run:
    print("would update: {}".format(settings_path))
else:
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print("updated: {}".format(settings_path))
'''
    result = subprocess.run(
        ["python3", "-", settings_path, hook_cmd, "1" if apply else "0"],
        input=script, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=10,
    )
    assert result.returncode == 0, f"reconcile failed: {result.stderr}"
    return result.stdout.strip()


def _extract_reconcile_python() -> str:
    """Extract the Python block from reconcile_hooks_settings in install.sh.

    Returns the raw Python source code embedded between the PYEOF markers.
    """
    install_sh = REPO_ROOT / "install.sh"
    text = install_sh.read_text()
    start = text.find("reconcile_hooks_settings() {")
    if start == -1:
        raise RuntimeError("reconcile_hooks_settings not found in install.sh")
    py_start = text.find("<<'PYEOF'\n", start)
    if py_start == -1:
        py_start = text.find('<<PYEOF\n', start)
    if py_start == -1:
        raise RuntimeError("PYEOF heredoc not found")
    py_start = text.index("\n", py_start) + 1
    py_end = text.find("\nPYEOF", py_start)
    if py_end == -1:
        raise RuntimeError("closing PYEOF not found")
    return text[py_start:py_end]


def _run_reconcile_multi(hooks_dir: str, *, apply: bool = True,
                         host: str = "worker") -> str:
    """Run the ACTUAL reconcile Python extracted from install.sh.

    This is the real code — if it can't handle multiple matchers, the test
    fails.  No mocking.  ``host`` simulates the host-type detection that
    install.sh does from the settings path.
    """
    settings_path = os.path.join(os.path.dirname(hooks_dir), "settings.json")
    hook_cmds = json.dumps(["no-full-suite.sh", "no-live-clone-edit.py"])
    matchers = json.dumps(["Bash", "Edit|Write|MultiEdit|NotebookEdit"])
    hosts = json.dumps(["all", "worker"])
    script = _extract_reconcile_python()
    result = subprocess.run(
        ["python3", "-", settings_path, hook_cmds, matchers, hosts,
         host, "1" if apply else "0"],
        input=script, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=10,
    )
    assert result.returncode == 0, f"reconcile failed: {result.stderr}"
    return result.stdout.strip()


def _make_settings(*, hooks_list: list[dict] | None = None,
                   include_hooks_key: bool = True) -> dict:
    """Build a settings.json fixture."""
    settings: dict = {"env": {}, "permissions": {"defaultMode": "auto"}}
    if include_hooks_key:
        entry = {"matcher": "Bash", "hooks": hooks_list or []}
        settings["hooks"] = {"PreToolUse": [entry]}
    return settings


class TestReconcileWorker:
    """AC-5: install reconcile on a worker-home settings.json."""

    def test_no_live_clone_edit_appears(self, tmp_path: Path) -> None:
        """AC-5: reconcile creates Edit|Write|MultiEdit|NotebookEdit entry
        and keeps Bash/no-full-suite.sh."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings(hooks_list=list(FOREIGN_HOOKS))
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile_multi(hooks_dir, host="worker")

        with open(settings_path) as f:
            result = json.load(f)
        pre_tool = result["hooks"]["PreToolUse"]
        matchers = {e["matcher"]: e for e in pre_tool}

        # Bash matcher exists and has no-full-suite.sh
        assert "Bash" in matchers, "Bash matcher entry missing"
        bash_cmds = [h["command"] for h in matchers["Bash"]["hooks"]]
        assert any("no-full-suite.sh" in c for c in bash_cmds)

        # Edit|Write|MultiEdit|NotebookEdit matcher exists
        assert "Edit|Write|MultiEdit|NotebookEdit" in matchers, (
            "Edit matcher entry missing"
        )

        # Foreign hooks are preserved
        all_cmds = [h["command"] for h in pre_tool[0]["hooks"]]
        for fh in FOREIGN_HOOKS:
            assert fh["command"] in all_cmds, (
                f"foreign hook {fh['command']} was removed"
            )

    def test_idempotent(self, tmp_path: Path) -> None:
        """AC-5: a second reconcile run changes nothing."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings(hooks_list=list(FOREIGN_HOOKS))
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile_multi(hooks_dir, host="worker")
        with open(settings_path) as f:
            first = f.read()

        _run_reconcile_multi(hooks_dir, host="worker")
        with open(settings_path) as f:
            second = f.read()

        assert first == second

    def test_interactive_home_excluded(self, tmp_path: Path) -> None:
        """AC-5: interactive home does NOT get no-live-clone-edit."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings(hooks_list=list(FOREIGN_HOOKS))
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile_multi(hooks_dir, host="interactive")

        with open(settings_path) as f:
            result = json.load(f)
        pre_tool = result["hooks"]["PreToolUse"]
        matchers = {e["matcher"]: e for e in pre_tool}
        assert "Edit|Write|MultiEdit|NotebookEdit" not in matchers, (
            "no-live-clone-edit appeared in interactive settings — "
            "should be worker-only"
        )