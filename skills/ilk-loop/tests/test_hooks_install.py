"""Test the full-suite guardrail hook's core behaviours.

AC-7: ILK_ALLOW_FULL_SUITE=1 escape hatch works both inline and exported.
AC-8: A full-suite pytest command is denied with permissionDecision: deny.
AC-3: Reconciling settings.json never removes or reorders foreign hooks.
AC-4: The reconcile is idempotent.
AC-5: Install succeeds with no settings.json or no hooks key.

These tests pin the behaviours that the rest of the sub-plan must not break.
They invoke the hook script directly with representative input.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-full-suite.sh"
# The command string that install.sh writes into settings.json
HOOK_CMD_SUFFIX = "hooks/no-full-suite.sh"


def _run_hook(command: str, env: dict[str, str] | None = None) -> dict:
    """Run the hook with a synthetic Bash event and return the parsed JSON output."""
    event = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
    run_env = os.environ.copy()
    # Clear the escape-hatch env var unless the caller explicitly sets it
    run_env.pop("ILK_ALLOW_FULL_SUITE", None)
    if env:
        run_env.update(env)
    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=event,
        capture_output=True,
        text=True,
        env=run_env,
        timeout=10,
    )
    assert result.returncode == 0, f"hook exited {result.returncode}: {result.stderr}"
    # Empty stdout means the hook allowed (no deny payload)
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


# ── AC-8: deny payload ──────────────────────────────────────────────────────

class TestDenyPayload:
    """An unscoped full-suite pytest command is denied."""

    def test_unscoped_pytest_is_denied(self) -> None:
        """AC-8: bare `pytest` produces permissionDecision: deny."""
        result = _run_hook("pytest")
        assert result["allowed"] is False
        output = result["payload"]
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "guardrail" in output["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_unscoped_python_m_pytest_is_denied(self) -> None:
        """AC-8: `python3 -m pytest` (unscoped) is denied."""
        result = _run_hook("python3 -m pytest")
        assert result["allowed"] is False
        assert result["payload"]["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_scoped_pytest_is_allowed(self) -> None:
        """A pytest run with a path argument is already cheap — allowed."""
        result = _run_hook("pytest skills/ilk-loop/tests/test_foo.py -q")
        assert result["allowed"] is True

    def test_collect_only_is_allowed(self) -> None:
        """--collect-only is cheap — allowed."""
        result = _run_hook("pytest --collect-only -q")
        assert result["allowed"] is True


# ── AC-7: escape hatch ──────────────────────────────────────────────────────

class TestEscapeHatch:
    """ILK_ALLOW_FULL_SUITE=1 permits backgrounded full-suite runs.

    A foreground broad run with the hatch is denied — the harness
    auto-backgrounds at 600s and returns 0 bytes.  Only a backgrounded
    invocation (ending with &) is allowed.
    """

    def test_foreground_hatch_is_denied(self) -> None:
        """A foreground hatch run is denied (must be backgrounded)."""
        result = _run_hook("ILK_ALLOW_FULL_SUITE=1 pytest")
        assert result["allowed"] is False

    def test_backgrounded_hatch_is_allowed(self) -> None:
        """A backgrounded hatch run is allowed."""
        result = _run_hook("ILK_ALLOW_FULL_SUITE=1 pytest > /tmp/gate.log 2>&1 &")
        assert result["allowed"] is True


# ── File integrity ───────────────────────────────────────────────────────────

class TestHookFileIntegrity:
    """The hook file exists and is executable."""

    def test_hook_exists(self) -> None:
        assert HOOK_PATH.exists(), f"hook not found at {HOOK_PATH}"

    def test_hook_is_executable(self) -> None:
        mode = HOOK_PATH.stat().st_mode
        assert mode & stat.S_IXUSR, "hook is not user-executable"

    def test_hook_sha256(self) -> None:
        """Pin the sha256 so a future diff is visible."""
        import hashlib
        digest = hashlib.sha256(HOOK_PATH.read_bytes()).hexdigest()
        # This is the sha256 of the imported file as of 2026-08-14
        assert digest == "f3866a4ca2fd5862d738ee5153a4cdc03b3b4cf795e875809566bde60b54f447"


# ── settings.json reconcile (AC-3, AC-4, AC-5) ──────────────────────────────

# The three real kr-sdlc foreign hooks that AC-3 must protect.
FOREIGN_HOOKS = [
    {"type": "command", "command": "/Users/chad/Projects/github/inluck-net/kr-sdlc/hooks/r1-prod-data.sh"},
    {"type": "command", "command": "/Users/chad/Projects/github/inluck-net/kr-sdlc/hooks/r2-self-merge.sh"},
    {"type": "command", "command": "/Users/chad/Projects/github/inluck-net/kr-sdlc/hooks/r3-secrets.sh"},
]


def _make_settings(*, hooks_list: list[dict] | None = None,
                   include_hooks_key: bool = True) -> dict:
    """Build a settings.json fixture."""
    settings: dict = {"env": {}, "permissions": {"defaultMode": "auto"}}
    if include_hooks_key:
        entry = {"matcher": "Bash", "hooks": hooks_list or []}
        settings["hooks"] = {"PreToolUse": [entry]}
    return settings


def _find_hook_command(hooks_dir: str) -> str:
    """Return the absolute path to the hook as install.sh would compute it."""
    return os.path.join(hooks_dir, "no-full-suite.sh")


def _run_reconcile(settings_path: str, hooks_dir: str, *, apply: bool = True) -> str:
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
        input=script, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"reconcile failed: {result.stderr}"
    return result.stdout.strip()


class TestSettingsReconcileForeignEntries:
    """AC-3: foreign hooks are never removed or reordered."""

    def test_foreign_entries_preserved(self, tmp_path: Path) -> None:
        """The 3 kr-sdlc hooks survive reconciliation."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings(hooks_list=list(FOREIGN_HOOKS))
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile(settings_path, hooks_dir)

        with open(settings_path) as f:
            result = json.load(f)
        hooks = result["hooks"]["PreToolUse"][0]["hooks"]
        assert hooks[:3] == FOREIGN_HOOKS
        assert hooks[-1]["command"] == _find_hook_command(hooks_dir)

    def test_foreign_plus_existing_hook(self, tmp_path: Path) -> None:
        """Foreign entries + our hook already present → skip (idempotent)."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        hook_cmd = _find_hook_command(hooks_dir)
        data = _make_settings(hooks_list=FOREIGN_HOOKS + [
            {"type": "command", "command": hook_cmd},
        ])
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        output = _run_reconcile(settings_path, hooks_dir)
        assert "skip" in output


class TestSettingsReconcileIdempotent:
    """AC-4: running reconcile twice produces no diff."""

    def test_twice_produces_no_change(self, tmp_path: Path) -> None:
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings(hooks_list=list(FOREIGN_HOOKS))
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile(settings_path, hooks_dir)
        with open(settings_path) as f:
            first = f.read()

        _run_reconcile(settings_path, hooks_dir)
        with open(settings_path) as f:
            second = f.read()

        assert first == second


class TestSettingsReconcileMissing:
    """AC-5: no settings.json, or no hooks key — both succeed."""

    def test_no_settings_json(self, tmp_path: Path) -> None:
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")

        _run_reconcile(settings_path, hooks_dir)

        with open(settings_path) as f:
            result = json.load(f)
        hooks = result["hooks"]["PreToolUse"][0]["hooks"]
        assert len(hooks) == 1
        assert hooks[0]["command"] == _find_hook_command(hooks_dir)

    def test_no_hooks_key(self, tmp_path: Path) -> None:
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings(include_hooks_key=False)
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile(settings_path, hooks_dir)

        with open(settings_path) as f:
            result = json.load(f)
        hooks = result["hooks"]["PreToolUse"][0]["hooks"]
        assert len(hooks) == 1
        assert hooks[0]["command"] == _find_hook_command(hooks_dir)


class TestSettingsReconcileDryRun:
    """AC-6: dry-run prints what would change and modifies nothing."""

    def test_dry_run_does_not_modify(self, tmp_path: Path) -> None:
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings(hooks_list=list(FOREIGN_HOOKS))
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        before_hash = hashlib.sha256(Path(settings_path).read_bytes()).hexdigest()
        _run_reconcile(settings_path, hooks_dir, apply=False)
        after_hash = hashlib.sha256(Path(settings_path).read_bytes()).hexdigest()

        assert before_hash == after_hash, "dry-run modified settings.json"


# ── AC-1: hook declaration table ──────────────────────────────────────────────

class TestHookDeclarationTable:
    """AC-1: install.sh maps hook filename → matcher in one declaration table."""

    def test_install_sh_declares_hook_table(self) -> None:
        """install.sh contains a table mapping each hook to its matcher."""
        install_sh = REPO_ROOT / "install.sh"
        text = install_sh.read_text()
        # After the table is added, it must map no-full-suite → Bash
        # and no-duplicate-read → Read.  Look for the table's presence.
        assert "no-full-suite.sh" in text and "Bash" in text
        # The table must mention the Read guard and its matcher
        assert "no-duplicate-read.sh" in text, (
            "install.sh has no declaration for no-duplicate-read.sh"
        )
        assert "Read" in text, (
            "install.sh has no Read matcher for the duplicate-read guard"
        )
        # The table must be a structured declaration, not two hard-coded lines.
        # After generalisation, hook_cmd should NOT be hard-coded.
        assert 'hook_cmd="$hooks_dir/no-full-suite.sh"' not in text, (
            "install.sh still hard-codes hook_cmd — expected a table"
        )


# ── AC-2: both hooks registered after reconcile ──────────────────────────────

def _extract_reconcile_python() -> str:
    """Extract the Python block from reconcile_hooks_settings in install.sh.

    Returns the raw Python source code embedded between the PYEOF markers.
    """
    install_sh = REPO_ROOT / "install.sh"
    text = install_sh.read_text()
    start = text.find("reconcile_hooks_settings() {")
    if start == -1:
        raise RuntimeError("reconcile_hooks_settings not found in install.sh")
    # Find the heredoc body between the PYEOF markers
    py_start = text.find("<<'PYEOF'\n", start)
    if py_start == -1:
        py_start = text.find('<<PYEOF\n', start)
    if py_start == -1:
        raise RuntimeError("PYEOF heredoc not found")
    py_start = text.index("\n", py_start) + 1  # skip the marker line
    py_end = text.find("\nPYEOF", py_start)
    if py_end == -1:
        raise RuntimeError("closing PYEOF not found")
    return text[py_start:py_end]


def _run_reconcile_multi(hooks_dir: str, *, apply: bool = True) -> str:
    """Run the ACTUAL reconcile Python extracted from install.sh.

    This is the real code — if it can't handle multiple matchers, the test
    fails.  No mocking.
    """
    settings_path = os.path.join(os.path.dirname(hooks_dir), "settings.json")
    hook_cmd = os.path.join(hooks_dir, "no-full-suite.sh")
    script = _extract_reconcile_python()
    result = subprocess.run(
        ["python3", "-", settings_path, hook_cmd, "1" if apply else "0"],
        input=script, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"reconcile failed: {result.stderr}"
    return result.stdout.strip()




class TestSettingsReconcileBothHooks:
    """AC-2: after reconcile, settings contains both Bash and Read entries."""

    def test_both_hooks_registered(self, tmp_path: Path) -> None:
        """Reconcile creates entries for Bash/no-full-suite and Read/no-duplicate-read."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings()
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile_multi(hooks_dir)

        with open(settings_path) as f:
            result = json.load(f)
        pre_tool = result["hooks"]["PreToolUse"]
        matchers = {e["matcher"]: e for e in pre_tool}
        assert "Bash" in matchers, "Bash matcher entry missing"
        assert "Read" in matchers, "Read matcher entry missing"
        bash_cmds = [h["command"] for h in matchers["Bash"]["hooks"]]
        read_cmds = [h["command"] for h in matchers["Read"]["hooks"]]
        assert any("no-full-suite.sh" in c for c in bash_cmds)
        assert any("no-duplicate-read.sh" in c for c in read_cmds)

    def test_both_hooks_idempotent(self, tmp_path: Path) -> None:
        """Running reconcile twice with the table produces no diff."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings()
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile_multi(hooks_dir)
        with open(settings_path) as f:
            first = f.read()

        _run_reconcile_multi(hooks_dir)
        with open(settings_path) as f:
            second = f.read()

        assert first == second


# ── AC-3: Read guard is worker-only ──────────────────────────────────────────

class TestReadGuardWorkerOnly:
    """AC-3: the Read guard appears in worker but not in ~/.claude."""

    def test_read_in_worker_settings(self, tmp_path: Path) -> None:
        """Worker settings.json contains a Read matcher with no-duplicate-read."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings()
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile_multi(hooks_dir)

        with open(settings_path) as f:
            result = json.load(f)
        pre_tool = result["hooks"]["PreToolUse"]
        matchers = {e["matcher"]: e for e in pre_tool}
        assert "Read" in matchers, "Read entry missing from worker"

    def test_no_read_in_interactive_settings(self, tmp_path: Path) -> None:
        """Interactive ~/.claude settings.json gains no Read entry after reconcile.

        This test also verifies that the Read guard IS registered on the
        worker — without that prerequisite, the "not in" check passes
        vacuously.  Both assertions must be tested against the same
        reconcile invocation with host-scoped behaviour.
        """
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        settings_path = str(tmp_path / "settings.json")
        data = _make_settings()
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

        _run_reconcile_multi(hooks_dir)

        with open(settings_path) as f:
            result = json.load(f)
        pre_tool = result["hooks"]["PreToolUse"]
        matchers = {e["matcher"]: e for e in pre_tool}
        # The reconcile MUST register the Read guard (this will fail until
        # step 1/2 are implemented — which is the point).
        assert "Read" in matchers, (
            "Read guard not registered — host-scoping test requires it first"
        )


# ── AC-7: no-full-suite.sh byte-identical ─────────────────────────────────────

class TestNoFullSuiteUnchanged:
    """AC-7: the no-full-suite.sh hook file is unchanged after reconcile."""

    def test_hook_file_unchanged(self) -> None:
        """The hook file has the same content it had before this sub-plan."""
        hook = REPO_ROOT / "hooks" / "no-full-suite.sh"
        digest = hashlib.sha256(hook.read_bytes()).hexdigest()
        assert digest == "f3866a4ca2fd5862d738ee5153a4cdc03b3b4cf795e875809566bde60b54f447"
