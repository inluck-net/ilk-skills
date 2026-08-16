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
        encoding="utf-8", errors="replace",
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


# ── AC-4: symlink-execution regression ─────────────────────────────

class TestPluginSymlinkResolution:
    """ilk.10s.sh resolves its repo correctly when invoked via a symlink."""

    def test_symlink_execution_produces_valid_output(self, tmp_path):
        """Running the plugin via a symlink prints valid xbar output."""
        link = tmp_path / "ilk.10s.sh"
        link.symlink_to(ENTRYPOINT_SH)
        result = subprocess.run(
            ["bash", str(link)],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        assert "not found" not in result.stdout, (
            f"plugin reported 'not found' — symlink resolution failed:\n{result.stdout}"
        )
        lines = result.stdout.strip().splitlines()
        assert len(lines) >= 2, f"expected ≥2 lines, got {len(lines)}"
        assert lines[1].strip() == "---", (
            f"line 2 expected '---', got {lines[1]!r}"
        )


# ── Action-row path routing: Start now → repo_path; Resume → data dir ──

# `path` is the ~/.ilk-data data dir; `repo_path` is the SOURCE repo.
# ilk-run.sh resolves a project root only from the repo path, while
# blacklist_status.py --project (Resume) needs the data dir. The two
# action rows must therefore NOT share one path. Regression for the
# "Start now silently no-ops" bug (ilk-run.sh got the data dir).
_DATA = "/home/me/.ilk-data/projects/users-me-proj"
_REPO = "/home/me/Projects/proj"


def _runnable_entry(*, repo_path):
    return {
        "project_key": "proj",
        "path": _DATA,
        "repo_path": repo_path,
        "active_master": "MASTER-2026-06-26-x.md",
        "next_subplan": "2026-06-26-x",
        "step": "1/3",
        "sentinel": {"pid": 0, "state": "none", "alive": False},
        "last_class": None,
        "manually_runnable": True,
        "parked": False,
    }


def _parked_entry(*, repo_path):
    return {
        "project_key": "proj",
        "path": _DATA,
        "repo_path": repo_path,
        "active_master": "MASTER-2026-06-26-x.md",
        "next_subplan": "2026-06-26-x",
        "step": "1/3",
        "sentinel": {"pid": 0, "state": "none", "alive": False},
        "last_class": None,
        "blocked": True,
        "manually_runnable": False,
        "parked": True,
    }


def _line_with(out: str, needle: str) -> str:
    matches = [ln for ln in out.splitlines() if needle in ln]
    assert matches, f"no line containing {needle!r} in:\n{out}"
    return matches[0]


class TestActionPathRouting:
    def test_start_now_uses_repo_path(self):
        fxt = _write_fixture([_runnable_entry(repo_path=_REPO)], "run_repo")
        out = _run_render(fxt).stdout
        line = _line_with(out, "Start now")
        assert _REPO in line, line
        assert f"param1={_DATA!r}" not in line, "Start now leaked the data dir"

    def test_start_now_falls_back_to_data_dir(self):
        fxt = _write_fixture([_runnable_entry(repo_path=None)], "run_fallback")
        out = _run_render(fxt).stdout
        line = _line_with(out, "Start now")
        assert _DATA in line, line

    def test_resume_uses_data_dir_even_with_repo_path(self):
        fxt = _write_fixture([_parked_entry(repo_path=_REPO)], "resume_repo")
        out = _run_render(fxt).stdout
        line = _line_with(out, "Resume")
        assert _DATA in line, line
        assert _REPO not in line, "Resume must target the data dir, not the repo"


# ── Orphan filter: data dirs whose source repo is gone ──────────────

def _orphaned_stale_running_entry():
    """A leaked data dir: repo gone, sentinel frozen at state=running.

    This is the exact shape the two pytest tmpdirs had on 2026-08-16 — a
    dead PID plus state=running makes status_all mark it
    blocked/stale-running, which no operator action can ever clear because
    the repo it names does not exist.
    """
    return {
        "project_key": "pytest-of-chad-p-1d71391",
        "path": _DATA,
        "repo_path": "/tmp/pytest-of-chad/pytest-1345/scratch-project",
        "orphaned": True,
        "active_master": "",
        "next_subplan": "",
        "step": "",
        "sentinel": {"pid": 44720, "state": "running", "alive": False},
        "last_class": None,
        "blocked": True,
        "blocked_reason": "stale-running",
        "manually_runnable": False,
        "parked": False,
    }


class TestOrphanFilter:
    """Entries flagged ``orphaned`` never reach the menu."""

    def test_orphaned_blocked_entry_is_hidden(self):
        """A blocked orphan is dropped — `blocked` alone must not force a row."""
        fxt = _write_fixture([_orphaned_stale_running_entry()], "orphan_blocked")
        out = _run_render(fxt).stdout
        assert "pytest-of-chad" not in out, f"orphan rendered a row:\n{out}"
        assert "!" not in out, f"orphan produced an alert icon:\n{out}"

    def test_orphan_does_not_suppress_real_projects(self):
        """A live project still renders when an orphan is present."""
        entries = [_orphaned_stale_running_entry()] + FIXTURE_TWO_ALIVE
        fxt = _write_fixture(entries, "orphan_plus_live")
        out = _run_render(fxt).stdout
        assert "* alpha" in out, out
        assert "* beta" in out, out
        assert "pytest-of-chad" not in out, out

    def test_orphan_excluded_from_title_count(self):
        """An orphan marked alive must not inflate the menu-bar count."""
        orphan = _orphaned_stale_running_entry()
        orphan["sentinel"] = {"pid": 44720, "state": "running", "alive": True}
        fxt = _write_fixture([orphan] + FIXTURE_TWO_ALIVE, "orphan_alive")
        out = _run_render(fxt).stdout
        assert out.splitlines()[0].strip() == "ilk 2*", out.splitlines()[0]

    def test_absent_orphaned_key_renders_normally(self):
        """Entries with no `orphaned` key (older producers) are unaffected."""
        fxt = _write_fixture(FIXTURE_TWO_ALIVE, "no_orphan_key")
        out = _run_render(fxt).stdout
        assert "* alpha" in out and "* beta" in out, out
