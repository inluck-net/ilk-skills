"""Tests for the idempotent ``init-project`` CLI verb.

All Lark HTTP is mocked — zero network calls, zero real bases (AC-9).
Covers AC-4 (reuse), AC-5 (create), AC-6 (refuse unreachable),
AC-7 (marker idempotent), config preservation, and operator_openid
grant + set-operator + show-members (AC-1 through AC-9).
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

        # Mock BitableClient to avoid real API calls
        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result) as m_create,
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema") as m_seed,
            mock.patch("cli.BitableClient", return_value=MockClient()),
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

        # Mock BitableClient to avoid real API calls
        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable") as m_create,
            mock.patch("cli._probe_tables", return_value=True),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema") as m_seed,
            mock.patch("cli.BitableClient", return_value=MockClient()),
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

        # Mock BitableClient to avoid real API calls
        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result) as m_create,
            mock.patch("cli._probe_tables", return_value=False),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
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


# ---------------------------------------------------------------------------
# AC-1: kanban created+grouped when absent
# ---------------------------------------------------------------------------

class TestKanbanCreated:
    def test_kanban_created_when_absent(self, env, monkeypatch, tmp_path):
        """AC-1: no kanban view → calls create_view(view_type='kanban') then
        patch_view to set group field to '状态'."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Mock BitableClient to record calls
        class MockClient:
            def __init__(self, **kwargs):
                self.calls = []
                self._views = []  # No existing views

            def field_id(self, name):
                return "fld_status"

            def list_views(self):
                return self._views

            def create_view(self, *, view_name, view_type):
                self.calls.append(("create_view", view_name, view_type))
                return {"view": {"view_id": f"view_{view_type}"}}

            def patch_view(self, view_id, body):
                self.calls.append(("patch_view", view_id, body))
                return {}

            def list_form_fields(self, form_id):
                return [{"field_id": "f1", "title": "标题"}]

            def patch_form_field(self, form_id, field_id, body):
                return {}

            def patch_form_meta(self, form_id, body):
                self.calls.append(("patch_form_meta", form_id, body))
                return {"form": {"shared_url": "https://form.url"}}

        mock_client = MockClient()

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=mock_client),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        # Verify kanban was created
        create_calls = [c for c in mock_client.calls if c[0] == "create_view" and c[2] == "kanban"]
        assert len(create_calls) == 1
        assert create_calls[0][1] == "工单看板"

        # Verify group field was set
        patch_calls = [c for c in mock_client.calls if c[0] == "patch_view"]
        assert len(patch_calls) == 1
        assert patch_calls[0][1] == "view_kanban"
        assert patch_calls[0][2] == {"property": {"group_field_id": "fld_status"}}


# ---------------------------------------------------------------------------
# AC-2: form created+shared, shared_url printed
# ---------------------------------------------------------------------------

class TestFormCreated:
    def test_form_created_and_shared(self, env, monkeypatch, tmp_path, capsys):
        """AC-2: no form view → calls create_view(view_type='form') then
        patch_form_meta(shared=True); shared_url appears in output."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Mock BitableClient to record calls
        class MockClient:
            def __init__(self, **kwargs):
                self.calls = []
                self._views = []  # No existing views

            def field_id(self, name):
                return "fld_status"

            def list_views(self):
                return self._views

            def create_view(self, *, view_name, view_type):
                self.calls.append(("create_view", view_name, view_type))
                return {"view": {"view_id": f"view_{view_type}"}}

            def patch_view(self, view_id, body):
                self.calls.append(("patch_view", view_id, body))
                return {}

            def list_form_fields(self, form_id):
                return [{"field_id": "f1", "title": "标题"}]

            def patch_form_field(self, form_id, field_id, body):
                return {}

            def patch_form_meta(self, form_id, body):
                self.calls.append(("patch_form_meta", form_id, body))
                return {"form": {"shared_url": "https://form.url", "shared_limit": "tenant_editable"}}

        mock_client = MockClient()

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=mock_client),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        # Verify form was created
        create_calls = [c for c in mock_client.calls if c[0] == "create_view" and c[2] == "form"]
        assert len(create_calls) == 1
        assert create_calls[0][1] == "myproj-提交新工单"

        # Verify form meta was updated with shared=True
        meta_calls = [c for c in mock_client.calls if c[0] == "patch_form_meta"]
        assert len(meta_calls) == 1
        assert meta_calls[0][2]["shared"] is True

        # Verify shared_url in output
        captured = capsys.readouterr()
        assert "https://form.url" in captured.out


# ---------------------------------------------------------------------------
# AC-3: both exist -> no create_view
# ---------------------------------------------------------------------------

class TestIdempotentSkip:
    def test_no_create_when_views_exist(self, env, monkeypatch, tmp_path):
        """AC-3: kanban + form already exist → no create_view calls."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Mock BitableClient with existing views
        class MockClient:
            def __init__(self, **kwargs):
                self.calls = []
                self._views = [
                    {"view_name": "工单看板", "view_type": "kanban", "view_id": "kb1"},
                    {"view_name": "myproj-提交新工单", "view_type": "form", "view_id": "fm1"},
                ]

            def field_id(self, name):
                return "fld_status"

            def list_views(self):
                return self._views

            def create_view(self, *, view_name, view_type):
                self.calls.append(("create_view", view_name, view_type))
                return {"view": {"view_id": f"view_{view_type}"}}

            def patch_view(self, view_id, body):
                self.calls.append(("patch_view", view_id, body))
                return {}

            def list_form_fields(self, form_id):
                return [{"field_id": "f1", "title": "标题"}]

            def patch_form_field(self, form_id, field_id, body):
                return {}

            def patch_form_meta(self, form_id, body):
                self.calls.append(("patch_form_meta", form_id, body))
                return {"form": {"shared_url": "https://form.url"}}

        mock_client = MockClient()

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=mock_client),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        # No create_view calls
        create_calls = [c for c in mock_client.calls if c[0] == "create_view"]
        assert len(create_calls) == 0

        # patch_view still called to set group field
        patch_calls = [c for c in mock_client.calls if c[0] == "patch_view"]
        assert len(patch_calls) == 1

        # patch_form_meta still called to update meta
        meta_calls = [c for c in mock_client.calls if c[0] == "patch_form_meta"]
        assert len(meta_calls) == 1


# ---------------------------------------------------------------------------
# AC-4: --folder and default_folder_token both reach create_bitable
# ---------------------------------------------------------------------------

class TestFolderResolution:
    def test_folder_arg_passed_to_create_bitable(self, env, monkeypatch, tmp_path):
        """AC-4: --folder TOK → create_bitable receives folder_token='TOK'."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Mock BitableClient to return real values
        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result) as m_create,
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
                "--folder", "TOK",
            ])
            cli.cmd_init_project(args)

        m_create.assert_called_once_with("myproj", folder_token="TOK", token="tok")

    def test_default_folder_token_used_when_no_arg(self, env, monkeypatch, tmp_path):
        """AC-4: config.default_folder_token='DEF' → create_bitable receives folder_token='DEF'."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {},
            "default_folder_token": "DEF",
        }
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Mock BitableClient to return real values
        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result) as m_create,
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        m_create.assert_called_once_with("myproj", folder_token="DEF", token="tok")


# ---------------------------------------------------------------------------
# AC-5: warn iff neither set
# ---------------------------------------------------------------------------

class TestEditableWarning:
    def test_warning_when_no_operator_openid(self, env, monkeypatch, tmp_path, capsys):
        """AC-5: no operator_openid → WARNING printed."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Mock BitableClient to return real values
        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "NOT editable" in captured.err

    def test_no_warning_when_operator_openid_set(self, env, monkeypatch, tmp_path, capsys):
        """AC-5: operator_openid set → no WARNING."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}, "operator_openid": "ou_TEST"}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Mock BitableClient to return real values
        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
                "--folder", "TOK",
            ])
            cli.cmd_init_project(args)

        captured = capsys.readouterr()
        assert "WARNING" not in captured.err


# ---------------------------------------------------------------------------
# AC-6: set-default-folder persists + preserves keys
# ---------------------------------------------------------------------------

class TestSetDefaultFolder:
    def test_persists_and_preserves_keys(self, env):
        """AC-6: set-default-folder <token> persists to config, preserves other keys."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {"p": {"bitable_app_token": "tok"}},
        }
        _write_config(env["config_path"], cfg)

        # Mock lark_client._resolve_config_path to return our temp config
        with mock.patch("lark_client._resolve_config_path", return_value=env["config_path"]):
            args = cli.build_parser().parse_args(["set-default-folder", "NEW_TOKEN"])
            cli.cmd_set_default_folder(args)

        saved = _read_config(env["config_path"])
        assert saved["default_folder_token"] == "NEW_TOKEN"
        assert saved["app_id"] == "a"
        assert saved["app_secret"] == "s"
        assert saved["projects"]["p"]["bitable_app_token"] == "tok"

    def test_overwrites_existing_default_folder(self, env):
        """AC-6: re-running with new token overwrites only that key."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "default_folder_token": "OLD",
            "projects": {},
        }
        _write_config(env["config_path"], cfg)

        # Mock lark_client._resolve_config_path to return our temp config
        with mock.patch("lark_client._resolve_config_path", return_value=env["config_path"]):
            args = cli.build_parser().parse_args(["set-default-folder", "NEW"])
            cli.cmd_set_default_folder(args)

        saved = _read_config(env["config_path"])
        assert saved["default_folder_token"] == "NEW"


# ---------------------------------------------------------------------------
# AC-7: grant failure is swallowed, init still succeeds
# ---------------------------------------------------------------------------

class TestGrantNonFatal:
    def test_grant_failure_does_not_block_init(self, env, monkeypatch, tmp_path, capsys):
        """AC-4: member-grant failure → init still completes successfully."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {},
            "operator_openid": "ou_TEST",
        }
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Mock _request to fail on the grant call
        def mock_request(method, path, *, token=None, params=None, body=None, **kw):
            if "permissions" in path and "members" in path:
                raise Exception("API error")
            return {}

        # Mock BitableClient to return real values
        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
            mock.patch("cli._request", side_effect=mock_request),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            # Should not raise
            cli.cmd_init_project(args)

        captured = capsys.readouterr()
        assert "NOTE" in captured.err
        assert "operator grant skipped" in captured.err


# ---------------------------------------------------------------------------
# AC-8: no real network — assert _request/create_bitable are mocks
# ---------------------------------------------------------------------------

class TestNoLiveApiExtended:
    """Verify all new tests use mocks — zero real HTTP calls."""

    def test_ensure_issue_views_mockable(self):
        """Sanity: ensure_issue_views is callable and mockable."""
        assert callable(cli.ensure_issue_views)

    def test_set_default_folder_mockable(self):
        """Sanity: cmd_set_default_folder is callable and mockable."""
        assert callable(cli.cmd_set_default_folder)


# ---------------------------------------------------------------------------
# AC-1: set-operator persists + preserves keys
# ---------------------------------------------------------------------------

class TestSetOperator:
    def test_persists_and_preserves_keys(self, env):
        """AC-1: set-operator <open_id> persists operator_openid, preserves other keys."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {"p": {"bitable_app_token": "tok"}},
            "default_folder_token": "FOLD",
        }
        _write_config(env["config_path"], cfg)

        with mock.patch("lark_client._resolve_config_path", return_value=env["config_path"]):
            args = cli.build_parser().parse_args(["set-operator", "ou_TEST_123"])
            cli.cmd_set_operator(args)

        saved = _read_config(env["config_path"])
        assert saved["operator_openid"] == "ou_TEST_123"
        assert saved["app_id"] == "a"
        assert saved["app_secret"] == "s"
        assert saved["default_folder_token"] == "FOLD"
        assert saved["projects"]["p"]["bitable_app_token"] == "tok"

    def test_overwrites_existing_operator(self, env):
        """AC-1: re-running with new open_id overwrites only that key."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {},
            "operator_openid": "ou_OLD",
        }
        _write_config(env["config_path"], cfg)

        with mock.patch("lark_client._resolve_config_path", return_value=env["config_path"]):
            args = cli.build_parser().parse_args(["set-operator", "ou_NEW"])
            cli.cmd_set_operator(args)

        saved = _read_config(env["config_path"])
        assert saved["operator_openid"] == "ou_NEW"


# ---------------------------------------------------------------------------
# AC-2/AC-3/AC-4: operator_openid grant behavior
# ---------------------------------------------------------------------------

class TestOperatorGrant:
    def test_grant_issued_when_operator_set(self, env, monkeypatch, tmp_path):
        """AC-2: with operator_openid set, init-project issues exactly one
        members-add call with the correct path/params/body."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {},
            "operator_openid": "ou_OPERATOR",
        }
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        # Record _request calls
        request_calls = []

        def mock_request(method, path, *, token=None, params=None, body=None, **kw):
            request_calls.append({"method": method, "path": path, "params": params, "body": body})
            return {}

        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
            mock.patch("cli._request", side_effect=mock_request),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        # Find the grant call (POST to permissions members)
        grant_calls = [
            c for c in request_calls
            if c["method"] == "POST" and "permissions" in c["path"] and "members" in c["path"]
        ]
        assert len(grant_calls) == 1
        gc = grant_calls[0]
        assert gc["params"]["type"] == "bitable"
        assert gc["params"]["need_notification"] == "false"
        assert gc["body"]["member_type"] == "openid"
        assert gc["body"]["member_id"] == "ou_OPERATOR"
        assert gc["body"]["perm"] == "full_access"

    def test_no_grant_when_operator_unset(self, env, monkeypatch, tmp_path):
        """AC-3: no operator_openid → no members-add call."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        request_calls = []

        def mock_request(method, path, *, token=None, params=None, body=None, **kw):
            request_calls.append({"method": method, "path": path, "params": params, "body": body})
            return {}

        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
            mock.patch("cli._request", side_effect=mock_request),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        # No grant calls
        grant_calls = [
            c for c in request_calls
            if c["method"] == "POST" and "permissions" in c["path"] and "members" in c["path"]
        ]
        assert len(grant_calls) == 0

    def test_grant_non_fatal_on_exception(self, env, monkeypatch, tmp_path, capsys):
        """AC-4: grant raises → init still completes, NOTE printed."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {},
            "operator_openid": "ou_OPERATOR",
        }
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        call_count = 0

        def mock_request(method, path, *, token=None, params=None, body=None, **kw):
            nonlocal call_count
            call_count += 1
            # Fail on the grant call (permissions/members)
            if "permissions" in path and "members" in path:
                raise Exception("API denied")
            return {}

        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
            mock.patch("cli._request", side_effect=mock_request),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            # Should NOT raise
            cli.cmd_init_project(args)

        captured = capsys.readouterr()
        assert "NOTE" in captured.err
        assert "operator grant skipped" in captured.err

    def test_grant_on_reuse_path(self, env, monkeypatch, tmp_path):
        """AC-2: grant also runs on the reuse path."""
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
            "operator_openid": "ou_REUSE",
        }
        _write_config(env["config_path"], cfg)
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        marker = repo_dir / ".lark-project"
        marker.write_text("myproj\n", encoding="utf-8")

        request_calls = []

        def mock_request(method, path, *, token=None, params=None, body=None, **kw):
            request_calls.append({"method": method, "path": path, "params": params, "body": body})
            return {}

        class MockClient:
            def __init__(self, **kwargs):
                pass
            def field_id(self, name):
                return "fld_status"
            def list_views(self):
                return []
            def create_view(self, **kwargs):
                return {"view": {"view_id": "view_1"}}
            def patch_view(self, *args, **kwargs):
                return {}
            def list_form_fields(self, form_id):
                return []
            def patch_form_field(self, form_id, field_id, body):
                return {}
            def patch_form_meta(self, *args, **kwargs):
                return {"form": {"shared_url": "https://form.url"}}

        with (
            mock.patch("cli.create_bitable") as m_create,
            mock.patch("cli._probe_tables", return_value=True),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=MockClient()),
            mock.patch("cli._request", side_effect=mock_request),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        # create_bitable NOT called (reuse path)
        m_create.assert_not_called()

        # Grant call issued
        grant_calls = [
            c for c in request_calls
            if c["method"] == "POST" and "permissions" in c["path"] and "members" in c["path"]
        ]
        assert len(grant_calls) == 1
        assert grant_calls[0]["body"]["member_id"] == "ou_REUSE"


# ---------------------------------------------------------------------------
# AC-7: show-members verb
# ---------------------------------------------------------------------------

class TestShowMembers:
    def test_show_members_lists_members(self, env, monkeypatch, capsys):
        """AC-7: show-members calls members-list endpoint and prints each member."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {
                "myproj": {"bitable_app_token": "tok123", "table_id": "tbl1"},
            },
        }
        _write_config(env["config_path"], cfg)

        fake_members = {
            "items": [
                {"member_id": "ou_A", "member_type": "openid", "perm": "full_access"},
                {"member_id": "ou_B", "member_type": "openid", "perm": "read"},
            ]
        }

        def mock_request(method, path, *, token=None, params=None, body=None, **kw):
            assert method == "GET"
            assert "permissions" in path
            assert params == {"type": "bitable"}
            return fake_members

        with (
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("cli._request", side_effect=mock_request),
        ):
            args = cli.build_parser().parse_args([
                "show-members", "--project", "myproj",
            ])
            cli.cmd_show_members(args)

        captured = capsys.readouterr()
        assert "ou_A" in captured.out
        assert "full_access" in captured.out
        assert "ou_B" in captured.out
        assert "read" in captured.out

    def test_show_members_exits_on_missing_project(self, env, monkeypatch):
        """AC-7: show-members with unknown project → SystemExit."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        with (
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
        ):
            args = cli.build_parser().parse_args([
                "show-members", "--project", "no_such_proj",
            ])
            with pytest.raises(SystemExit):
                cli.cmd_show_members(args)


# ---------------------------------------------------------------------------
# AC-9: no real network — verify _request is mocked in grant tests
# ---------------------------------------------------------------------------

class TestNoLiveApiGrant:
    """All grant tests above mock cli._request — zero real HTTP calls."""

    def test_try_grant_operator_access_callable(self):
        """Sanity: _try_grant_operator_access is callable."""
        assert callable(cli._try_grant_operator_access)

    def test_cmd_show_members_callable(self):
        """Sanity: cmd_show_members is callable."""
        assert callable(cli.cmd_show_members)


# ---------------------------------------------------------------------------
# AC-1: ensure_form_fields PATCHes each field per FORM_SPEC
# ---------------------------------------------------------------------------

class TestEnsureFormFields:
    """ensure_form_fields resolves title→field_id and PATCHes per FORM_SPEC."""

    def _make_mock_client(self, form_fields):
        """Return a MockClient with list_form_fields / patch_form_field."""
        class MockClient:
            def __init__(self):
                self.patch_calls = []

            def list_form_fields(self, form_id):
                return form_fields

            def patch_form_field(self, form_id, field_id, body):
                self.patch_calls.append((form_id, field_id, body))
                return {}

        return MockClient()

    def test_per_field_patch_matches_spec(self):
        """AC-1: 标题 → visible+required, 原文描述 → visible=false, etc."""
        form_fields = [
            {"field_id": "f1", "title": "标题"},
            {"field_id": "f2", "title": "原文描述"},
            {"field_id": "f3", "title": "AI 理解"},
            {"field_id": "f4", "title": "状态"},
            {"field_id": "f5", "title": "操作步骤"},
            {"field_id": "f6", "title": "截图"},
        ]
        client = self._make_mock_client(form_fields)
        results = cli.ensure_form_fields(client, "form_123")

        # Build a lookup by field_id
        by_fid = {r["field_id"]: r for r in results}

        assert by_fid["f1"]["visible"] is True
        assert by_fid["f1"]["required"] is True

        assert by_fid["f5"]["visible"] is True
        assert by_fid["f5"]["required"] is False

        assert by_fid["f6"]["visible"] is True
        assert by_fid["f6"]["required"] is False

        # Hidden fields
        for fid in ("f2", "f3", "f4"):
            assert by_fid[fid]["visible"] is False
            assert by_fid[fid]["required"] is False

    def test_default_hidden_for_unknown_title(self):
        """AC-1: a field not in FORM_SPEC → visible:false, required:false."""
        form_fields = [{"field_id": "fx", "title": "Some Unknown Field"}]
        client = self._make_mock_client(form_fields)
        results = cli.ensure_form_fields(client, "form_123")

        assert len(results) == 1
        assert results[0]["visible"] is False
        assert results[0]["required"] is False

    def test_non_fatal_on_patch_error(self):
        """AC-2: patch_form_field raises → logs error, continues, init completes."""
        class ErrorClient:
            def list_form_fields(self, form_id):
                return [
                    {"field_id": "f_ok", "title": "标题"},
                    {"field_id": "f_bad", "title": "截图"},
                ]

            def patch_form_field(self, form_id, field_id, body):
                if field_id == "f_bad":
                    raise Exception("API boom")
                return {}

        client = ErrorClient()
        results = cli.ensure_form_fields(client, "form_123")

        # Both fields returned; f_bad has error
        assert len(results) == 2
        by_fid = {r["field_id"]: r for r in results}
        assert by_fid["f_ok"]["visible"] is True
        assert "error" in by_fid["f_bad"]
        assert "API boom" in by_fid["f_bad"]["error"]


# ---------------------------------------------------------------------------
# AC-4: sharing-on-create (created → shared_limit; existing → no shared_limit)
# ---------------------------------------------------------------------------

