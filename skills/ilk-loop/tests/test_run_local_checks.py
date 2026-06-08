"""Tests for run_local_checks.run_one — the REAL gate executor the loop uses.

Regression (2026-06-08/09): run_one executed commands via shell=True, i.e.
cmd.exe on Windows, where posix gates (grep, bash -n, jq) don't exist — so every
gate errored and scheduler-dispatched runs shipped UNVERIFIED. It now runs each
command via git-bash (never the WSL shim). These pin pass / fail / error on the
real path (`Invoke-LocalChecks` -> run_local_checks.py -> run_one), not the
orphan ps1 Invoke-LocalCheck.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_local_checks as rlc  # noqa: E402


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")


def test_bash_resolves_and_is_not_the_wsl_shim() -> None:
    b = rlc._resolve_bash()
    assert b, "no bash resolved"
    assert "windowsapps" not in b.lower(), "must not resolve the WSL shim (uses /mnt/c, fails on Windows cwd)"


def test_grep_match_passes(tmp_path: Path) -> None:
    _write(tmp_path, "f.txt", "hello world\n")
    r = rlc.run_one({"command": "grep -q hello f.txt"}, "step", tmp_path)
    assert r.passed is True
    assert r.exit_code == 0


def test_grep_nomatch_fails(tmp_path: Path) -> None:
    _write(tmp_path, "f.txt", "goodbye moon\n")
    r = rlc.run_one({"command": "grep -q hello f.txt"}, "step", tmp_path)
    assert r.passed is False
    assert r.exit_code == 1


def test_unrunnable_command_errors_not_passes(tmp_path: Path) -> None:
    # The original bug class: a posix command that can't execute must NOT pass.
    r = rlc.run_one({"command": "definitely-not-a-cmd-xyzzy"}, "step", tmp_path)
    assert r.passed is False
    assert r.exit_code not in (0, 1)


def test_regex_escape_not_corrupted(tmp_path: Path) -> None:
    # A global backslash->slash rewrite (an earlier mis-fix) would corrupt
    # regex escapes like \+. Ensure the command runs verbatim.
    _write(tmp_path, "f.txt", "build+flash\n")
    r = rlc.run_one({"command": r'grep -Eq "build\+flash" f.txt'}, "step", tmp_path)
    assert r.passed is True
    assert r.exit_code == 0
