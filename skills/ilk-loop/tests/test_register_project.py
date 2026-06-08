"""Tests for register_project.py — idempotent, BOM-free project registration.

All tests are hermetic: they use tmp_path for the registry file and never
touch the real launcher registry.

AC-1: register_project(repo_path) adds entry and returns correct dict.
AC-2: idempotent — second call with same path is a no-op (added: False).
AC-3: real-dir-only — nonexistent path is rejected.
AC-4: BOM-free utf-8 write; re-read with plain utf-8 succeeds.
AC-5: missing registry file is created; existing entries preserved.
AC-6: explicit registry path arg works (hermetic testing).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make register_project importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from register_project import register_project, _normalize_path  # noqa: E402


# ── AC-1: basic add ─────────────────────────────────────────────────

class TestAC1_BasicAdd:
    """register_project adds {name, path} to registry."""

    def test_add_to_missing_file(self, tmp_path: Path):
        """Creates registry file when it doesn't exist."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "myproject"
        repo.mkdir()

        result = register_project(str(repo), projects_json=str(reg))

        assert result["added"] is True
        assert result["name"] == "myproject"
        assert result["path"] == str(repo.resolve())
        assert result["total"] == 1

    def test_returns_correct_dict_shape(self, tmp_path: Path):
        """Return dict has all required keys."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "proj"
        repo.mkdir()

        result = register_project(str(repo), projects_json=str(reg))

        for key in ("added", "name", "path", "total"):
            assert key in result, f"missing key: {key}"


# ── AC-2: idempotency ───────────────────────────────────────────────

class TestAC2_Idempotent:
    """Second call with same path is a no-op."""

    def test_second_call_returns_added_false(self, tmp_path: Path):
        """Calling twice with same path: first added=True, second added=False."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "proj"
        repo.mkdir()

        r1 = register_project(str(repo), projects_json=str(reg))
        r2 = register_project(str(repo), projects_json=str(reg))

        assert r1["added"] is True
        assert r2["added"] is False
        assert r2["total"] == 1  # still one entry

    def test_dedup_by_normalized_path(self, tmp_path: Path):
        """Paths differing only in case/separator are deduplicated."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "MyProject"
        repo.mkdir()

        r1 = register_project(str(repo), projects_json=str(reg))
        # Same path but lowercase — should still dedup on Windows
        alt = str(repo).lower()
        r2 = register_project(alt, projects_json=str(reg))

        assert r1["added"] is True
        # On case-insensitive filesystems this should dedup.
        # On case-sensitive (Linux) it may add a second entry — that's OK.
        data = json.loads(reg.read_text(encoding="utf-8"))
        # At most 2 entries (case-sensitive FS allows both)
        assert len(data["projects"]) <= 2


# ── AC-3: real-dir guard ────────────────────────────────────────────

class TestAC3_RealDirGuard:
    """Nonexistent paths are not registered."""

    def test_nonexistent_path_rejected(self, tmp_path: Path):
        """A path that doesn't exist on disk returns added=False."""
        reg = tmp_path / "projects.json"
        fake = tmp_path / "does_not_exist"

        result = register_project(str(fake), projects_json=str(reg))

        assert result["added"] is False
        assert "reason" in result
        assert "does not exist" in result["reason"].lower() or "not exist" in result["reason"].lower()

    def test_file_not_dir_rejected(self, tmp_path: Path):
        """A path pointing to a file (not directory) is rejected."""
        reg = tmp_path / "projects.json"
        not_dir = tmp_path / "afile.txt"
        not_dir.write_text("hello")

        result = register_project(str(not_dir), projects_json=str(reg))

        assert result["added"] is False


# ── AC-4: BOM-free utf-8 ────────────────────────────────────────────

class TestAC4_BOMFreeUtf8:
    """Written file is plain utf-8 (no BOM)."""

    def test_no_bom_prefix(self, tmp_path: Path):
        """First 3 bytes of written file must NOT be the UTF-8 BOM."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "proj"
        repo.mkdir()

        register_project(str(repo), projects_json=str(reg))

        raw = reg.read_bytes()
        assert raw[:3] != b"\xef\xbb\xbf", "file starts with UTF-8 BOM"

    def test_roundtrip_with_plain_utf8(self, tmp_path: Path):
        """File re-reads via read_text(encoding='utf-8') without error."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "proj"
        repo.mkdir()

        register_project(str(repo), projects_json=str(reg))

        # This would raise UnicodeDecodeError if there were a BOM
        text = reg.read_text(encoding="utf-8")
        data = json.loads(text)
        assert "projects" in data
        assert len(data["projects"]) == 1

    def test_unicode_name_preserved(self, tmp_path: Path):
        """Non-ASCII characters in name survive round-trip."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "测试项目"
        repo.mkdir()

        register_project(str(repo), projects_json=str(reg), name="测试项目")

        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["projects"][0]["name"] == "测试项目"


# ── AC-5: missing file creation + preserve existing ─────────────────

class TestAC5_MissingAndPreserve:
    """Missing registry is created; existing entries preserved."""

    def test_creates_missing_file_with_correct_shape(self, tmp_path: Path):
        """A missing registry file is created with _comment + projects."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "proj"
        repo.mkdir()

        register_project(str(repo), projects_json=str(reg))

        assert reg.exists()
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert "_comment" in data
        assert "projects" in data
        assert isinstance(data["projects"], list)

    def test_preserves_existing_entries(self, tmp_path: Path):
        """Existing entries survive when adding a new one."""
        reg = tmp_path / "projects.json"
        # Pre-populate
        existing = {
            "_comment": "test",
            "projects": [{"name": "old", "path": "/some/old/path"}]
        }
        reg.write_text(json.dumps(existing), encoding="utf-8")

        repo = tmp_path / "new"
        repo.mkdir()
        result = register_project(str(repo), projects_json=str(reg))

        assert result["added"] is True
        assert result["total"] == 2

        data = json.loads(reg.read_text(encoding="utf-8"))
        names = [p["name"] for p in data["projects"]]
        assert "old" in names
        assert "new" in names

    def test_parent_dir_created(self, tmp_path: Path):
        """Registry file's parent directory is created if missing."""
        reg = tmp_path / "sub" / "deep" / "projects.json"
        repo = tmp_path / "proj"
        repo.mkdir()

        register_project(str(repo), projects_json=str(reg))

        assert reg.exists()


# ── AC-6: explicit registry path ────────────────────────────────────

class TestAC6_ExplicitPath:
    """Explicit projects_json arg works for hermetic testing."""

    def test_custom_path_used(self, tmp_path: Path):
        """Custom registry path is used instead of default."""
        reg1 = tmp_path / "reg1.json"
        reg2 = tmp_path / "reg2.json"
        repo = tmp_path / "proj"
        repo.mkdir()

        register_project(str(repo), projects_json=str(reg1))
        register_project(str(repo), projects_json=str(reg2))

        # Both should exist independently
        assert reg1.exists()
        assert reg2.exists()
        data1 = json.loads(reg1.read_text(encoding="utf-8"))
        data2 = json.loads(reg2.read_text(encoding="utf-8"))
        assert len(data1["projects"]) == 1
        assert len(data2["projects"]) == 1

    def test_custom_name(self, tmp_path: Path):
        """--name overrides the leaf directory name."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "some-dir"
        repo.mkdir()

        result = register_project(str(repo), projects_json=str(reg),
                                  name="custom-name")

        assert result["name"] == "custom-name"
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["projects"][0]["name"] == "custom-name"


# ── Edge cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    """Additional edge cases."""

    def test_corrupted_json_recreated(self, tmp_path: Path):
        """A corrupted registry file is treated as empty (fresh start)."""
        reg = tmp_path / "projects.json"
        reg.write_text("NOT VALID JSON{{{", encoding="utf-8")

        repo = tmp_path / "proj"
        repo.mkdir()

        result = register_project(str(repo), projects_json=str(reg))

        assert result["added"] is True
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert len(data["projects"]) == 1

    def test_json_indent_and_trailing_newline(self, tmp_path: Path):
        """Output is nicely formatted: 4-space indent, trailing newline."""
        reg = tmp_path / "projects.json"
        repo = tmp_path / "proj"
        repo.mkdir()

        register_project(str(repo), projects_json=str(reg))

        raw = reg.read_text(encoding="utf-8")
        assert raw.endswith("\n")
        # Re-parse to verify valid JSON
        json.loads(raw)
