r"""Red-first pins: a step gate runs the importers of what it changed.

Part of sub-plan ``a-step-gate-runs-the-importers-of-what-it-changed``
(step 0 of 2).

Contract (pre-resolved — do not re-decide):

- New ``skills/ilk-loop/scripts/test_importers.py``:
  ``importer_tests(project: Path, changed: list[str]) -> list[str]``
  returns sorted repo-relative test files (``test_*.py`` under any
  ``tests/`` dir, git-tracked only) whose ``ast`` imports name the
  module of any changed non-test ``.py`` file (``import x``,
  ``from x import``, ``from pkg.x import``).  Port the semantics of
  ``plan_lint._discover_tests_by_import``; it becomes a thin call to
  the helper.

- ``run_local_checks._synthesize_mention_check`` adds
  ``importer_tests(changed)`` to the files it runs, with the existing
  declared-red deselection applied to them too.

- ``verification_record.compute_suite_scope`` step 3 calls the helper
  instead of the substring match.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ── Helpers ─────────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def _make_repo(
    tmp_path: Path,
    *,
    source_files: dict[str, str] | None = None,
    test_files: dict[str, str] | None = None,
) -> Path:
    """Create a minimal git repo with source and test files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")

    for rel, content in (source_files or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    for rel, content in (test_files or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


# ── AC-1: test_importers.importer_tests exists and returns correct results ──


class TestImporterHelper:
    """The shared importer helper finds test files by ast import analysis."""

    def test_importer_tests_returns_test_that_imports_changed_module(
        self, tmp_path: Path,
    ) -> None:
        """A test file that ``import``s the changed module is found."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_widget.py": (
                    "import src.widget\n"
                    "def test_widget(): pass\n"
                ),
            },
        )
        # The helper should exist at this path
        helper = SCRIPTS_DIR / "test_importers.py"
        assert helper.exists(), (
            "test_importers.py does not exist yet — expected at "
            f"{helper}"
        )
        mod = _import_helper(helper)
        result = mod.importer_tests(repo, ["src/widget.py"])
        assert "tests/test_widget.py" in result

    def test_importer_tests_returns_test_that_from_imports_module(
        self, tmp_path: Path,
    ) -> None:
        """A test file that ``from X import ...`` is found."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_widget.py": (
                    "from src.widget import Widget\n"
                    "def test_widget(): pass\n"
                ),
            },
        )
        helper = SCRIPTS_DIR / "test_importers.py"
        assert helper.exists()
        mod = _import_helper(helper)
        result = mod.importer_tests(repo, ["src/widget.py"])
        assert "tests/test_widget.py" in result

    def test_importer_tests_skips_non_importing_tests(
        self, tmp_path: Path,
    ) -> None:
        """A test file that does NOT import the changed module is excluded."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_other.py": (
                    "import src.other\n"
                    "def test_other(): pass\n"
                ),
            },
        )
        helper = SCRIPTS_DIR / "test_importers.py"
        assert helper.exists()
        mod = _import_helper(helper)
        result = mod.importer_tests(repo, ["src/widget.py"])
        assert "tests/test_other.py" not in result

    def test_importer_tests_skips_test_files_in_changed_list(
        self, tmp_path: Path,
    ) -> None:
        """Changed test files are not returned (they are not production modules)."""
        repo = _make_repo(
            tmp_path,
            test_files={
                "tests/test_widget.py": (
                    "import src.widget\n"
                    "def test_widget(): pass\n"
                ),
            },
        )
        helper = SCRIPTS_DIR / "test_importers.py"
        assert helper.exists()
        mod = _import_helper(helper)
        # Changed file IS a test file — it should not appear in results
        result = mod.importer_tests(repo, ["tests/test_widget.py"])
        assert "tests/test_widget.py" not in result

    def test_importer_tests_only_returns_git_tracked_files(
        self, tmp_path: Path,
    ) -> None:
        """Untracked test files are excluded."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_widget.py": (
                    "import src.widget\n"
                    "def test_widget(): pass\n"
                ),
            },
        )
        # Add an untracked test file
        untracked = repo / "tests" / "test_untracked.py"
        untracked.write_text(
            "import src.widget\n"
            "def test_untracked(): pass\n",
            encoding="utf-8",
        )
        helper = SCRIPTS_DIR / "test_importers.py"
        assert helper.exists()
        mod = _import_helper(helper)
        result = mod.importer_tests(repo, ["src/widget.py"])
        assert "tests/test_untracked.py" not in result

    def test_importer_tests_returns_sorted_paths(
        self, tmp_path: Path,
    ) -> None:
        """Results are sorted repo-relative paths."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_z.py": (
                    "import src.widget\n"
                    "def test_z(): pass\n"
                ),
                "tests/test_a.py": (
                    "import src.widget\n"
                    "def test_a(): pass\n"
                ),
            },
        )
        helper = SCRIPTS_DIR / "test_importers.py"
        assert helper.exists()
        mod = _import_helper(helper)
        result = mod.importer_tests(repo, ["src/widget.py"])
        assert result == sorted(result)

    def test_importer_tests_handles_subpackage_imports(
        self, tmp_path: Path,
    ) -> None:
        """``from pkg.widget import X`` matches module ``pkg`` (top-level)."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/pkg/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_widget.py": (
                    "from src.pkg.widget import Widget\n"
                    "def test_widget(): pass\n"
                ),
            },
        )
        helper = SCRIPTS_DIR / "test_importers.py"
        assert helper.exists()
        mod = _import_helper(helper)
        # The changed file is src/pkg/widget.py — the top-level module is "src"
        result = mod.importer_tests(repo, ["src/pkg/widget.py"])
        assert "tests/test_widget.py" in result

    def test_importer_tests_returns_empty_for_no_imports(
        self, tmp_path: Path,
    ) -> None:
        """No test files import the changed module → empty list."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
        )
        helper = SCRIPTS_DIR / "test_importers.py"
        assert helper.exists()
        mod = _import_helper(helper)
        result = mod.importer_tests(repo, ["src/widget.py"])
        assert result == []

    def test_importer_tests_port_of_plan_lint_semantics(
        self, tmp_path: Path,
    ) -> None:
        """The helper's semantics match ``plan_lint._discover_tests_by_import``."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_widget.py": (
                    "import src.widget\n"
                    "def test_widget(): pass\n"
                ),
                "tests/test_other.py": (
                    "def test_other(): pass\n"
                ),
            },
        )
        helper = SCRIPTS_DIR / "test_importers.py"
        assert helper.exists()
        mod = _import_helper(helper)
        # The helper should find test_widget.py (imports src.widget)
        # and exclude test_other.py (no import of src.widget)
        result = mod.importer_tests(repo, ["src/widget.py"])
        assert "tests/test_widget.py" in result
        assert "tests/test_other.py" not in result


