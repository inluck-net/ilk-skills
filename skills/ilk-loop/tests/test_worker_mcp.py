"""Tests for worker_mcp.py — report the loop worker's MCP server set.

Hermetic: tmp_path worker homes; never touches the real ~/.claude-worker.

AC-1: list reads <worker-home>/.claude.json mcpServers (sorted).
AC-2: empty {} / missing file / missing key -> [] (graceful).
      env-var override + explicit param override + BOM (utf-8-sig).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from worker_mcp import resolve_worker_home, worker_mcp_servers  # noqa: E402


def _write_claude_json(home: Path, mcp: dict | None, *, bom: bool = False) -> None:
    home.mkdir(parents=True, exist_ok=True)
    obj: dict = {"numStartups": 1}
    if mcp is not None:
        obj["mcpServers"] = mcp
    text = json.dumps(obj, indent=2)
    encoding = "utf-8-sig" if bom else "utf-8"
    (home / ".claude.json").write_text(text, encoding=encoding)


# ── populated ───────────────────────────────────────────────────────

def test_populated_sorted(tmp_path: Path):
    _write_claude_json(tmp_path, {"figma": {"type": "http"}, "chrome-devtools": {"type": "stdio"}})
    assert worker_mcp_servers(tmp_path) == ["chrome-devtools", "figma"]


def test_empty_mcpservers(tmp_path: Path):
    _write_claude_json(tmp_path, {})
    assert worker_mcp_servers(tmp_path) == []


def test_missing_key(tmp_path: Path):
    _write_claude_json(tmp_path, None)  # no mcpServers key
    assert worker_mcp_servers(tmp_path) == []


def test_missing_claude_json(tmp_path: Path):
    # tmp_path exists but has no .claude.json
    assert worker_mcp_servers(tmp_path) == []


def test_malformed_json(tmp_path: Path):
    (tmp_path / ".claude.json").write_text("{ not valid json", encoding="utf-8")
    assert worker_mcp_servers(tmp_path) == []


def test_bom_prefixed(tmp_path: Path):
    _write_claude_json(tmp_path, {"figma": {"type": "http"}}, bom=True)
    assert worker_mcp_servers(tmp_path) == ["figma"]


# ── resolution ──────────────────────────────────────────────────────

def test_explicit_param_override(tmp_path: Path):
    _write_claude_json(tmp_path, {"figma": {}})
    assert resolve_worker_home(str(tmp_path)) == tmp_path
    assert worker_mcp_servers(str(tmp_path)) == ["figma"]


def test_env_var_override(tmp_path: Path, monkeypatch):
    _write_claude_json(tmp_path, {"chrome-devtools": {}})
    monkeypatch.setenv("CLAUDE_WORKER_HOME", str(tmp_path))
    assert resolve_worker_home() == tmp_path
    assert worker_mcp_servers(resolve_worker_home()) == ["chrome-devtools"]


def test_explicit_beats_env(tmp_path: Path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("CLAUDE_WORKER_HOME", str(other))
    assert resolve_worker_home(str(tmp_path)) == tmp_path


def test_default_home_when_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_WORKER_HOME", raising=False)
    assert resolve_worker_home() == Path.home() / ".claude-worker"