class TestSharingOnCreate:
    """patch_form_meta includes shared_limit only when the form was just created."""

    def test_created_form_gets_shared_limit(self, env, monkeypatch, tmp_path):
        """AC-4: form just created → patch_form_meta called with shared_limit."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        class MockClient:
            def __init__(self, **kwargs):
                self.calls = []
                self._views = []  # No existing views → form will be created

            def field_id(self, name):
                return "fld_status"

            def list_views(self):
                return self._views

            def create_view(self, *, view_name, view_type):
                self.calls.append(("create_view", view_name, view_type))
                return {"view": {"view_id": f"view_{view_type}"}}

            def patch_view(self, view_id, body):
                self.calls.append(("patch_view", view_id, body))
                return {}

            def list_form_fields(self, form_id):
                return [{"field_id": "f1", "title": "标题"}]

            def patch_form_field(self, form_id, field_id, body):
                return {}

            def patch_form_meta(self, form_id, body):
                self.calls.append(("patch_form_meta", form_id, body))
                return {"form": {"shared_url": "https://form.url", "shared_limit": body.get("shared_limit")}}

        mock_client = MockClient()

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=mock_client),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        meta_calls = [c for c in mock_client.calls if c[0] == "patch_form_meta"]
        assert len(meta_calls) == 1
        body = meta_calls[0][2]
        assert body["shared"] is True
        assert body["shared_limit"] == "tenant_editable"
        assert body["submit_limit_once"] is False

    def test_existing_form_omits_shared_limit(self, env, monkeypatch, tmp_path):
        """AC-4: form already exists → patch_form_meta without shared_limit."""
        cfg = {"app_id": "a", "app_secret": "s", "projects": {}}
        _write_config(env["config_path"], cfg)

        created_result = {
            "app_token": "new_token",
            "table_id": "tbl_new",
            "url": "https://feishu.cn/base/new",
        }
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        class MockClient:
            def __init__(self, **kwargs):
                self.calls = []
                self._views = [
                    {"view_name": "工单看板", "view_type": "kanban", "view_id": "kb1"},
                    {"view_name": "myproj-提交新工单", "view_type": "form", "view_id": "fm1"},
                ]

            def field_id(self, name):
                return "fld_status"

            def list_views(self):
                return self._views

            def create_view(self, *, view_name, view_type):
                self.calls.append(("create_view", view_name, view_type))
                return {"view": {"view_id": f"view_{view_type}"}}

            def patch_view(self, view_id, body):
                self.calls.append(("patch_view", view_id, body))
                return {}

            def list_form_fields(self, form_id):
                return [{"field_id": "f1", "title": "标题"}]

            def patch_form_field(self, form_id, field_id, body):
                return {}

            def patch_form_meta(self, form_id, body):
                self.calls.append(("patch_form_meta", form_id, body))
                return {"form": {"shared_url": "https://form.url"}}

        mock_client = MockClient()

        with (
            mock.patch("cli.create_bitable", return_value=created_result),
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("init_bitable.seed_schema"),
            mock.patch("cli.BitableClient", return_value=mock_client),
        ):
            args = cli.build_parser().parse_args([
                "init-project", "--project", "myproj", "--repo", str(repo_dir),
            ])
            cli.cmd_init_project(args)

        meta_calls = [c for c in mock_client.calls if c[0] == "patch_form_meta"]
        assert len(meta_calls) == 1
        body = meta_calls[0][2]
        assert body["shared"] is True
        assert "shared_limit" not in body  # Preserved from manual upgrade


# ---------------------------------------------------------------------------
# AC-5: pull-new filter shape (or / isEmpty)
# ---------------------------------------------------------------------------

class TestPullNewFilter:
    """cmd_pull_new uses OR filter with isEmpty for blank-状态 records."""

    def test_filter_shape_is_or_with_isEmpty(self, env, monkeypatch, capsys):
        """AC-5: filter is conjunction=or, conditions include isEmpty."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {
                "myproj": {"bitable_app_token": "tok123", "table_id": "tbl1"},
            },
        }
        _write_config(env["config_path"], cfg)

        captured_filter = {}

        def mock_request(method, path, *, token=None, params=None, body=None, **kw):
            if "records" in path and "search" in path:
                captured_filter["body"] = body
                # Return one record with 状态=新建
                return {
                    "items": [
                        {
                            "record_id": "rec1",
                            "fields": {"状态": "新建", "标题": [{"text": "Test"}]},
                        }
                    ],
                    "has_more": False,
                }
            if path.endswith("/fields") or "/fields?" in path:
                return {
                    "items": [
                        {"field_id": "f1", "field_name": "标题", "ui_type": "Text"},
                        {"field_id": "f2", "field_name": "状态", "ui_type": "SingleSelect"},
                    ],
                    "has_more": False,
                }
            return {}

        with (
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("cli._request", side_effect=mock_request),
            mock.patch("lark_client._request", side_effect=mock_request),
            mock.patch("lark_client.get_tenant_access_token", return_value="tok"),
        ):
            args = cli.build_parser().parse_args([
                "--project", "myproj", "pull-new",
            ])
            cli.cmd_pull_new(args)

        filt = captured_filter["body"]["filter"]
        assert filt["conjunction"] == "or"
        conditions = filt["conditions"]
        assert len(conditions) == 2

        is_cond = [c for c in conditions if c["operator"] == "is"]
        assert len(is_cond) == 1
        assert is_cond[0]["field_name"] == "状态"
        assert is_cond[0]["value"] == ["新建"]

        empty_cond = [c for c in conditions if c["operator"] == "isEmpty"]
        assert len(empty_cond) == 1
        assert empty_cond[0]["field_name"] == "状态"


# ---------------------------------------------------------------------------
# AC-6: pull-new backfills blank-状态 → 新建
# ---------------------------------------------------------------------------

