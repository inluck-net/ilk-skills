"""hooks/no-sealed-read.py denies every tool call that names the sealed slice.

RSI design point 2 (Chad, Oct 6). Each case runs the real hook as a
subprocess with a PreToolUse event on stdin, the way Claude Code does.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
HOOK = _REPO / "hooks" / "no-sealed-read.py"


def _run(tool_name: str, tool_input: dict, tmp_path: Path, *, data_home: str | None = None,
         raw: str | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("ILK_DATA_HOME", "ILK_DATA_DIR")}
    env["HOME"] = str(tmp_path / "home")
    if data_home:
        env["ILK_DATA_HOME"] = data_home
    stdin = raw if raw is not None else json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input})
    r = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True,
                       text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _denied(out: str) -> bool:
    if not out.strip():
        return False
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("tool,tool_input", [
    ("Read", {"file_path": "~/.ilk-data/sealed/mutations.json"}),
    ("Read", {"file_path": "/Users/x/.ilk-data/sealed/mutations.json"}),
    ("Grep", {"pattern": "replacement", "path": "/Users/x/.ilk-data/sealed"}),
    ("Glob", {"pattern": "sealed/**", "path": "/Users/x/.ilk-data"}),
    ("Bash", {"command": "cd ~/.ilk-data && cat sealed/mutations.json"}),
    ("Edit", {"file_path": "/Users/x/.ilk-data/sealed/mutations.json",
              "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": "/Users/x/.ilk-data/sealed/x.json", "content": "{}"}),
])
def test_a_tool_call_naming_the_sealed_slice_is_denied(tmp_path, tool, tool_input) -> None:
    assert _denied(_run(tool, tool_input, tmp_path))


def test_a_custom_data_home_sealed_dir_is_denied(tmp_path: Path) -> None:
    dh = tmp_path / "custom-root"
    out = _run("Read", {"file_path": str(dh / "sealed" / "m.json")}, tmp_path, data_home=str(dh))
    assert _denied(out)


@pytest.mark.parametrize("tool,tool_input", [
    ("Read", {"file_path": "/Users/x/.ilk-data/projects/k/plans/MASTER-a.md"}),
    ("Bash", {"command": "python3 -m pytest tests/invariants -q"}),
    ("Grep", {"pattern": "sealed", "path": "skills/ilk-loop/scripts"}),
])
def test_an_unrelated_call_is_allowed(tmp_path, tool, tool_input) -> None:
    assert _run(tool, tool_input, tmp_path) == ""


def test_garbage_stdin_is_allowed(tmp_path: Path) -> None:
    assert _run("Read", {}, tmp_path, raw="not json") == ""


def test_install_registers_the_hook_for_reads_and_shell_on_worker_homes() -> None:
    text = (_REPO / "install.sh").read_text(encoding="utf-8")
    row = next(l.strip().strip('"') for l in text.splitlines()
               if l.strip().startswith('"no-sealed-read.py:'))
    _name, matcher, hosts = row.split(":")
    assert hosts == "worker"
    assert {"Read", "Grep", "Glob", "Bash", "Edit", "Write"} <= set(matcher.split("|"))


def test_the_hook_is_kernel_tier() -> None:
    kernel = json.loads((_REPO / "skills" / "ilk-loop" / "safety-kernel.json").read_text())
    assert any(e["path"] == "hooks/no-sealed-read.py" for e in kernel["kernel"])
