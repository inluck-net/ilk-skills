"""Tests for resolve_worker_model helper.

AC-1: settings.json env.ANTHROPIC_MODEL → ("model", "settings")
AC-2: precedence: flag > env > settings > unknown
AC-3: malformed / BOM / absent settings.json → ("", "unknown"), never raises
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the scripts dir is importable
_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from resolve_worker_model import resolve_model


# ---------------------------------------------------------------------------
# AC-1: settings.json env block fallback
# ---------------------------------------------------------------------------

class TestSettingsFallback:
    def test_env_block_model(self, tmp_path: Path) -> None:
        """AC-1: settings.json env.ANTHROPIC_MODEL → ('mimo-v2.5-pro', 'settings')"""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "mimo-v2.5-pro"}}), encoding="utf-8")
        assert resolve_model("", "", tmp_path) == ("mimo-v2.5-pro", "settings")

    def test_env_block_with_bom(self, tmp_path: Path) -> None:
        """AC-3: BOM-prefixed settings.json is still readable."""
        settings = tmp_path / "settings.json"
        content = json.dumps({"env": {"ANTHROPIC_MODEL": "gpt-4o"}})
        settings.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
        assert resolve_model("", "", tmp_path) == ("gpt-4o", "settings")

    def test_env_block_extra_keys_ignored(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "env": {"ANTHROPIC_MODEL": "deepseek-r1", "OTHER": "value"},
            "theme": "dark",
        }), encoding="utf-8")
        assert resolve_model("", "", tmp_path) == ("deepseek-r1", "settings")


# ---------------------------------------------------------------------------
# AC-2: precedence
# ---------------------------------------------------------------------------

class TestPrecedence:
    def test_flag_wins_over_env(self, tmp_path: Path) -> None:
        assert resolve_model("winner", "loser", tmp_path) == ("winner", "flag")

    def test_flag_wins_over_settings(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "loser"}}), encoding="utf-8")
        assert resolve_model("winner", "", tmp_path) == ("winner", "flag")

    def test_env_wins_over_settings(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "loser"}}), encoding="utf-8")
        assert resolve_model("", "winner", tmp_path) == ("winner", "env")

    def test_nothing_anywhere(self, tmp_path: Path) -> None:
        """AC-2: nothing → ('', 'unknown')"""
        assert resolve_model("", "", tmp_path) == ("", "unknown")

    def test_empty_env_block(self, tmp_path: Path) -> None:
        """env block exists but no ANTHROPIC_MODEL key."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"env": {}}), encoding="utf-8")
        assert resolve_model("", "", tmp_path) == ("", "unknown")

    def test_env_block_value_is_empty_string(self, tmp_path: Path) -> None:
        """env.ANTHROPIC_MODEL exists but is '' → treated as absent."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"env": {"ANTHROPIC_MODEL": ""}}), encoding="utf-8")
        assert resolve_model("", "", tmp_path) == ("", "unknown")


# ---------------------------------------------------------------------------
# AC-3: malformed / absent settings.json — never raises
# ---------------------------------------------------------------------------

class TestMalformedSettings:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert resolve_model("", "", tmp_path) == ("", "unknown")

    def test_malformed_json(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text("not json {{{", encoding="utf-8")
        assert resolve_model("", "", tmp_path) == ("", "unknown")

    def test_empty_file(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text("", encoding="utf-8")
        assert resolve_model("", "", tmp_path) == ("", "unknown")

    def test_no_env_key(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        assert resolve_model("", "", tmp_path) == ("", "unknown")

    def test_env_is_not_a_dict(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"env": "bad"}), encoding="utf-8")
        assert resolve_model("", "", tmp_path) == ("", "unknown")

    def test_directory_does_not_exist(self) -> None:
        assert resolve_model("", "", "/nonexistent/path") == ("", "unknown")


# ---------------------------------------------------------------------------
# CLI contract (regression for 1c43749f): the runners invoke the resolver as a
# subprocess with NAMED args. A grep-only gate let a broken invocation ship; this
# actually RUNS the CLI in the exact forms the .ps1 / .sh use.
# ---------------------------------------------------------------------------

import subprocess  # noqa: E402

_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "resolve_worker_model.py")


def _cli(*args: str) -> str:
    out = subprocess.run([sys.executable, _SCRIPT, *args],
                         capture_output=True, text=True, encoding="utf-8")
    return out.stdout.strip()


class TestCli:
    def test_config_dir_only_settings(self, tmp_path: Path) -> None:
        """.ps1 else-branch form: only --config-dir passed (no empty positionals)."""
        (tmp_path / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_MODEL": "mimo-v2.5-pro"}}), encoding="utf-8")
        assert _cli("--config-dir", str(tmp_path)) == "mimo-v2.5-pro|settings"

    def test_sh_form_empty_named_values(self, tmp_path: Path) -> None:
        """.sh form: empty --model/--env-model values must NOT misalign (1c43749f)."""
        (tmp_path / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_MODEL": "mimo-v2.5-pro"}}), encoding="utf-8")
        assert _cli("--model", "", "--env-model", "", "--config-dir", str(tmp_path)) == "mimo-v2.5-pro|settings"

    def test_model_flag_wins(self, tmp_path: Path) -> None:
        assert _cli("--model", "claude-x", "--config-dir", str(tmp_path)) == "claude-x|flag"

    def test_no_args_unknown(self) -> None:
        assert _cli() == "|unknown"
