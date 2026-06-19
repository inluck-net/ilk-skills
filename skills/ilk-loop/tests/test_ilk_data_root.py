"""Tests for unified data-home resolution across the Python toolkit.

Covers:
  AC-1: ilk_data_root() precedence (ILK_DATA_HOME > ILK_DATA_DIR > ~/.ilk-data)
  AC-2: improvement_backlog._backlog_dir() agreement under all three env states
  AC-3: lark_client._resolve_data_dir() agreement + fallback when ilk_paths
        is unimportable
  AC-5: default unchanged (no env vars → ~/.ilk-data)
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Resolve paths relative to this test file.
_HERE = Path(__file__).resolve()
_LOOP_SCRIPTS = _HERE.parent.parent / "scripts"
_FEEDBACK_SCRIPTS = _HERE.parent.parent.parent / "ilk-feedback" / "scripts"
_LARK_SCRIPTS = _HERE.parent.parent.parent / "ilk-lark-tickets" / "scripts"


# ── helpers ──────────────────────────────────────────────────────────────────

def _import_ilk_paths():
    """(Re)import ilk_paths from the sibling scripts dir."""
    if str(_LOOP_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS))
    import ilk_paths
    importlib.reload(ilk_paths)
    return ilk_paths


def _import_improvement_backlog():
    """(Re)import improvement_backlog from the sibling scripts dir."""
    if str(_FEEDBACK_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_FEEDBACK_SCRIPTS))
    import improvement_backlog
    importlib.reload(improvement_backlog)
    return improvement_backlog


def _import_lark_client():
    """(Re)import lark_client from the sibling scripts dir."""
    if str(_LARK_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LARK_SCRIPTS))
    import lark_client
    importlib.reload(lark_client)
    return lark_client


# ── AC-1: ilk_data_root() precedence ────────────────────────────────────────

class TestIlkDataRootPrecedence:
    """ILK_DATA_HOME > ILK_DATA_DIR > ~/.ilk-data."""

    def test_home_overrides_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "a"))
        monkeypatch.setenv("ILK_DATA_DIR", str(tmp_path / "b"))
        m = _import_ilk_paths()
        assert m.ilk_data_root() == (tmp_path / "a").resolve()

    def test_dir_used_when_home_unset(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ILK_DATA_HOME", raising=False)
        monkeypatch.setenv("ILK_DATA_DIR", str(tmp_path / "b"))
        m = _import_ilk_paths()
        assert m.ilk_data_root() == (tmp_path / "b").resolve()

    def test_default_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("ILK_DATA_HOME", raising=False)
        monkeypatch.delenv("ILK_DATA_DIR", raising=False)
        m = _import_ilk_paths()
        assert m.ilk_data_root() == Path.home() / ".ilk-data"


# ── AC-2: improvement_backlog agreement ──────────────────────────────────────

class TestImprovementBacklogAgreement:
    """_backlog_dir() == ilk_data_root() / 'ilk-skills-improvements'."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """Ensure env is clean before each test."""
        monkeypatch.delenv("ILK_DATA_HOME", raising=False)
        monkeypatch.delenv("ILK_DATA_DIR", raising=False)

    def _expected(self, monkeypatch, tmp_path, env_key=None, env_val=None):
        if env_key:
            monkeypatch.setenv(env_key, env_val)
        m = _import_ilk_paths()
        return m.ilk_data_root() / "ilk-skills-improvements"

    def test_home_overrides_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "a"))
        monkeypatch.setenv("ILK_DATA_DIR", str(tmp_path / "b"))
        ib = _import_improvement_backlog()
        expected = (tmp_path / "a").resolve() / "ilk-skills-improvements"
        assert ib._backlog_dir() == expected

    def test_dir_used_when_home_unset(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ILK_DATA_HOME", raising=False)
        monkeypatch.setenv("ILK_DATA_DIR", str(tmp_path / "b"))
        ib = _import_improvement_backlog()
        expected = (tmp_path / "b").resolve() / "ilk-skills-improvements"
        assert ib._backlog_dir() == expected

    def test_default_when_neither_set(self, monkeypatch):
        ib = _import_improvement_backlog()
        expected = Path.home() / ".ilk-data" / "ilk-skills-improvements"
        assert ib._backlog_dir() == expected


# ── AC-3: lark_client agreement ──────────────────────────────────────────────

class TestLarkClientAgreement:
    """_resolve_data_dir() == ilk_data_root() / 'ilk-lark-tickets'."""

    def test_home_overrides_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "a"))
        monkeypatch.setenv("ILK_DATA_DIR", str(tmp_path / "b"))
        lc = _import_lark_client()
        expected = (tmp_path / "a").resolve() / "ilk-lark-tickets"
        assert lc._resolve_data_dir() == expected

    def test_dir_used_when_home_unset(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ILK_DATA_HOME", raising=False)
        monkeypatch.setenv("ILK_DATA_DIR", str(tmp_path / "b"))
        lc = _import_lark_client()
        expected = (tmp_path / "b").resolve() / "ilk-lark-tickets"
        assert lc._resolve_data_dir() == expected

    def test_default_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("ILK_DATA_HOME", raising=False)
        monkeypatch.delenv("ILK_DATA_DIR", raising=False)
        lc = _import_lark_client()
        expected = Path.home() / ".ilk-data" / "ilk-lark-tickets"
        assert lc._resolve_data_dir() == expected


class TestLarkClientFallback:
    """lark_client still resolves when ilk_paths is unimportable."""

    def test_fallback_when_ilk_paths_blocked(self, monkeypatch, tmp_path):
        """Block ilk_paths import and verify lark_client falls back to
        its inline resolver."""
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "x"))
        # Remove ilk_paths from sys.modules so the import inside
        # _resolve_data_root() would fail if the path insert doesn't
        # help. We simulate ilk_paths being truly unimportable by
        # inserting None into sys.modules for it.
        saved = sys.modules.get("ilk_paths")
        sys.modules["ilk_paths"] = None  # type: ignore[assignment]
        try:
            lc = _import_lark_client()
            expected = (tmp_path / "x").resolve() / "ilk-lark-tickets"
            assert lc._resolve_data_dir() == expected
        finally:
            if saved is not None:
                sys.modules["ilk_paths"] = saved
            else:
                sys.modules.pop("ilk_paths", None)

    def test_fallback_default_when_neither_set(self, monkeypatch):
        """Fallback with no env vars yields ~/.ilk-data."""
        monkeypatch.delenv("ILK_DATA_HOME", raising=False)
        monkeypatch.delenv("ILK_DATA_DIR", raising=False)
        saved = sys.modules.get("ilk_paths")
        sys.modules["ilk_paths"] = None  # type: ignore[assignment]
        try:
            lc = _import_lark_client()
            expected = Path.home() / ".ilk-data" / "ilk-lark-tickets"
            assert lc._resolve_data_dir() == expected
        finally:
            if saved is not None:
                sys.modules["ilk_paths"] = saved
            else:
                sys.modules.pop("ilk_paths", None)
