#!/usr/bin/env python3
"""Tests for orphan_check.py — built-but-unwired symbol detector.

Verifies:
- Warns when a symbol's only call sites are test files (AC-1)
- Quiet when a symbol has a production call site (AC-2)
- Test-file classifier covers common conventions (AC-3)
- Works without ripgrep via Python fallback (AC-4, step 2)

Part of sub-plan 2026-06-28-orphaned-capability-detector.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from orphan_check import is_test_file, check_symbol  # noqa: E402

_ORPHAN_CHECK = SCRIPTS_DIR / "orphan_check.py"


def _run_check(tmp_path: Path, *symbols: str) -> subprocess.CompletedProcess:
    """Run orphan_check.py against tmp_path with given symbols."""
    cmd = [sys.executable, str(_ORPHAN_CHECK), "--root", str(tmp_path)]
    for sym in symbols:
        cmd.extend(["--symbol", sym])
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )


# ── Test-file classifier (AC-3) ─────────────────────────────────────────

@pytest.mark.parametrize("path,expected", [
    # Python conventions
    ("tests/test_foo.py", True),
    ("tests/foo_test.py", True),
    ("test/test_bar.py", True),
    ("myapp/tests/test_views.py", True),
    ("myapp/test_views.py", True),
    ("foo_test.py", True),
    # JS/TS conventions
    ("__tests__/foo.test.ts", True),
    ("src/__tests__/bar.spec.ts", True),
    ("foo.spec.ts", True),
    ("bar.test.js", True),
    # e2e
    ("e2e/login.spec.ts", True),
    ("tests/e2e/checkout.test.ts", True),
    # Go
    ("pkg/foo_test.go", True),
    # Java / C#
    ("src/FooTest.java", True),
    ("tests/BarTests.cs", True),
    # Ruby
    ("spec/models/user_spec.rb", True),
    # Non-test files
    ("src/foo.py", False),
    ("lib/bar.ts", False),
    ("pkg/baz.go", False),
    ("main.py", False),
    ("src/utils/helper.js", False),
    # Test-adjacent but NOT test files
    ("test_data/config.json", False),
    ("test_helpers/util.py", False),
])
class TestTestFileClassifier:
    def test_classifies_correctly(self, path: str, expected: bool):
        assert is_test_file(path) == expected, f"{path} should be {'test' if expected else 'production'}"


# ── Core orphan detection (AC-1, AC-2) ─────────────────────────────────

def _create_fixture_tree(root: Path, files: dict[str, str]):
    """Create a file tree under *root* from a {relative_path: content} dict."""
    for relpath, content in files.items():
        p = root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")


class TestOrphanDetection:
    def test_unwired_symbol_warns(self, tmp_path):
        """AC-1: symbol defined + referenced only in test files → warns."""
        _create_fixture_tree(tmp_path, {
            "src/tower.py": textwrap.dedent("""\
                class Tower:
                    def upgrade(self):
                        pass
                """),
            "tests/test_tower.py": textwrap.dedent("""\
                from src.tower import Tower
                def test_upgrade():
                    t = Tower()
                    t.upgrade()
                """),
        })
        result = check_symbol(str(tmp_path), "upgrade")
        assert result["orphaned"] is True
        assert result["test_refs"] > 0
        assert result["prod_refs"] == 0

    def test_wired_symbol_quiet(self, tmp_path):
        """AC-2: symbol has a production call site → not orphaned."""
        _create_fixture_tree(tmp_path, {
            "src/tower.py": textwrap.dedent("""\
                class Tower:
                    def upgrade(self):
                        pass
                """),
            "src/game_loop.py": textwrap.dedent("""\
                from src.tower import Tower
                def tick():
                    t = Tower()
                    t.upgrade()
                """),
            "tests/test_tower.py": textwrap.dedent("""\
                from src.tower import Tower
                def test_upgrade():
                    t = Tower()
                    t.upgrade()
                """),
        })
        result = check_symbol(str(tmp_path), "upgrade")
        assert result["orphaned"] is False
        assert result["prod_refs"] > 0

    def test_no_references_at_all(self, tmp_path):
        """Symbol not found anywhere → not orphaned (no test refs either)."""
        _create_fixture_tree(tmp_path, {
            "src/tower.py": "pass\n",
        })
        result = check_symbol(str(tmp_path), "nonexistent_symbol")
        assert result["orphaned"] is False
        assert result["total_refs"] == 0

    def test_multiple_symbols(self, tmp_path):
        """Check multiple symbols independently."""
        _create_fixture_tree(tmp_path, {
            "src/tower.py": textwrap.dedent("""\
                class Tower:
                    def upgrade(self):
                        pass
                    def sell(self):
                        pass
                """),
            "src/game_loop.py": textwrap.dedent("""\
                from src.tower import Tower
                def tick():
                    t = Tower()
                    t.sell()
                """),
            "tests/test_tower.py": textwrap.dedent("""\
                from src.tower import Tower
                def test_upgrade():
                    Tower().upgrade()
                def test_sell():
                    Tower().sell()
                """),
        })
        r1 = check_symbol(str(tmp_path), "upgrade")
        r2 = check_symbol(str(tmp_path), "sell")
        assert r1["orphaned"] is True, "upgrade should be orphaned"
        assert r2["orphaned"] is False, "sell should not be orphaned (has prod ref)"


# ── CLI integration ─────────────────────────────────────────────────────

class TestCLI:
    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(_ORPHAN_CHECK), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "orphan_check" in result.stdout.lower() or "--root" in result.stdout

    def test_warns_on_unwired(self, tmp_path):
        _create_fixture_tree(tmp_path, {
            "src/tower.py": "def upgrade(): pass\n",
            "tests/test_tower.py": "import tower; tower.upgrade()\n",
        })
        result = _run_check(tmp_path, "upgrade")
        assert result.returncode == 1
        assert "WARN" in result.stdout
        assert "upgrade" in result.stdout

    def test_clean_on_wired(self, tmp_path):
        _create_fixture_tree(tmp_path, {
            "src/tower.py": "def upgrade(): pass\n",
            "src/game.py": "import tower; tower.upgrade()\n",
            "tests/test_tower.py": "import tower; tower.upgrade()\n",
        })
        result = _run_check(tmp_path, "upgrade")
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_json_output(self, tmp_path):
        _create_fixture_tree(tmp_path, {
            "src/tower.py": "def upgrade(): pass\n",
            "tests/test_tower.py": "import tower; tower.upgrade()\n",
        })
        result = _run_check_json(tmp_path, "upgrade")
        assert result.returncode == 1
        import json
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["orphaned"] is True


def _run_check_json(tmp_path: Path, *symbols: str) -> subprocess.CompletedProcess:
    """Run orphan_check.py with --json flag."""
    cmd = [sys.executable, str(_ORPHAN_CHECK), "--root", str(tmp_path), "--json"]
    for sym in symbols:
        cmd.extend(["--symbol", sym])
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )
