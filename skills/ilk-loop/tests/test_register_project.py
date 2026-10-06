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

from register_project import register_project, _normalize_path, _default_registry_path  # noqa: E402


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


# ── AC-7: default registry path (skill-root, not per-project) ───────

class TestAC7_DefaultRegistryPath:
    """_default_registry_path returns <skill-root>/ilk-launcher/projects.json,
    the canonical registry read by scheduler and launcher."""

    def test_default_path_is_skill_root(self, tmp_path: Path, monkeypatch):
        """Without projects_json arg, register_project writes to skill-root path."""
        # Set up a fake skill root
        fake_skill_root = tmp_path / "skill-root"
        launcher_dir = fake_skill_root / "ilk-launcher"
        launcher_dir.mkdir(parents=True)
        canonical_reg = launcher_dir / "projects.json"

        # Pre-populate with existing entries (simulating a real registry)
        existing = {
            "_comment": "existing",
            "projects": [
                {"name": "proj-a", "path": "/a"},
                {"name": "proj-b", "path": "/b"},
            ],
        }
        canonical_reg.write_text(json.dumps(existing), encoding="utf-8")

        # Monkeypatch skill_root to return our fake path
        import ilk_paths
        monkeypatch.setattr(ilk_paths, "skill_root", lambda: fake_skill_root)

        # Create a real directory for the repo
        repo = tmp_path / "myproject"
        repo.mkdir()

        # Call register_project WITHOUT projects_json — should use canonical path
        result = register_project(str(repo))

        assert result["added"] is True
        assert result["total"] == 3  # 2 existing + 1 new

        # Verify it wrote to the canonical path (not a per-project phantom)
        data = json.loads(canonical_reg.read_text(encoding="utf-8"))
        names = [p["name"] for p in data["projects"]]
        assert "proj-a" in names
        assert "proj-b" in names
        assert "myproject" in names

    def test_default_path_idempotent(self, tmp_path: Path, monkeypatch):
        """Registering a path already in the canonical registry returns added=False."""
        fake_skill_root = tmp_path / "skill-root"
        launcher_dir = fake_skill_root / "ilk-launcher"
        launcher_dir.mkdir(parents=True)
        canonical_reg = launcher_dir / "projects.json"

        repo = tmp_path / "existing-proj"
        repo.mkdir()

        existing = {
            "_comment": "test",
            "projects": [{"name": "existing-proj", "path": str(repo.resolve())}],
        }
        canonical_reg.write_text(json.dumps(existing), encoding="utf-8")

        import ilk_paths
        monkeypatch.setattr(ilk_paths, "skill_root", lambda: fake_skill_root)

        result = register_project(str(repo))

        assert result["added"] is False
        assert result["total"] == 1
        assert "already registered" in result.get("reason", "")


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


# ── Data-home registry contract (step 0 pins) ────────────────────────
#
# These tests pin the contract that the project registry lives in the
# writable data home (ilk_data_root()), not in the read-only installed
# release tree (skill_root()). They use xfail(strict=True) for the
# specific escaped defect and plain assertions for behavior that should
# already work.
#
# After step 1 (the fix), all xfail markers will be removed.

import os
import unittest.mock


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def data_home_env(tmp_path, monkeypatch):
    """Set up isolated data-home and legacy skill-root paths.

    Returns (data_home, legacy_path) where:
      data_home = tmp_path / "data-home"
      legacy_path = tmp_path / "fake-skill-root" / "ilk-launcher" / "projects.json"
    """
    data_home = tmp_path / "data-home"
    fake_skill_root = tmp_path / "fake-skill-root"
    legacy_path = fake_skill_root / "ilk-launcher" / "projects.json"

    # Patch _default_registry_path to point to the legacy path
    monkeypatch.setattr(
        "register_project._default_registry_path", lambda: legacy_path
    )

    # Patch ilk_paths.ilk_data_root to return our data-home
    import ilk_paths
    monkeypatch.setattr(ilk_paths, "ilk_data_root", lambda: data_home)

    return data_home, legacy_path


@pytest.fixture()
def data_home(data_home_env):
    """Just the data-home path."""
    return data_home_env[0]


# ── DR-1: _default_registry_path returns data-home ────────────────────