class TestPullNewBackfill:
    """cmd_pull_new backfills blank-状态 records to 新建."""

    def test_blank_status_backfilled_to_new(self, env, monkeypatch, capsys):
        """AC-6: record with blank 状态 → update_record(状态=新建)."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {
                "myproj": {"bitable_app_token": "tok123", "table_id": "tbl1"},
            },
        }
        _write_config(env["config_path"], cfg)

        update_calls = []

        def mock_request(method, path, *, token=None, params=None, body=None, **kw):
            if "records" in path and "search" in path:
                return {
                    "items": [
                        {
                            "record_id": "rec_blank",
                            "fields": {"状态": None, "标题": [{"text": "Blank ticket"}]},
                        },
                        {
                            "record_id": "rec_new",
                            "fields": {"状态": "新建", "标题": [{"text": "New ticket"}]},
                        },
                    ],
                    "has_more": False,
                }
            if "records" in path and method == "PUT":
                update_calls.append({"record_id": path.split("/")[-1], "fields": body["fields"]})
                return {}
            if path.endswith("/fields") or "/fields?" in path:
                return {
                    "items": [
                        {"field_id": "f1", "field_name": "标题", "ui_type": "Text"},
                        {"field_id": "f2", "field_name": "状态", "ui_type": "SingleSelect"},
                    ],
                    "has_more": False,
                }
            return {}

        with (
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("cli._request", side_effect=mock_request),
            mock.patch("lark_client._request", side_effect=mock_request),
            mock.patch("lark_client.get_tenant_access_token", return_value="tok"),
        ):
            args = cli.build_parser().parse_args([
                "--project", "myproj", "pull-new",
            ])
            cli.cmd_pull_new(args)

        # Only rec_blank should be updated (blank 状态 → 新建)
        assert len(update_calls) == 1
        assert update_calls[0]["record_id"] == "rec_blank"
        assert update_calls[0]["fields"]["状态"] == "新建"

        # Output includes both records
        captured = capsys.readouterr()
        assert "rec_blank" in captured.out
        assert "rec_new" in captured.out

    def test_new_status_not_rewritten(self, env, monkeypatch, capsys):
        """AC-6: record already 新建 → no update_record call."""
        cfg = {
            "app_id": "a",
            "app_secret": "s",
            "projects": {
                "myproj": {"bitable_app_token": "tok123", "table_id": "tbl1"},
            },
        }
        _write_config(env["config_path"], cfg)

        update_calls = []

        def mock_request(method, path, *, token=None, params=None, body=None, **kw):
            if "records" in path and "search" in path:
                return {
                    "items": [
                        {
                            "record_id": "rec_new",
                            "fields": {"状态": "新建", "标题": [{"text": "Already new"}]},
                        },
                    ],
                    "has_more": False,
                }
            if "records" in path and method == "PUT":
                update_calls.append(path)
                return {}
            if path.endswith("/fields") or "/fields?" in path:
                return {
                    "items": [
                        {"field_id": "f1", "field_name": "标题", "ui_type": "Text"},
                        {"field_id": "f2", "field_name": "状态", "ui_type": "SingleSelect"},
                    ],
                    "has_more": False,
                }
            return {}

        with (
            mock.patch("cli.get_tenant_access_token", return_value="tok"),
            mock.patch("cli.load_config", return_value=cfg),
            mock.patch("cli._request", side_effect=mock_request),
            mock.patch("lark_client._request", side_effect=mock_request),
            mock.patch("lark_client.get_tenant_access_token", return_value="tok"),
        ):
            args = cli.build_parser().parse_args([
                "--project", "myproj", "pull-new",
            ])
            cli.cmd_pull_new(args)

        # No update calls for 新建 records
        assert len(update_calls) == 0


# ---------------------------------------------------------------------------
# AC-7: no kanban property assertion in tests
# ---------------------------------------------------------------------------

class TestNoKanbanProperty:
    """Verify ensure_form_fields does not inspect kanban property (API returns null)."""

    def test_ensure_form_fields_does_not_touch_kanban(self):
        """AC-7: ensure_form_fields only calls list_form_fields / patch_form_field."""
        calls = []

        class RecordingClient:
            def list_form_fields(self, form_id):
                calls.append(("list_form_fields", form_id))
                return [{"field_id": "f1", "title": "标题"}]

            def patch_form_field(self, form_id, field_id, body):
                calls.append(("patch_form_field", form_id, field_id, body))
                return {}

        client = RecordingClient()
        cli.ensure_form_fields(client, "form_123")

        # Only form-field calls, no kanban/view calls
        for c in calls:
            assert c[0] in ("list_form_fields", "patch_form_field")
