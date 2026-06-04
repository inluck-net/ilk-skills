"""Tests for ccswitch_import using synthetic CCSwitch fixtures.

All tests create a temporary SQLite database with known provider data so
no live CCSwitch secrets are required.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Ensure the module under test is importable.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ccswitch_import import (
    Provider,
    check_ccswitch_dir,
    mask_token,
    normalize_for_worker,
    parse_providers_from_db,
    provider_summary,
)


# ── Synthetic fixture helpers ────────────────────────────────────────────────

def _create_ccswitch_db(db_path: Path, providers: list[dict]) -> None:
    """Create a minimal CCSwitch SQLite database with synthetic providers."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE providers (
            id TEXT PRIMARY KEY,
            app_type TEXT,
            name TEXT,
            settings_config TEXT,
            website_url TEXT,
            category TEXT,
            is_current BOOLEAN,
            provider_type TEXT
        )
    """)
    for p in providers:
        cursor.execute(
            "INSERT INTO providers (id, app_type, name, settings_config, "
            "website_url, category, is_current, provider_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p["id"],
                p["app_type"],
                p["name"],
                json.dumps(p.get("settings_config", {})),
                p.get("website_url"),
                p.get("category"),
                p.get("is_current", False),
                p.get("provider_type"),
            ),
        )
    conn.commit()
    conn.close()


PROVIDER_CLAUDE_FULL = {
    "id": "test-provider-1",
    "app_type": "claude",
    "name": "Test Provider",
    "category": "custom",
    "is_current": True,
    "settings_config": {
        "env": {
            "ANTHROPIC_BASE_URL": "https://test.example.com/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "sk-test-secret-token-12345",
            "ANTHROPIC_MODEL": "test-model-v1",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "test-sonnet",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "test-opus",
        },
        "autoCompactEnabled": False,
    },
}

PROVIDER_OFFICIAL = {
    "id": "claude-official",
    "app_type": "claude",
    "name": "Claude Official",
    "category": "official",
    "settings_config": {"env": {}, "model": "opus"},
}

PROVIDER_MISSING_TOKEN = {
    "id": "incomplete",
    "app_type": "claude",
    "name": "Incomplete Provider",
    "category": "custom",
    "settings_config": {
        "env": {
            "ANTHROPIC_BASE_URL": "https://incomplete.example.com",
            "ANTHROPIC_MODEL": "some-model",
        },
    },
}

PROVIDER_CODEX = {
    "id": "codex-official",
    "app_type": "codex",
    "name": "OpenAI Official",
    "category": "official",
    "settings_config": {"auth": {}, "config": ""},
}


@pytest.fixture
def ccswitch_dir(tmp_path: Path):
    """Create a synthetic CCSwitch directory with a populated database."""
    db_path = tmp_path / "cc-switch.db"
    _create_ccswitch_db(db_path, [
        PROVIDER_CLAUDE_FULL,
        PROVIDER_OFFICIAL,
        PROVIDER_MISSING_TOKEN,
        PROVIDER_CODEX,
    ])
    # Also create a settings.json so check_ccswitch_dir passes.
    (tmp_path / "settings.json").write_text("{}")
    return tmp_path


# ── mask_token ───────────────────────────────────────────────────────────────

class TestMaskToken:
    def test_empty(self):
        assert mask_token("") == "(missing)"

    def test_none(self):
        assert mask_token(None) == "(missing)"

    def test_normal_token(self):
        result = mask_token("sk-abcdef1234567890")
        assert "sk-" not in result
        assert "19 chars" in result

    def test_consistent_length(self):
        t = "a" * 100
        assert "100 chars" in mask_token(t)


# ── check_ccswitch_dir ──────────────────────────────────────────────────────

class TestCheckCcsSwitchDir:
    def test_valid_dir(self, ccswitch_dir: Path):
        check_ccswitch_dir(ccswitch_dir)  # should not raise

    def test_missing_dir(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="not found"):
            check_ccswitch_dir(tmp_path / "nonexistent")

    def test_empty_dir(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="No CCSwitch config"):
            check_ccswitch_dir(empty)


# ── parse_providers_from_db ──────────────────────────────────────────────────

class TestParseProvidersFromDb:
    def test_returns_all_providers(self, ccswitch_dir: Path):
        providers = parse_providers_from_db(ccswitch_dir / "cc-switch.db")
        # parse_providers_from_db returns ALL providers; callers filter by is_claude.
        assert len(providers) == 4
        claude = [p for p in providers if p.is_claude]
        assert len(claude) == 3  # full, official, missing-token; not codex

    def test_extracts_env_fields(self, ccswitch_dir: Path):
        providers = parse_providers_from_db(ccswitch_dir / "cc-switch.db")
        full = next(p for p in providers if p.id == "test-provider-1")
        assert full.base_url == "https://test.example.com/anthropic"
        assert full.auth_token == "sk-test-secret-token-12345"
        assert full.model == "test-model-v1"

    def test_extracts_extra_env(self, ccswitch_dir: Path):
        providers = parse_providers_from_db(ccswitch_dir / "cc-switch.db")
        full = next(p for p in providers if p.id == "test-provider-1")
        assert full.extra_env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "test-sonnet"
        assert full.extra_env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "test-opus"

    def test_official_has_empty_env(self, ccswitch_dir: Path):
        providers = parse_providers_from_db(ccswitch_dir / "cc-switch.db")
        official = next(p for p in providers if p.id == "claude-official")
        assert official.base_url == ""
        assert official.auth_token == ""
        assert official.model == ""

    def test_missing_db_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="not found"):
            parse_providers_from_db(tmp_path / "nonexistent.db")


# ── Provider dataclass ──────────────────────────────────────────────────────

class TestProvider:
    def test_is_claude(self):
        p = Provider(id="x", name="x", app_type="claude")
        assert p.is_claude

    def test_is_not_claude(self):
        p = Provider(id="x", name="x", app_type="codex")
        assert not p.is_claude

    def test_is_official(self):
        p = Provider(id="x", name="x", app_type="claude", category="official")
        assert p.is_official

    def test_has_required_env(self):
        p = Provider(id="x", name="x", app_type="claude",
                     base_url="https://x", auth_token="tok", model="m")
        assert p.has_required_env

    def test_missing_base_url(self):
        p = Provider(id="x", name="x", app_type="claude",
                     auth_token="tok", model="m")
        assert not p.has_required_env


# ── normalize_for_worker ────────────────────────────────────────────────────

class TestNormalizeForWorker:
    def test_full_provider(self, ccswitch_dir: Path):
        providers = parse_providers_from_db(ccswitch_dir / "cc-switch.db")
        full = next(p for p in providers if p.id == "test-provider-1")
        env = normalize_for_worker(full)
        assert env["ANTHROPIC_BASE_URL"] == "https://test.example.com/anthropic"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-secret-token-12345"
        assert env["ANTHROPIC_MODEL"] == "test-model-v1"
        assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "test-sonnet"

    def test_incomplete_raises(self, ccswitch_dir: Path):
        providers = parse_providers_from_db(ccswitch_dir / "cc-switch.db")
        incomplete = next(p for p in providers if p.id == "incomplete")
        with pytest.raises(ValueError, match="missing required fields"):
            normalize_for_worker(incomplete)

    def test_official_raises(self, ccswitch_dir: Path):
        providers = parse_providers_from_db(ccswitch_dir / "cc-switch.db")
        official = next(p for p in providers if p.id == "claude-official")
        with pytest.raises(ValueError, match="missing required fields"):
            normalize_for_worker(official)


# ── provider_summary ────────────────────────────────────────────────────────

class TestProviderSummary:
    def test_redacts_token(self, ccswitch_dir: Path):
        providers = parse_providers_from_db(ccswitch_dir / "cc-switch.db")
        full = next(p for p in providers if p.id == "test-provider-1")
        summary = provider_summary(full)
        assert "sk-test" not in summary["auth_token"]
        assert "set" in summary["auth_token"]

    def test_includes_metadata(self, ccswitch_dir: Path):
        providers = parse_providers_from_db(ccswitch_dir / "cc-switch.db")
        full = next(p for p in providers if p.id == "test-provider-1")
        summary = provider_summary(full)
        assert summary["id"] == "test-provider-1"
        assert summary["name"] == "Test Provider"
        assert summary["category"] == "custom"
        assert summary["is_current"] is True
