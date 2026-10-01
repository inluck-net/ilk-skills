"""Pin install.sh behaviours for worker-home hook deployment.

AC-1: --apply produces symlink + .bak + Bash matcher in worker settings.json.
AC-2: dry-run is a no-op and prints replace-file (backup).
AC-3: with no .claude-worker dir, nothing is created.
AC-4: second --apply is a no-op (no new .bak, byte-identical settings.json).
AC-5: interactive ~/.claude gets no worker-only rows.

These invoke the real install.sh with HOME pinned to tmp_path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


def _run_install(tmp_home: Path, *, apply: bool = True,
                 extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run install.sh with HOME=tmp_home and return the result."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_home)
    args = ["bash", str(INSTALL_SH)]
    if apply:
        args.append("--apply")
    else:
        args.append("--dry-run")
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(
        args,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        env=env,
        timeout=30,
    )


def _make_stopgap_settings(tmp_home: Path) -> Path:
    """Create the stopgap shape in tmp_home/.claude-worker/.

    Returns the path to the stopgap hook file.
    """
    worker_hooks = tmp_home / ".claude-worker" / "hooks"
    worker_hooks.mkdir(parents=True, exist_ok=True)
    # The stopgap: a regular file (not a symlink)
    stopgap = worker_hooks / "no-live-clone-edit.py"
    stopgap.write_text("# stopgap hook\nprint('hello')\n")
    # Settings with only the Edit matcher (no Bash row)
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write|MultiEdit|NotebookEdit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(stopgap),
                        }
                    ],
                }
            ]
        }
    }
    settings_path = tmp_home / ".claude-worker" / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return stopgap


# -- AC-1: --apply produces symlink + .bak + Bash matcher ---------------------

class TestApplyDeploysWorkerHooks:
    """AC-1: install.sh --apply deploys hooks to .claude-worker."""

    def test_hook_becomes_symlink(self, tmp_path: Path) -> None:
        """no-live-clone-edit.py becomes a symlink into the repo."""
        _make_stopgap_settings(tmp_path)
        result = _run_install(tmp_path, apply=True)
        assert result.returncode == 0, f"install failed: {result.stderr}"
        hook = tmp_path / ".claude-worker" / "hooks" / "no-live-clone-edit.py"
        assert hook.is_symlink(), "hook is not a symlink"
        target = hook.resolve()
        assert target == (REPO_ROOT / "hooks" / "no-live-clone-edit.py").resolve()

    def test_backup_file_created(self, tmp_path: Path) -> None:
        """A .bak-* file holds the old stopgap bytes."""
        stopgap = _make_stopgap_settings(tmp_path)
        original_content = stopgap.read_text()
        result = _run_install(tmp_path, apply=True)
        assert result.returncode == 0, f"install failed: {result.stderr}"
        hooks_dir = tmp_path / ".claude-worker" / "hooks"
        bak_files = list(hooks_dir.glob("no-live-clone-edit.py.bak-*"))
        assert len(bak_files) == 1, f"expected 1 .bak file, got {len(bak_files)}"
        assert bak_files[0].read_text() == original_content

    def test_bash_matcher_added(self, tmp_path: Path) -> None:
        """settings.json gains a Bash matcher running the hook."""
        _make_stopgap_settings(tmp_path)
        result = _run_install(tmp_path, apply=True)
        assert result.returncode == 0, f"install failed: {result.stderr}"
        settings_path = tmp_path / ".claude-worker" / "settings.json"
        with open(settings_path) as f:
            settings = json.load(f)
        pre_tool = settings.get("hooks", {}).get("PreToolUse", [])
        matchers = {e.get("matcher"): e for e in pre_tool}
        assert "Bash" in matchers, "Bash matcher missing"
        bash_hooks = [h["command"] for h in matchers["Bash"]["hooks"]]
        assert any("no-live-clone-edit.py" in c for c in bash_hooks), (
            "Bash matcher does not reference no-live-clone-edit.py"
        )


# -- AC-2: dry-run is a no-op -------------------------------------------------

class TestDryRunNoOp:
    """AC-2: dry-run makes no changes and prints replace-file (backup)."""

    def test_dry_run_prints_replace_file(self, tmp_path: Path) -> None:
        """Dry-run output names replace-file (backup) for the stopgap."""
        _make_stopgap_settings(tmp_path)
        result = _run_install(tmp_path, apply=False)
        assert result.returncode == 0, f"dry-run failed: {result.stderr}"
        assert "replace-file" in result.stdout or "replace-file (backup)" in result.stdout, (
            f"expected replace-file in output: {result.stdout}"
        )

    def test_dry_run_makes_no_change(self, tmp_path: Path) -> None:
        """Tree bytes are identical before and after dry-run."""
        _make_stopgap_settings(tmp_path)

        def _snapshot(root: Path) -> dict[str, bytes]:
            """Snapshot dotfiles and dotdirs, excluding Python/system caches."""
            snap = {}
            # Only snapshot the relevant dotdirs that install.sh touches
            for dotdir in [".claude-worker", ".claude", ".cursor", ".codex"]:
                target = root / dotdir
                if not target.exists():
                    continue
                for p in sorted(target.rglob("*")):
                    if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                        snap[str(p.relative_to(root))] = p.read_bytes()
            return snap

        before = _snapshot(tmp_path)
        result = _run_install(tmp_path, apply=False)
        assert result.returncode == 0, f"dry-run failed: {result.stderr}"
        after = _snapshot(tmp_path)
        assert before == after, "dry-run modified the tree"


# -- AC-3: no .claude-worker -> nothing created --------------------------------

class TestNoWorkerDirCreated:
    """AC-3: with no .claude-worker dir, install.sh creates nothing there."""

    def test_no_worker_dir_created(self, tmp_path: Path) -> None:
        """install.sh --apply does not create .claude-worker/."""
        assert not (tmp_path / ".claude-worker").exists()
        result = _run_install(tmp_path, apply=True)
        assert result.returncode == 0, f"install failed: {result.stderr}"
        assert not (tmp_path / ".claude-worker").exists(), (
            ".claude-worker was created but should not have been"
        )


# -- AC-4: second --apply is a no-op ------------------------------------------

class TestIdempotentApply:
    """AC-4: running --apply twice produces no new .bak and byte-identical settings.

    These tests depend on the first run doing its job (symlink + backup + Bash
    matcher).  If the first run doesn't deploy, the test fails at that
    assertion, which is the red-first behaviour.
    """

    def test_second_apply_no_new_backup(self, tmp_path: Path) -> None:
        """No additional .bak file on second run."""
        _make_stopgap_settings(tmp_path)
        hooks_dir = tmp_path / ".claude-worker" / "hooks"
        # First run: must create exactly one .bak
        result1 = _run_install(tmp_path, apply=True)
        assert result1.returncode == 0, f"first install failed: {result1.stderr}"
        bak_count_1 = len(list(hooks_dir.glob("no-live-clone-edit.py.bak-*")))
        assert bak_count_1 == 1, f"first run should create 1 .bak, got {bak_count_1}"
        # Second run: no new .bak
        result2 = _run_install(tmp_path, apply=True)
        assert result2.returncode == 0, f"second install failed: {result2.stderr}"
        bak_count_2 = len(list(hooks_dir.glob("no-live-clone-edit.py.bak-*")))
        assert bak_count_1 == bak_count_2, (
            f"second run created new .bak: {bak_count_1} -> {bak_count_2}"
        )

    def test_second_apply_settings_identical(self, tmp_path: Path) -> None:
        """settings.json is byte-identical after second run."""
        _make_stopgap_settings(tmp_path)
        # First run: must add Bash matcher
        result1 = _run_install(tmp_path, apply=True)
        assert result1.returncode == 0, f"first install failed: {result1.stderr}"
        settings_path = tmp_path / ".claude-worker" / "settings.json"
        with open(settings_path) as f:
            s1 = json.load(f)
        matchers = {e.get("matcher") for e in s1.get("hooks", {}).get("PreToolUse", [])}
        assert "Bash" in matchers, "first run did not add Bash matcher"
        # Second run: settings unchanged
        first_text = settings_path.read_text()
        result2 = _run_install(tmp_path, apply=True)
        assert result2.returncode == 0, f"second install failed: {result2.stderr}"
        second_text = settings_path.read_text()
        assert first_text == second_text, "settings.json changed on second run"


# -- AC-5: interactive home gets no worker-only rows ---------------------------

class TestInteractiveHomeClean:
    """AC-5: the interactive home ~/.claude gets no worker-only rows."""

    def test_interactive_home_no_worker_rows(self, tmp_path: Path) -> None:
        """~/.claude/settings.json has no worker-only hook entries."""
        result = _run_install(tmp_path, apply=True)
        assert result.returncode == 0, f"install failed: {result.stderr}"
        settings_path = tmp_path / ".claude" / "settings.json"
        if not settings_path.exists():
            return
        with open(settings_path) as f:
            settings = json.load(f)
        pre_tool = settings.get("hooks", {}).get("PreToolUse", [])
        for entry in pre_tool:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                assert "no-live-clone-edit" not in cmd, (
                    f"worker-only hook appeared in interactive settings: {cmd}"
                )