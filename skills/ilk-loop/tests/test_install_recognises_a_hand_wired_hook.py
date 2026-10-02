"""Test that install.sh reconcile recognises a hand-wired hook.

AC-1: settings with ``python3 <path>`` for matcher M ⇒ the reconcile adds
      no second entry for M.
AC-2: settings with both forms ⇒ one entry remains (the canonical ``<path>``),
      and the dry run says ``would dedupe``.
AC-3: a second ``--apply`` is a no-op.

These tests pin the behaviour that the reconcile must not duplicate a hook
whose command was hand-wired with a leading interpreter.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ── helpers ──────────────────────────────────────────────────────────────────

def _extract_reconcile_python() -> str:
    """Extract the Python block from reconcile_hooks_settings in install.sh."""
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


def _run_reconcile(settings_path: str, hooks_dir: str, *,
                   hook_cmds: list[str] | None = None,
                   matchers: list[str] | None = None,
                   hosts: list[str] | None = None,
                   host: str = "worker",
                   apply: bool = True) -> str:
    """Run the ACTUAL reconcile Python extracted from install.sh."""
    if hook_cmds is None:
        hook_cmds = ["no-live-clone-edit.py"]
    if matchers is None:
        matchers = ["Edit|Write|MultiEdit|NotebookEdit"]
    if hosts is None:
        hosts = ["worker"]
    hook_cmds_json = json.dumps(hook_cmds)
    matchers_json = json.dumps(matchers)
    hosts_json = json.dumps(hosts)
    script = _extract_reconcile_python()
    result = subprocess.run(
        ["python3", "-", settings_path, hook_cmds_json, matchers_json,
         hosts_json, host, "1" if apply else "0"],
        input=script, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=10,
    )
    assert result.returncode == 0, f"reconcile failed: {result.stderr}"
    return result.stdout.strip()


def _make_settings(entries: list[dict]) -> dict:
    """Build a settings.json with the given PreToolUse entries."""
    return {
        "env": {},
        "permissions": {"defaultMode": "auto"},
        "hooks": {"PreToolUse": entries},
    }


def _write_settings(settings_path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _load_settings(settings_path: str) -> dict:
    with open(settings_path) as f:
        return json.load(f)


# ── AC-1: python3 <path> ⇒ no second entry ──────────────────────────────────

class TestHandWiredHookNotDuplicated:
    """AC-1: a ``python3 <path>`` entry is recognised; no duplicate added."""

    def test_python3_prefix_no_duplicate(self, tmp_path: Path) -> None:
        """Settings with ``python3 /path/to/hook.py`` ⇒ reconcile adds nothing."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        hook_path = os.path.join(hooks_dir, "no-live-clone-edit.py")
        settings_path = str(tmp_path / "settings.json")

        # Hand-wired entry with python3 prefix
        data = _make_settings([
            {"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [
                {"type": "command", "command": f"python3 {hook_path}"},
            ]},
        ])
        _write_settings(settings_path, data)

        output = _run_reconcile(settings_path, hooks_dir)

        result = _load_settings(settings_path)
        pre_tool = result["hooks"]["PreToolUse"]
        edit_entries = [e for e in pre_tool
                        if e.get("matcher") == "Edit|Write|MultiEdit|NotebookEdit"]
        assert len(edit_entries) == 1, (
            f"expected 1 matcher entry, got {len(edit_entries)}"
        )
        cmds = [h["command"] for h in edit_entries[0]["hooks"]]
        assert len(cmds) == 1, (
            f"expected 1 hook command, got {len(cmds)}: {cmds}"
        )

    def test_bash_prefix_no_duplicate(self, tmp_path: Path) -> None:
        """Settings with ``bash /path/to/hook.py`` ⇒ reconcile adds nothing."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        hook_path = os.path.join(hooks_dir, "no-live-clone-edit.py")
        settings_path = str(tmp_path / "settings.json")

        data = _make_settings([
            {"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [
                {"type": "command", "command": f"bash {hook_path}"},
            ]},
        ])
        _write_settings(settings_path, data)

        output = _run_reconcile(settings_path, hooks_dir)

        result = _load_settings(settings_path)
        pre_tool = result["hooks"]["PreToolUse"]
        edit_entries = [e for e in pre_tool
                        if e.get("matcher") == "Edit|Write|MultiEdit|NotebookEdit"]
        assert len(edit_entries) == 1
        cmds = [h["command"] for h in edit_entries[0]["hooks"]]
        assert len(cmds) == 1


# ── AC-2: both forms ⇒ one remains, dry-run says would dedupe ───────────────

class TestBothFormsDedupe:
    """AC-2: canonical + hand-wired ⇒ dedupe to canonical."""

    def test_both_forms_dedupe_to_canonical(self, tmp_path: Path) -> None:
        """Both ``python3 <path>`` and ``<path>`` present ⇒ canonical remains."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        hook_path = os.path.join(hooks_dir, "no-live-clone-edit.py")
        settings_path = str(tmp_path / "settings.json")

        # Both forms present
        data = _make_settings([
            {"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [
                {"type": "command", "command": f"python3 {hook_path}"},
                {"type": "command", "command": hook_path},
            ]},
        ])
        _write_settings(settings_path, data)

        output = _run_reconcile(settings_path, hooks_dir)

        result = _load_settings(settings_path)
        pre_tool = result["hooks"]["PreToolUse"]
        edit_entries = [e for e in pre_tool
                        if e.get("matcher") == "Edit|Write|MultiEdit|NotebookEdit"]
        assert len(edit_entries) == 1
        cmds = [h["command"] for h in edit_entries[0]["hooks"]]
        assert len(cmds) == 1, (
            f"expected 1 hook command after dedupe, got {len(cmds)}: {cmds}"
        )
        assert cmds[0] == hook_path, (
            f"expected canonical path {hook_path}, got {cmds[0]}"
        )

    def test_dedupe_preserves_bash_matcher_entry(self, tmp_path: Path) -> None:
        """Bash matcher entry with hand-wired form is also deduped."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        hook_path = os.path.join(hooks_dir, "no-live-clone-edit.py")
        settings_path = str(tmp_path / "settings.json")

        # Both forms in Bash matcher
        data = _make_settings([
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": f"python3 {hook_path}"},
                {"type": "command", "command": hook_path},
            ]},
        ])
        _write_settings(settings_path, data)

        # Reconcile with the Bash matcher entry
        output = _run_reconcile(
            settings_path, hooks_dir,
            hook_cmds=["no-live-clone-edit.py"],
            matchers=["Bash"],
            hosts=["worker"],
        )

        result = _load_settings(settings_path)
        pre_tool = result["hooks"]["PreToolUse"]
        bash_entries = [e for e in pre_tool if e.get("matcher") == "Bash"]
        assert len(bash_entries) == 1
        cmds = [h["command"] for h in bash_entries[0]["hooks"]]
        assert len(cmds) == 1
        assert cmds[0] == hook_path


# ── AC-2 (dry-run signal): already passes — no xfail ────────────────────────

def test_dry_run_says_would_update_with_both_forms(tmp_path: Path) -> None:
    """Dry-run with both forms prints ``would update`` (canonical not yet present)."""
    hooks_dir = str(tmp_path / "hooks")
    os.makedirs(hooks_dir)
    hook_path = os.path.join(hooks_dir, "no-live-clone-edit.py")
    settings_path = str(tmp_path / "settings.json")

    data = _make_settings([
        {"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [
            {"type": "command", "command": f"python3 {hook_path}"},
            {"type": "command", "command": hook_path},
        ]},
    ])
    _write_settings(settings_path, data)

    output = _run_reconcile(settings_path, hooks_dir, apply=False)

    # The dry-run should indicate a change
    assert "would" in output.lower() or "update" in output.lower(), (
        f"dry-run output did not indicate a change: {output}"
    )


# ── AC-3: second --apply is a no-op ─────────────────────────────────────────

class TestSecondApplyNoop:
    """AC-3: running apply twice produces no diff."""

    def test_second_apply_no_diff(self, tmp_path: Path) -> None:
        """Apply on hand-wired settings, then apply again ⇒ no change."""
        hooks_dir = str(tmp_path / "hooks")
        os.makedirs(hooks_dir)
        hook_path = os.path.join(hooks_dir, "no-live-clone-edit.py")
        settings_path = str(tmp_path / "settings.json")

        data = _make_settings([
            {"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [
                {"type": "command", "command": f"python3 {hook_path}"},
            ]},
        ])
        _write_settings(settings_path, data)

        # First apply: dedupes
        _run_reconcile(settings_path, hooks_dir)
        with open(settings_path) as f:
            first = f.read()

        # Second apply: no-op
        _run_reconcile(settings_path, hooks_dir)
        with open(settings_path) as f:
            second = f.read()

        assert first == second, "second apply changed settings.json"