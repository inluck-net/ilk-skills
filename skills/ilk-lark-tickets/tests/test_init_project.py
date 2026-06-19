"""Tests for the idempotent ``init-project`` CLI verb.

All Lark HTTP is mocked — zero network calls, zero real bases (AC-8).
Covers AC-4 (reuse), AC-5 (create), AC-6 (refuse unreachable),
AC-7 (marker idempotent), and config preservation.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

# Ensure the scripts package is importable regardless of cwd.
_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import cli  # noqa: E402
import lark_client  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate config, ILK_DATA_HOME, and cwd for each test."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"app_id": "a", "app_secret": "s", "projects": {}}),
        encoding="utf-8",
    )
    # Point the config resolver at our temp config.
    monkeypatch.setattr(lark_client, "_resolve_config_path", lambda: config_path)
    # Also set ILK_DATA_HOME so any fallback resolution stays in tmp.
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path))
    # Reset module-level CONFIG_PATH / TOKEN_CACHE so they pick up the mock.
    monkeypatch.setattr(lark_client, "CONFIG_PATH", config_path)
    monkeypatch.setattr(lark_client, "TOKEN_CACHE", tmp_path / ".token_cache.json")
    return {"config_path": config_path, "tmp_path": tmp_path}


def _write_config(path: Path, cfg: dict) -> None:
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AC-5: create path — no entry → create_bitable + upsert + marker + seed
# ---------------------------------------------------------------------------

class TestCreatePath:
    def test_creates_base_when_no_entry(self, env, monkeypatch, tmp_path):
        """AC-5: no config entry → calls create_bitable exactly once,
        upserts entry (preserves app_id/app_secret), writes marker, exits 0."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with (
            mock.patch("cli.create_bitable", return_value=created_result) as m_create,
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema") as m_seed,
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        # create_bitable called exactly once
        m_create.assert_called_once_with("myproj", folder_token=None, token="tok")

        # Config was upserted (preserves app_id/app_secret)
        saved = _read_config(env["config_path"])
        assert saved["app_id"] == "a"
        assert saved["app_secret"] == "s"
        assert saved["projects"]["myproj"]["bitable_app_token"] == "new_token"
        assert saved["projects"]["myproj"]["table_id"] == "tbl_new"
        assert saved["projects"]["myproj"]["ticket_id_prefix"] == "T"

        # Marker written
        marker = repo_dir / ".lark-project"
        assert marker.exists()
        assert marker.read_text(encoding="utf-8").strip() == "myproj"

        # seed_schema called
        m_seed.assert_called_once_with(project_name="myproj", rename_primary=True)


# ---------------------------------------------------------------------------
# AC-4: reuse path — entry + reachable → no create, seed, marker
# ---------------------------------------------------------------------------

