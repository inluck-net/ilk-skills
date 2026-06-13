"""Tests for worker_mcp_edit.py — add an MCP to the worker home correctly.

Hermetic: tmp_path worker homes + fake user credentials; never touches the
real ~/.claude-worker or ~/.claude.

Key guarantee under test: copy_server_oauth copies ONLY the named server's
mcpOAuth and NEVER claudeAiOauth (preserves the worker's Claude isolation).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent  # tools/claude-worker
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from worker_mcp_edit import (  # noqa: E402
    add_server, copy_server_oauth, worker_servers, resolve_worker_home,
    PRESETS,
)


def _write(path: Path, obj: dict, *, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8-sig" if bom else "utf-8")


# ── add_server ──────────────────────────────────────────────────────

class TestAddServer:
    def test_adds_and_preserves_other_keys(self, tmp_path: Path):
        _write(tmp_path / ".claude.json", {"numStartups": 7, "mcpServers": {}})
        add_server(tmp_path, "figma", PRESETS["figma"])
        data = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8-sig"))
        assert data["mcpServers"]["figma"] == PRESETS["figma"]
        assert data["numStartups"] == 7  # preserved

    def test_creates_when_missing(self, tmp_path: Path):
        add_server(tmp_path, "chrome-devtools", PRESETS["chrome-devtools"])
        assert worker_servers(tmp_path) == ["chrome-devtools"]

    def test_idempotent(self, tmp_path: Path):
        add_server(tmp_path, "figma", PRESETS["figma"])
        add_server(tmp_path, "figma", PRESETS["figma"])
        assert worker_servers(tmp_path) == ["figma"]

    def test_bom_input_preserved(self, tmp_path: Path):
        _write(tmp_path / ".claude.json", {"numStartups": 1, "mcpServers": {"a": {}}}, bom=True)
        add_server(tmp_path, "figma", PRESETS["figma"])
        assert worker_servers(tmp_path) == ["a", "figma"]


# ── copy_server_oauth — the isolation guarantee ─────────────────────

class TestCopyServerOauth:
    def _user_cred(self, tmp_path: Path) -> Path:
        p = tmp_path / "user" / ".credentials.json"
        _write(p, {
            "claudeAiOauth": {"accessToken": "PLANNER-SECRET", "refreshToken": "x"},
            "mcpOAuth": {
                "figma|d39d3b6252bc1ac5": {"serverName": "figma", "accessToken": "FIGMA-TOK"},
                "other|zzz": {"serverName": "other", "accessToken": "NOPE"},
            },
        })
        return p

    def test_copies_only_named_server(self, tmp_path: Path):
        wh = tmp_path / "worker"
        out = copy_server_oauth(wh, self._user_cred(tmp_path), "figma")
        assert out is not None
        cred = json.loads(out.read_text(encoding="utf-8-sig"))
        assert list(cred["mcpOAuth"].keys()) == ["figma|d39d3b6252bc1ac5"]
        assert "other|zzz" not in cred["mcpOAuth"]

    def test_never_copies_claude_identity(self, tmp_path: Path):
        wh = tmp_path / "worker"
        out = copy_server_oauth(wh, self._user_cred(tmp_path), "figma")
        cred = json.loads(out.read_text(encoding="utf-8-sig"))
        assert "claudeAiOauth" not in cred  # the whole point

    def test_strips_preexisting_claude_oauth(self, tmp_path: Path):
        wh = tmp_path / "worker"
        # worker already (wrongly) had claudeAiOauth — must be removed
        _write(wh / ".credentials.json", {"claudeAiOauth": {"accessToken": "OLD"}})
        out = copy_server_oauth(wh, self._user_cred(tmp_path), "figma")
        cred = json.loads(out.read_text(encoding="utf-8-sig"))
        assert "claudeAiOauth" not in cred
        assert "figma|d39d3b6252bc1ac5" in cred["mcpOAuth"]

    def test_no_match_returns_none(self, tmp_path: Path):
        wh = tmp_path / "worker"
        out = copy_server_oauth(wh, self._user_cred(tmp_path), "chrome-devtools")
        assert out is None  # chrome-devtools has no mcpOAuth entry

    def test_missing_user_cred(self, tmp_path: Path):
        wh = tmp_path / "worker"
        assert copy_server_oauth(wh, tmp_path / "nope.json", "figma") is None


# ── presets / resolution ────────────────────────────────────────────

def test_presets_shape():
    assert PRESETS["figma"]["type"] == "http"
    assert PRESETS["chrome-devtools"]["type"] == "stdio"


def test_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAUDE_WORKER_HOME", str(tmp_path))
    assert resolve_worker_home() == tmp_path
