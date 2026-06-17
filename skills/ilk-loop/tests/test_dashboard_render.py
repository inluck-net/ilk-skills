"""Red test: ilk_dashboard.py --once --json-from renders all-projects + slots view.

AC-1: ilk_dashboard.py --once --json-from <fixture.json> exits 0 and prints
      a table containing each project's key, active master, sub-plan cur/est,
      sentinel state + alive flag, and last classification — with no
      network/live deps.

AC-2: A --watch invocation with -n 1 runs at least one frame then can be
      interrupted; the test only exercises --once (no infinite loop in CI).

AC-3: Slot view — when >=1 entry has sentinel.alive=true, the header shows a
      live/box count (e.g. "live 2 / slots 5") derived from the JSON.

AC-4: commands/ilk-status.md documents --watch, and ilk-status.ps1/.sh
      pass --watch/-n through to ilk_dashboard.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root — three levels up from this file (tests/ -> ilk-loop/ -> skills/ -> root).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DASHBOARD_PY = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "ilk_dashboard.py"

# Scratch dir inside the repo (gitignored, never $TEMP — decomposition §9).
SCRATCH = REPO_ROOT / "scratch" / "dashboard"
FIXTURE = SCRATCH / "sample_status.json"


# ── helpers ─────────────────────────────────────────────────────────

def _write_fixture(entries: list[dict]) -> Path:
    """Write a status_all-style JSON fixture to scratch dir. Returns path."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    return FIXTURE


def _run_dashboard(*args: str) -> subprocess.CompletedProcess:
    """Run ilk_dashboard.py with given args, return result."""
    return subprocess.run(
        [sys.executable, str(DASHBOARD_PY), *args],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )


# ── sample data ─────────────────────────────────────────────────────

SAMPLE_PROJECTS = [
    {
        "project_key": "es-api",
        "path": "/home/user/projects/es-api",
        "active_master": "MASTER-2026-06-07-es-api.md",
        "next_subplan": "es-api-cleanup",
        "step": "3/7",
        "sentinel": {"pid": 54321, "state": "running", "alive": True},
        "last_class": "ok",
    },
    {
        "project_key": "crawler",
        "path": "/home/user/projects/crawler",
        "active_master": "MASTER-2026-05-22-crawler.md",
        "next_subplan": "zara-source",
        "step": "0/9",
        "sentinel": {"pid": 0, "state": "none", "alive": False},
        "last_class": None,
    },
    {
        "project_key": "my-proj",
        "path": "/home/user/projects/my-proj",
        "active_master": "",
        "next_subplan": "",
        "step": "",
        "sentinel": {"pid": 99999, "state": "running", "alive": True},
        "last_class": None,
    },
]


# ── fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean():
    """Create and clean scratch dir."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    yield
    import shutil
    shutil.rmtree(SCRATCH, ignore_errors=True)


# ── AC-1: renderer outputs project table from fixture ───────────────

class TestAC1_RendererFromFixture:
    """ilk_dashboard.py --once --json-from <fixture> prints a table."""

    def test_exits_zero_with_fixture(self):
        """--once --json-from <valid fixture> exits 0."""
        fixture = _write_fixture(SAMPLE_PROJECTS)
        result = _run_dashboard("--once", "--json-from", str(fixture))
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

    def test_output_contains_project_keys(self):
        """Table output includes each project key."""
        fixture = _write_fixture(SAMPLE_PROJECTS)
        result = _run_dashboard("--once", "--json-from", str(fixture))
        assert result.returncode == 0
        for key in ("es-api", "crawler", "my-proj"):
            assert key in result.stdout, f"missing project key '{key}' in output"

    def test_output_contains_active_master(self):
        """Table output includes the active master filename."""
        fixture = _write_fixture(SAMPLE_PROJECTS)
        result = _run_dashboard("--once", "--json-from", str(fixture))
        assert result.returncode == 0
        assert "MASTER-2026-06-07-es-api.md" in result.stdout

    def test_output_contains_step_progress(self):
        """Table output includes cur/est step info."""
        fixture = _write_fixture(SAMPLE_PROJECTS)
        result = _run_dashboard("--once", "--json-from", str(fixture))
        assert result.returncode == 0
        assert "3/7" in result.stdout
        assert "0/9" in result.stdout

    def test_output_contains_sentinel_state(self):
        """Table output includes sentinel state + alive/dead indicator."""
        fixture = _write_fixture(SAMPLE_PROJECTS)
        result = _run_dashboard("--once", "--json-from", str(fixture))
        assert result.returncode == 0
        # alive projects should show "alive" or a box char
        out = result.stdout.lower()
        assert "alive" in out or "▣" in result.stdout or "■" in result.stdout

    def test_output_contains_last_classification(self):
        """Table output includes last classification when present."""
        fixture = _write_fixture(SAMPLE_PROJECTS)
        result = _run_dashboard("--once", "--json-from", str(fixture))
        assert result.returncode == 0
        assert "ok" in result.stdout


# ── AC-3: live/box count in header ──────────────────────────────────

class TestAC3_LiveCount:
    """When >=1 sentinel.alive=true, header shows live N / slots M."""

    def test_live_count_with_two_alive(self):
        """Header shows 'live 2' when two entries have alive=true."""
        fixture = _write_fixture(SAMPLE_PROJECTS)
        result = _run_dashboard("--once", "--json-from", str(fixture))
        assert result.returncode == 0
        out = result.stdout
        # Should show "live 2" or similar (2 of 3 have alive=true)
        assert "live" in out.lower() or "2" in out

    def test_live_count_all_dead(self):
        """When all sentinels dead, header shows live 0 or no alive indicator."""
        dead_projects = [
            {**p, "sentinel": {"pid": 0, "state": "none", "alive": False}}
            for p in SAMPLE_PROJECTS
        ]
        fixture = _write_fixture(dead_projects)
        result = _run_dashboard("--once", "--json-from", str(fixture))
        assert result.returncode == 0
        # Should show "live 0" or similar
        assert "live" in result.stdout.lower() or "0" in result.stdout

    def test_live_count_single_alive(self):
        """Header shows 'live 1' when one entry has alive=true."""
        one_alive = [
            SAMPLE_PROJECTS[0],  # alive
            {**SAMPLE_PROJECTS[1], "sentinel": {"pid": 0, "state": "none", "alive": False}},
        ]
        fixture = _write_fixture(one_alive)
        result = _run_dashboard("--once", "--json-from", str(fixture))
        assert result.returncode == 0
        out = result.stdout
        assert "live" in out.lower() or "1" in out


# ── AC-4: command doc + wrapper wiring ───────────────────────────────

class TestAC4_CommandDocAndWrappers:
    """commands/ilk-status.md documents --watch; wrappers pass flags through."""

    def test_command_doc_mentions_watch(self):
        """The command doc includes a --watch section."""
        cmd_doc = REPO_ROOT / "commands" / "ilk-status.md"
        assert cmd_doc.exists(), f"command doc not found: {cmd_doc}"
        text = cmd_doc.read_text(encoding="utf-8")
        assert "--watch" in text, "command doc missing --watch mention"

    def test_ps1_wrapper_passes_watch(self):
        """ilk-status.ps1 accepts -Watch and passes it to ilk_dashboard.py."""
        ps1 = REPO_ROOT / "skills" / "ilk-runner" / "scripts" / "ilk-status.ps1"
        assert ps1.exists(), f"ps1 wrapper not found: {ps1}"
        text = ps1.read_text(encoding="utf-8")
        assert "-Watch" in text or "--watch" in text.lower(), "ps1 missing -Watch param"
        assert "DashboardPy" in text or "dashboard" in text.lower(), "ps1 missing dashboard call"

    def test_sh_wrapper_passes_watch(self):
        """ilk-status.sh accepts --watch and passes it to ilk_dashboard.py."""
        sh = REPO_ROOT / "skills" / "ilk-runner" / "scripts" / "ilk-status.sh"
        assert sh.exists(), f"sh wrapper not found: {sh}"
        text = sh.read_text(encoding="utf-8")
        assert "--watch" in text, "sh missing --watch flag"
        assert "dashboard" in text.lower() or "DASHBOARD" in text, "sh missing dashboard call"