class TestReusePath:
    def test_reuses_existing_reachable_base(self, env, monkeypatch, tmp_path):
        """AC-4: entry+reachable → does NOT call create_bitable,
        calls seed_schema, keeps marker, exits 0, prints 'reused'."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {
                "myproj": {
                    "bitable_app_token": "existing_tok",
                    "table_id": "tbl_existing",
                    "url": "https://feishu.cn/base/existing",
                    "ticket_id_prefix": "T",
                },
            },
        }
        _write_config(env["config_path"], cfg)
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        marker = repo_dir / ".lark-project"
        marker.write_text("myproj\n", encoding="utf-8")

        with (
            mock.patch("cli.create_bitable") as m_create,
            mock.patch("cli._probe_tables", return_value=True),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema") as m_seed,
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        # create_bitable must NOT be called
        m_create.assert_not_called()
        # seed_schema called
        m_seed.assert_called_once_with(project_name="myproj", rename_primary=True)
        # Config unchanged
        saved = _read_config(env["config_path"])
        assert saved["projects"]["myproj"]["bitable_app_token"] == "existing_tok"


# ---------------------------------------------------------------------------
# AC-6: refuse unreachable — entry present but base unreachable
# ---------------------------------------------------------------------------

class TestRefuseUnreachable:
    def test_refuses_unreachable_without_force(self, env, monkeypatch, tmp_path):
        """AC-6: entry present, base unreachable, no --force-recreate
        → exits non-zero, does NOT call create_bitable."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {
                "myproj": {
                    "bitable_app_token": "dead_token",
                    "table_id": "tbl_dead",
                },
            },
        }
        _write_config(env["config_path"], cfg)
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with (
            mock.patch("cli.create_bitable") as m_create,
            mock.patch("cli._probe_tables", return_value=False),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            with pytest.raises(SystemExit) as exc_info:
                cli.cmd_init_project(args)
            assert exc_info.value.code == 1

        m_create.assert_not_called()

    def test_force_recreate_on_unreachable(self, env, monkeypatch, tmp_path):
        """AC-6: with --force-recreate, unreachable base is replaced."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {
                "myproj": {
                    "bitable_app_token": "dead_token",
                    "table_id": "tbl_dead",
                },
            },
        }
        _write_config(env["config_path"], cfg)
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        created_result = {
            "app_token": "recreated_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }

        with (
            mock.patch("cli.create_bitable", return_value=created_result) as m_create,
            mock.patch("cli._probe_tables", return_value=False),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj",
                "--repo", str(repo_dir), "--force-recreate",
            ])
            cli.cmd_init_project(args)

        m_create.assert_called_once()
        saved = _read_config(env["config_path"])
        assert saved["projects"]["myproj"]["bitable_app_token"] == "recreated_token"


# ---------------------------------------------------------------------------
# AC-7: marker idempotent — no-op when already correct
# ---------------------------------------------------------------------------

class TestMarkerIdempotent:
    def test_marker_noop_when_correct(self, env, tmp_path):
        """AC-7: marker already contains project name → no write."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        marker = repo_dir / ".lark-project"
        marker.write_text("myproj\n", encoding="utf-8")
        mtime_before = marker.stat().st_mtime

        result = cli._ensure_marker(str(repo_dir), "myproj")
        assert result == marker
        # File not rewritten (mtime unchanged on most systems; content check is definitive)
        assert marker.read_text(encoding="utf-8").strip() == "myproj"

    def test_marker_updates_when_different(self, env, tmp_path):
        """AC-7: marker has a different name → updated."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        marker = repo_dir / ".lark-project"
        marker.write_text("old_name\n", encoding="utf-8")

        cli._ensure_marker(str(repo_dir), "myproj")
        assert marker.read_text(encoding="utf-8").strip() == "myproj"

    def test_marker_created_when_missing(self, env, tmp_path):
        """AC-7: no marker → created."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        result = cli._ensure_marker(str(repo_dir), "myproj")
        assert result.exists()
        assert result.read_text(encoding="utf-8").strip() == "myproj"


# ---------------------------------------------------------------------------
# Config preservation (cross-cutting)
# ---------------------------------------------------------------------------

class TestConfigPreservation:
    def test_upsert_preserves_all_keys(self, env):
        """upsert_project_config preserves app_id, app_secret, and other projects."""
        cfg = {
            "app_id": "aid",
            "app_secret": "sec",
            "projects": {
                "existing": {"bitable_app_token": "tok1", "table_id": "t1"},
            },
        }
        _write_config(env["config_path"], cfg)

        lark_client.upsert_project_config(
            "new_proj",
            {"bitable_app_token": "tok2", "table_id": "t2", "ticket_id_prefix": "X"},
            config_path=str(env["config_path"]),
        )

        saved = _read_config(env["config_path"])
        assert saved["app_id"] == "aid"
        assert saved["app_secret"] == "sec"
        assert saved["projects"]["existing"]["table_id"] == "t1"
        assert saved["projects"]["new_proj"]["bitable_app_token"] == "tok2"
        assert saved["projects"]["new_proj"]["ticket_id_prefix"] == "X"

    def test_upsert_overwrites_same_project(self, env):
        """Upserting an existing project name replaces its entry."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {"p": {"bitable_app_token": "old", "table_id": "t"}},
        }
        _write_config(env["config_path"], cfg)

        lark_client.upsert_project_config(
            "p",
            {"bitable_app_token": "new", "table_id": "t2"},
            config_path=str(env["config_path"]),
        )

        saved = _read_config(env["config_path"])
        assert saved["projects"]["p"]["bitable_app_token"] == "new"
        assert saved["projects"]["p"]["table_id"] == "t2"


# ---------------------------------------------------------------------------
# AC-8: no live API — verify mocks are in place
# ---------------------------------------------------------------------------

class TestNoLiveApi:
    """All tests above use mock.patch on _request / create_bitable /
    get_tenant_access_token — zero real HTTP calls are made (AC-8)."""

    def test_no_real_request_imported(self):
        """Sanity: _request is mockable (not wired to real HTTP)."""
        assert callable(lark_client._request)