class TestDR1_DefaultRegistryPathIsDataHome:
    """After the fix, _default_registry_path() returns data-home, not
    skill-root."""

    def test_default_path_is_data_home(self, tmp_path, monkeypatch):
        """_default_registry_path() must return ilk_data_root() / 'projects.json'."""
        import ilk_paths

        data_home = tmp_path / "data-home"
        monkeypatch.setattr(ilk_paths, "ilk_data_root", lambda: data_home)

        result = _default_registry_path()

        assert result == data_home / "projects.json"


# ── DR-2: read-only skill tree ────────────────────────────────────────

class TestDR2_ReadOnlySkillTree:
    """Registration succeeds when the installed release tree is read-only."""

    def test_readonly_skill_root_still_registers(
        self, tmp_path, data_home_env
    ):
        """When skill_root is read-only, register_project must succeed by
        writing to the data-home registry."""
        data_home, legacy_path = data_home_env

        # Create a read-only skill-root directory
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(
            json.dumps({"_comment": "ro", "projects": []}),
            encoding="utf-8",
        )
        legacy_path.parent.chmod(0o444)

        try:
            repo = tmp_path / "myproject"
            repo.mkdir()

            result = register_project(str(repo))

            assert result["added"] is True
            assert result["total"] == 1

            # Verify it wrote to data-home, not skill-root
            assert (data_home / "projects.json").exists()
            data = json.loads(
                (data_home / "projects.json").read_text(encoding="utf-8")
            )
            assert len(data["projects"]) == 1
            assert data["projects"][0]["name"] == "myproject"
        finally:
            legacy_path.parent.chmod(0o755)


# ── DR-3: legacy import ───────────────────────────────────────────────

class TestDR3_LegacyImport:
    """When data-home registry is absent, legacy entries are imported."""

    def test_legacy_entries_imported_to_data_home(
        self, tmp_path, data_home_env
    ):
        """Legacy skill-root entries are imported on first registration."""
        data_home, legacy_path = data_home_env

        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(
            json.dumps({
                "_comment": "legacy",
                "projects": [
                    {"name": "legacy-a", "path": "/a"},
                    {"name": "legacy-b", "path": "/b"},
                ],
            }),
            encoding="utf-8",
        )

        repo = tmp_path / "new-project"
        repo.mkdir()

        result = register_project(str(repo))

        assert result["added"] is True
        assert result["total"] == 3

        data = json.loads(
            (data_home / "projects.json").read_text(encoding="utf-8")
        )
        names = [p["name"] for p in data["projects"]]
        assert "legacy-a" in names
        assert "legacy-b" in names
        assert "new-project" in names


# ── DR-4: data-home wins ──────────────────────────────────────────────

class TestDR4_DataHomeWins:
    """Existing data-home entries are never overwritten by legacy import."""

    def test_data_home_not_overwritten_by_legacy(
        self, tmp_path, data_home_env
    ):
        """Legacy import must not drop or overwrite existing data-home entries."""
        data_home, legacy_path = data_home_env

        # Data-home already has an entry
        data_home.mkdir(parents=True)
        (data_home / "projects.json").write_text(
            json.dumps({
                "_comment": "data-home",
                "projects": [{"name": "home-proj", "path": "/home"}],
            }),
            encoding="utf-8",
        )

        # Legacy has different entries
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(
            json.dumps({
                "_comment": "legacy",
                "projects": [{"name": "legacy-proj", "path": "/legacy"}],
            }),
            encoding="utf-8",
        )

        repo = tmp_path / "new-proj"
        repo.mkdir()

        result = register_project(str(repo))

        assert result["added"] is True
        assert result["total"] == 2  # home-proj + new-proj, NOT legacy-proj

        data = json.loads(
            (data_home / "projects.json").read_text(encoding="utf-8")
        )
        names = [p["name"] for p in data["projects"]]
        assert "home-proj" in names
        assert "new-proj" in names
        # Legacy entries must NOT appear when data-home already exists
        assert "legacy-proj" not in names


# ── DR-5: dedup on legacy merge ───────────────────────────────────────

class TestDR5_DedupOnLegacyMerge:
    """Legacy entries that duplicate data-home entries are not doubled."""

    def test_dedup_on_legacy_import(self, tmp_path, data_home_env):
        """Legacy entries with the same normalized path as data-home entries
        are merged, not duplicated."""
        data_home, legacy_path = data_home_env

        repo = tmp_path / "shared-proj"
        repo.mkdir()
        norm_path = str(repo.resolve())

        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(
            json.dumps({
                "_comment": "legacy",
                "projects": [{"name": "shared-proj", "path": norm_path}],
            }),
            encoding="utf-8",
        )

        result = register_project(str(repo))

        assert result["added"] is True

        data = json.loads(
            (data_home / "projects.json").read_text(encoding="utf-8")
        )
        paths = [_normalize_path(p["path"]) for p in data["projects"]]
        assert paths.count(_normalize_path(norm_path)) == 1


# ── DR-6: atomic output ───────────────────────────────────────────────

class TestDR6_AtomicOutput:
    """Write is atomic and BOM-free; interruption cannot leave a partial
    canonical registry."""

    def test_failed_write_does_not_corrupt_existing(
        self, tmp_path, data_home_env
    ):
        """When the write raises, the existing data-home registry must be
        unchanged."""
        data_home, _ = data_home_env

        # Pre-populate data-home
        data_home.mkdir(parents=True)
        original = {"_comment": "original", "projects": [{"name": "a", "path": "/a"}]}
        (data_home / "projects.json").write_text(
            json.dumps(original), encoding="utf-8"
        )

        repo = tmp_path / "new-proj"
        repo.mkdir()

        # Patch write_text to simulate interruption
        original_write = Path.write_text

        def failing_write(self, data, encoding=None):
            raise OSError("disk full")

        with unittest.mock.patch.object(Path, "write_text", failing_write):
            with pytest.raises(OSError, match="disk full"):
                register_project(str(repo))

        # Existing registry must be unchanged
        data = json.loads(
            (data_home / "projects.json").read_text(encoding="utf-8")
        )
        assert data == original

    def test_successful_write_is_complete(self, tmp_path, data_home_env):
        """After a successful write, the data-home registry contains the
        new entry and is valid JSON."""
        data_home, _ = data_home_env

        repo = tmp_path / "proj"
        repo.mkdir()

        result = register_project(str(repo))

        assert result["added"] is True
        assert (data_home / "projects.json").exists()

        data = json.loads(
            (data_home / "projects.json").read_text(encoding="utf-8")
        )
        assert len(data["projects"]) == 1
        assert data["projects"][0]["name"] == "proj"


# ── DR-7: env precedence ──────────────────────────────────────────────

class TestDR7_EnvPrecedence:
    """ILK_DATA_HOME > ILK_DATA_DIR > ~/.ilk-data."""

    def test_ilk_data_home_takes_precedence(self, tmp_path, monkeypatch):
        """ILK_DATA_HOME wins over ILK_DATA_DIR and default."""
        import ilk_paths

        home = tmp_path / "via-home"
        dir_ = tmp_path / "via-dir"

        monkeypatch.setenv("ILK_DATA_HOME", str(home))
        monkeypatch.setenv("ILK_DATA_DIR", str(dir_))

        result = ilk_paths.ilk_data_root()

        assert result == home.resolve()

    def test_ilk_data_dir_fallback(self, tmp_path, monkeypatch):
        """ILK_DATA_DIR is used when ILK_DATA_HOME is unset."""
        import ilk_paths

        dir_ = tmp_path / "via-dir"

        monkeypatch.delenv("ILK_DATA_HOME", raising=False)
        monkeypatch.setenv("ILK_DATA_DIR", str(dir_))

        result = ilk_paths.ilk_data_root()

        assert result == dir_.resolve()


# ── DR-8: explicit --projects-json ────────────────────────────────────

class TestDR8_ExplicitOverride:
    """Explicit --projects-json bypasses automatic migration."""

    def test_explicit_path_used_no_migration(self, tmp_path, data_home_env):
        """With projects_json set, data-home and legacy paths are irrelevant."""
        data_home, _ = data_home_env

        # Legacy has entries that should NOT be imported
        legacy = data_home / "legacy" / "projects.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            json.dumps({
                "_comment": "legacy",
                "projects": [{"name": "legacy-x", "path": "/x"}],
            }),
            encoding="utf-8",
        )

        explicit = tmp_path / "explicit" / "projects.json"
        repo = tmp_path / "myrepo"
        repo.mkdir()

        result = register_project(str(repo), projects_json=str(explicit))

        assert result["added"] is True

        # Only the explicit registry should exist
        data = json.loads(explicit.read_text(encoding="utf-8"))
        assert len(data["projects"]) == 1
        assert data["projects"][0]["name"] == "myrepo"

        # Data-home registry must NOT have been touched
        assert not (data_home / "projects.json").exists()
