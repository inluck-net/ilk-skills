"""Red test: xbar/SwiftBar menu-bar plugin over status_all --json.

AC-1: render_xbar.py --json-from <fixture> prints valid xbar output: a
      non-empty first line (menu-bar title), a `---` separator, and one row
      per project containing its key + state.  Exit 0.

AC-2: Title reflects live count — fixture with 2 `alive` entries → title
      shows `2` (or `▣`-style live marker); all-idle fixture → an idle
      marker, no error.

AC-3: ilk.10s.sh is executable, has the xbar metadata header comment, and
      calls status_all then render_xbar.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root — three levels up from this file (tests/ -> ilk-watchdog/ -> skills/ -> root).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RENDER_PY = REPO_ROOT / "tools" / "xbar" / "render_xbar.py"
ENTRYPOINT_SH = REPO_ROOT / "tools" / "xbar" / "ilk.10s.sh"

# Scratch dir inside the repo (gitignored).
SCRATCH = REPO_ROOT / "scratch" / "xbar"


# ── sample fixtures ─────────────────────────────────────────────────

FIXTURE_TWO_ALIVE = [
    {
        "project_key": "alpha",
        "path": "/home/user/.ilk-data/projects/alpha",
        "active_master": "MASTER-2026-06-07-execution-plan.md",
        "next_subplan": "2026-06-07-auth",
        "step": "2/5",
        "sentinel": {"pid": 12345, "state": "running", "alive": True},
        "last_class": None,
    },
    {
        "project_key": "beta",
        "path": "/home/user/.ilk-data/projects/beta",
        "active_master": "MASTER-2026-06-07-execution-plan.md",
        "next_subplan": "2026-06-07-ui",
        "step": "1/3",
        "sentinel": {"pid": 23456, "state": "running", "alive": True},
        "last_class": None,
    },
]

FIXTURE_ALL_IDLE = [
    {
        "project_key": "alpha",
        "path": "/home/user/.ilk-data/projects/alpha",
        "active_master": "MASTER-2026-06-07-execution-plan.md",
        "next_subplan": "",
        "step": "",
        "sentinel": {"pid": 0, "state": "none", "alive": False},
        "last_class": None,
    },
    {
        "project_key": "beta",
        "path": "/home/user/.ilk-data/projects/beta",
        "active_master": "",
        "next_subplan": "",
        "step": "",
        "sentinel": {"pid": 0, "state": "none", "alive": False},
        "last_class": "ok",
    },
]


# ── helpers ─────────────────────────────────────────────────────────

def _write_fixture(data: list, name: str) -> Path:
    """Write a JSON fixture to scratch dir, return its path."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    p = SCRATCH / f"{name}.json"
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def _run_render(fixture_path: Path) -> subprocess.CompletedProcess:
    """Run render_xbar.py with --json-from, return result."""
    return subprocess.run(
        [sys.executable, str(RENDER_PY), "--json-from", str(fixture_path)],
        capture_output=True, text=True, timeout=15,
    )


# ── fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    yield
    import shutil
    shutil.rmtree(SCRATCH, ignore_errors=True)


# ── AC-1: valid xbar output format ─────────────────────────────────

class TestAC1_ValidXbarFormat:
    """render_xbar.py emits valid xbar output."""

    def test_exit_zero(self):
        """Exits 0 on valid input."""
        fxt = _write_fixture(FIXTURE_TWO_ALIVE, "two_alive")
        result = _run_render(fxt)
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

    def test_first_line_non_empty(self):
        """First line (menu-bar title) is non-empty."""
        fxt = _write_fixture(FIXTURE_TWO_ALIVE, "two_alive")
        result = _run_render(fxt)
        lines = result.stdout.strip().splitlines()
        assert len(lines) >= 2, f"expected ≥2 lines, got {len(lines)}"
        assert len(lines[0].strip()) > 0, "first line (title) is empty"

    def test_second_line_is_separator(self):
        """Second line is the xbar separator `---`."""
        fxt = _write_fixture(FIXTURE_TWO_ALIVE, "two_alive")
        result = _run_render(fxt)
        lines = result.stdout.strip().splitlines()
        assert lines[1].strip() == "---", f"expected '---', got {lines[1]!r}"

    def test_rows_contain_project_keys(self):
        """Each project key appears in the output rows."""
        fxt = _write_fixture(FIXTURE_TWO_ALIVE, "two_alive")
        result = _run_render(fxt)
        out = result.stdout.lower()
        assert "alpha" in out, "missing project 'alpha'"
        assert "beta" in out, "missing project 'beta'"

    def test_rows_contain_liveness_marker(self):
        """Alive projects are marked with * icon."""
        fxt = _write_fixture(FIXTURE_TWO_ALIVE, "two_alive")
        result = _run_render(fxt)
        out = result.stdout
        # alive projects get a * icon
        assert "* alpha" in out, "missing liveness marker for alpha"
        assert "* beta" in out, "missing liveness marker for beta"


# ── AC-2: title reflects live count ────────────────────────────────

class TestAC2_TitleReflectsLiveCount:
    """Menu-bar title shows how many loops are alive."""

    def test_two_alive_shows_count(self):
        """Fixture with 2 alive entries → title includes '2' or live marker."""
        fxt = _write_fixture(FIXTURE_TWO_ALIVE, "two_alive")
        result = _run_render(fxt)
        title = result.stdout.strip().splitlines()[0]
        # Accept either a digit "2" or a marker like "*"
        assert "2" in title or "*" in title or "alive" in title.lower(), \
            f"title does not reflect 2 alive: {title!r}"

    def test_all_idle_shows_idle_marker(self):
        """All-idle fixture → title has an idle/clear marker, no error."""
        fxt = _write_fixture(FIXTURE_ALL_IDLE, "all_idle")
        result = _run_render(fxt)
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        title = result.stdout.strip().splitlines()[0]
        # Should not show an error; may show ✓, idle, 0, etc.
        assert "error" not in title.lower(), f"title shows error: {title!r}"


# ── AC-3: entrypoint shell script ──────────────────────────────────

class TestAC3_EntrypointShellScript:
    """ilk.10s.sh has xbar metadata and calls status_all + render_xbar."""

    def test_file_exists(self):
        """ilk.10s.sh exists."""
        assert ENTRYPOINT_SH.exists(), f"not found: {ENTRYPOINT_SH}"

    def test_has_xbar_metadata_header(self):
        """First lines contain xbar metadata (refresh interval comment)."""
        text = ENTRYPOINT_SH.read_text(encoding="utf-8")
        # xbar plugins start with a comment like #!/bin/bash and metadata
        # lines prefixed with "# <xbar.*>" — at minimum a title and refresh.
        assert "<xbar." in text.lower() or "xbar" in text.lower(), \
            "missing xbar metadata header"

    def test_calls_status_all(self):
        """Script invokes status_all (via python or direct call)."""
        text = ENTRYPOINT_SH.read_text(encoding="utf-8")
        assert "status_all" in text, "does not reference status_all"

    def test_calls_render_xbar(self):
        """Script pipes to or invokes render_xbar."""
        text = ENTRYPOINT_SH.read_text(encoding="utf-8")
        assert "render_xbar" in text, "does not reference render_xbar"