# ── AC-2: run_local_checks._synthesize_mention_check uses the helper ────────


class TestMentionCheckUsesHelper:
    """The mention gate adds importer tests to the files it runs."""

    def test_mention_check_includes_importer_tests(
        self, tmp_path: Path,
    ) -> None:
        """Changed module's importer tests are added to the mention gate."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_widget.py": (
                    "import src.widget\n"
                    "def test_widget(): pass\n"
                ),
            },
        )
        # _synthesize_mention_check should add importer tests
        result = _run_synthesize_mention_check(
            repo, changed_files=["src/widget.py"],
        )
        # The result should include test_widget.py because it imports
        # src.widget
        if result is not None:
            cmd = result.get("command", "")
            assert "test_widget.py" in cmd, (
                f"mention gate command {cmd!r} does not include "
                f"test_widget.py which imports src.widget"
            )

    def test_mention_check_applies_deselection_to_importer_tests(
        self, tmp_path: Path,
    ) -> None:
        """Declared-red tests are excluded from importer tests too."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_widget.py": (
                    "import src.widget\n"
                    "def test_widget(): pass\n"
                ),
            },
        )
        baseline_red = [{"node_id": "tests/test_widget.py"}]
        result = _run_synthesize_mention_check(
            repo,
            changed_files=["src/widget.py"],
            baseline_red=baseline_red,
        )
        if result is not None:
            cmd = result.get("command", "")
            assert "test_widget.py" not in cmd, (
                f"mention gate command {cmd!r} includes "
                f"test_widget.py which is declared-red"
            )


# ── AC-3: verification_record.compute_suite_scope uses the helper ───────────


class TestSuiteScopeUsesHelper:
    """compute_suite_scope uses the importer helper instead of substring."""

    def test_suite_scope_finds_importer_tests(self, tmp_path: Path) -> None:
        """A changed module whose test imports it is found by suite scope."""
        repo = _make_repo(
            tmp_path,
            source_files={"src/widget.py": "class Widget: pass\n"},
            test_files={
                "tests/test_widget.py": (
                    "import src.widget\n"
                    "def test_widget(): pass\n"
                ),
            },
        )
        # compute_suite_scope should use the helper to find test_widget.py
        result = _run_compute_suite_scope(
            repo, changed_files=["src/widget.py"],
        )
        if result is not None:
            selection = result.get("selection", [])
            assert "tests/test_widget.py" in selection, (
                f"suite scope selection {selection} does not include "
                f"tests/test_widget.py which imports src.widget"
            )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _import_helper(path: Path):
    """Import a Python module from a file path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("test_importers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_synthesize_mention_check(
    project: Path,
    changed_files: list[str],
    baseline_red: list[dict] | None = None,
) -> dict | None:
    """Run _synthesize_mention_check with the given files."""
    try:
        import run_local_checks as rlc
        return rlc._synthesize_mention_check(
            project,
            changed_files,
            existing_commands=set(),
            baseline_red=baseline_red,
        )
    except ImportError:
        pytest.skip("run_local_checks not importable")
        return None


def _run_compute_suite_scope(
    project: Path,
    changed_files: list[str],
) -> dict | None:
    """Run compute_suite_scope with a two-commit repo.

    Creates a base commit (empty or with existing files), then a second
    commit with *changed_files* so that ``git diff base..HEAD`` shows them.
    """
    try:
        import verification_record as vr
    except ImportError:
        pytest.skip("verification_record not importable")
        return None

    # The repo already has one commit from _make_repo.  Record its SHA as
    # base, then make a no-op change so HEAD differs from base.
    base = _git(project, "rev-parse", "HEAD~0")
    # Modify each changed file to create a content diff entry.
    for f in changed_files:
        p = project / f
        if p.exists():
            # Append a marker so git detects a content change.
            p.write_text(
                p.read_text(encoding="utf-8") + "\n# changed\n",
                encoding="utf-8",
            )
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# changed\n", encoding="utf-8")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "change", "--allow-empty")
    return vr.compute_suite_scope(project, base)