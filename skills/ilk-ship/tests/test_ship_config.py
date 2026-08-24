"""Tests for ship_config.py — the ship: block loader.

AC-2: Precedence test (location 1 beats location 3 when both exist).
AC-1: Resolved-path reporting + which location.
AC-3: Missing ship: → NotConfigured (not an error).
AC-4: Malformed ship: → MalformedConfig (distinguishable from not-configured).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the scripts directory is importable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from ship_config import (
    Location,
    MalformedConfig,
    NotConfigured,
    ShipConfig,
    load_ship_config,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _write_json(path: Path, data: dict) -> None:
    """Write a JSON file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal project root with .git."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _ext_plans(tmp_path: Path) -> Path:
    """Simulate the external plans dir for testing.

    We cannot call ilk_paths.project_key() here because the tmp_path
    won't match a real ~/.ilk-data entry. Instead we plant configs
    directly and mock the resolution.
    """
    return tmp_path / "ext_plans"


# ── Precedence tests (AC-2) ─────────────────────────────────────────────────

class TestPrecedence:
    """Location 1 (external plans) beats location 2 (docs/plans) beats
    location 3 (project root)."""

    def test_location1_wins_over_location3(self, tmp_path: Path) -> None:
        """When a config exists at both location 1 and 3, location 1 wins."""
        project = _make_project(tmp_path)
        loc1 = _ext_plans(tmp_path) / ".ilk-launch.json"
        loc3 = project / ".ilk-launch.json"

        _write_json(loc1, {"ship": {"suite": {"command": "from-loc1"}}})
        _write_json(loc3, {"ship": {"suite": {"command": "from-loc3"}}})

        result = load_ship_config(project, ext_plans_dir=_ext_plans(tmp_path))
        assert isinstance(result, ShipConfig)
        assert result.ship["suite"]["command"] == "from-loc1"
        assert result.location == Location.EXTERNAL_PLANS
        assert result.resolved_path == loc1

    def test_location2_wins_over_location3(self, tmp_path: Path) -> None:
        """When a config exists at both location 2 and 3, location 2 wins."""
        project = _make_project(tmp_path)
        loc2 = project / "docs" / "plans" / ".ilk-launch.json"
        loc3 = project / ".ilk-launch.json"

        _write_json(loc2, {"ship": {"suite": {"command": "from-loc2"}}})
        _write_json(loc3, {"ship": {"suite": {"command": "from-loc3"}}})

        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        assert result.ship["suite"]["command"] == "from-loc2"
        assert result.location == Location.DOCS_PLANS
        assert result.resolved_path == loc2

    def test_location1_wins_over_location2(self, tmp_path: Path) -> None:
        """When a config exists at both location 1 and 2, location 1 wins."""
        project = _make_project(tmp_path)
        loc1 = _ext_plans(tmp_path) / ".ilk-launch.json"
        loc2 = project / "docs" / "plans" / ".ilk-launch.json"

        _write_json(loc1, {"ship": {"suite": {"command": "from-loc1"}}})
        _write_json(loc2, {"ship": {"suite": {"command": "from-loc2"}}})

        result = load_ship_config(project, ext_plans_dir=_ext_plans(tmp_path))
        assert isinstance(result, ShipConfig)
        assert result.ship["suite"]["command"] == "from-loc1"
        assert result.location == Location.EXTERNAL_PLANS

    def test_location2_fallback_when_no_loc1(self, tmp_path: Path) -> None:
        """Location 2 is used when location 1 doesn't exist."""
        project = _make_project(tmp_path)
        loc2 = project / "docs" / "plans" / ".ilk-launch.json"

        _write_json(loc2, {"ship": {"suite": {"command": "from-loc2"}}})

        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        assert result.location == Location.DOCS_PLANS
        assert result.resolved_path == loc2

    def test_location3_fallback_when_no_loc1_or_loc2(self, tmp_path: Path) -> None:
        """Location 3 is used when locations 1 and 2 don't exist."""
        project = _make_project(tmp_path)
        loc3 = project / ".ilk-launch.json"

        _write_json(loc3, {"ship": {"suite": {"command": "from-loc3"}}})

        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        assert result.location == Location.PROJECT_ROOT
        assert result.resolved_path == loc3


# ── Not-configured (AC-3) ──────────────────────────────────────────────────

class TestNotConfigured:
    """A missing ship: block returns NotConfigured, not an error."""

    def test_no_file_at_all(self, tmp_path: Path) -> None:
        """No .ilk-launch.json anywhere → NotConfigured."""
        project = _make_project(tmp_path)
        result = load_ship_config(project)
        assert isinstance(result, NotConfigured)

    def test_file_exists_but_no_ship_key(self, tmp_path: Path) -> None:
        """File exists with other keys but no ship: → NotConfigured."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "max_iterations": 5,
            "iteration_timeout_min": 30,
        })
        result = load_ship_config(project)
        assert isinstance(result, NotConfigured)
        assert result.resolved_path == project / ".ilk-launch.json"

    def test_ship_key_is_null(self, tmp_path: Path) -> None:
        """ship: null → NotConfigured, not malformed."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {"ship": None})
        result = load_ship_config(project)
        assert isinstance(result, NotConfigured)


# ── Malformed (AC-4) ───────────────────────────────────────────────────────

class TestMalformedConfig:
    """A malformed ship: block is an error, distinguishable from not-configured."""

    def test_ship_not_a_dict(self, tmp_path: Path) -> None:
        """ship: "string" → MalformedConfig."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {"ship": "bad"})
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)
        assert "ship" in result.detail

    def test_ship_is_a_list(self, tmp_path: Path) -> None:
        """ship: [...] → MalformedConfig."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {"ship": [1, 2]})
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)

    def test_suite_not_a_dict(self, tmp_path: Path) -> None:
        """ship.suite: "bad" → MalformedConfig."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {"suite": "bad"},
        })
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)
        assert "suite" in result.detail

    def test_suite_command_missing(self, tmp_path: Path) -> None:
        """ship.suite exists but no command → MalformedConfig."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {"suite": {"flags": ["--timeout-method=signal"]}},
        })
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)
        assert "command" in result.detail

    def test_baseline_red_entry_missing_reason(self, tmp_path: Path) -> None:
        """A baseline_red entry without reason → MalformedConfig (AC-5)."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {"command": "pytest"},
                "baseline_red": [
                    {"node_id": "test_foo.py"},
                ],
            },
        })
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)
        assert "reason" in result.detail

    def test_baseline_red_reason_empty_string(self, tmp_path: Path) -> None:
        """A baseline_red entry with empty reason → MalformedConfig."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {"command": "pytest"},
                "baseline_red": [
                    {"node_id": "test_foo.py", "reason": ""},
                ],
            },
        })
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)

    def test_malformed_reports_resolved_path(self, tmp_path: Path) -> None:
        """MalformedConfig includes which file was malformed."""
        project = _make_project(tmp_path)
        f = project / ".ilk-launch.json"
        _write_json(f, {"ship": "bad"})
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)
        assert result.resolved_path == f


# ── Valid config (AC-1, AC-7) ──────────────────────────────────────────────

class TestValidConfig:
    """A valid ship: block returns ShipConfig with path and location."""

    def test_full_config(self, tmp_path: Path) -> None:
        """All fields present and valid."""
        project = _make_project(tmp_path)
        config = {
            "ship": {
                "suite": {
                    "command": "python3 -m pytest",
                    "flags": ["--timeout-method=signal"],
                    "timeout": 300,
                },
                "baseline_red": [
                    {
                        "node_id": "test_foo.py",
                        "reason": "20 failures at v0.9.62",
                        "as_of": "2026-08-14",
                    },
                ],
                "hosts": ["chad-mbp", "rezmac"],
                "path_prelude": "export PATH=\"/opt/homebrew/bin:$PATH\"",
            },
        }
        _write_json(project / ".ilk-launch.json", config)

        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        assert result.ship["suite"]["command"] == "python3 -m pytest"
        assert result.ship["suite"]["flags"] == ["--timeout-method=signal"]
        assert result.ship["suite"]["timeout"] == 300
        assert len(result.ship["baseline_red"]) == 1
        assert result.ship["hosts"] == ["chad-mbp", "rezmac"]
        assert result.location == Location.PROJECT_ROOT

    def test_minimal_config(self, tmp_path: Path) -> None:
        """Only suite.command is required."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {"suite": {"command": "npm test"}},
        })

        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        assert result.ship["suite"]["command"] == "npm test"

    def test_suite_flags_default_empty(self, tmp_path: Path) -> None:
        """flags defaults to [] when omitted."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {"suite": {"command": "pytest"}},
        })

        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        assert result.ship["suite"]["flags"] == []

    def test_existing_max_iterations_not_disturbed(self, tmp_path: Path) -> None:
        """A ship: block coexists with max_iterations / iteration_timeout_min."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "max_iterations": 5,
            "iteration_timeout_min": 30,
            "ship": {"suite": {"command": "pytest"}},
        })

        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        # The ship: loader only extracts the ship key; other keys are untouched.
        assert result.ship["suite"]["command"] == "pytest"


# ── Staleness (AC-6) ───────────────────────────────────────────────────────

class TestSuiteRoundTrip:
    """suite.command and suite.flags round-trip exactly (AC-7)."""

    def test_command_and_flags_preserved(self, tmp_path: Path) -> None:
        """command + flags survive write → load unchanged."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {
                    "command": "python3 -m pytest",
                    "flags": ["--timeout-method=signal", "--timeout=60"],
                },
            },
        })
        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        assert result.ship["suite"]["command"] == "python3 -m pytest"
        assert result.ship["suite"]["flags"] == [
            "--timeout-method=signal", "--timeout=60",
        ]

    def test_flags_none_normalized_to_empty_list(self, tmp_path: Path) -> None:
        """flags: null → [] after load, not None."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {"command": "pytest", "flags": None},
            },
        })
        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        assert result.ship["suite"]["flags"] == []
        # Caller can reconstruct invocation as: command + " ".join(flags)

    def test_flags_omitted_normalized_to_empty_list(self, tmp_path: Path) -> None:
        """flags absent → [] after load."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {"suite": {"command": "pytest"}},
        })
        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        assert result.ship["suite"]["flags"] == []

    def test_suite_command_must_be_non_empty_string(self, tmp_path: Path) -> None:
        """suite.command: '' → MalformedConfig."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {"suite": {"command": ""}},
        })
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)
        assert "command" in result.detail

    def test_baseline_red_entry_missing_node_id(self, tmp_path: Path) -> None:
        """A baseline_red entry without node_id → MalformedConfig (AC-5)."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {"command": "pytest"},
                "baseline_red": [
                    {"reason": "something"},
                ],
            },
        })
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)
        assert "node_id" in result.detail


class TestStaleness:
    """baseline_red entries older than a threshold are reported as stale."""

    def test_stale_entry_reported(self, tmp_path: Path) -> None:
        """An entry with as_of older than threshold is marked stale."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {"command": "pytest"},
                "baseline_red": [
                    {
                        "node_id": "test_foo.py",
                        "reason": "old failure",
                        "as_of": "2026-01-01",
                    },
                ],
            },
        })

        result = load_ship_config(project, staleness_days=30)
        assert isinstance(result, ShipConfig)
        assert len(result.stale_exclusions) == 1
        assert result.stale_exclusions[0] == "test_foo.py"

    def test_fresh_entry_not_stale(self, tmp_path: Path) -> None:
        """A recent entry is not marked stale."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {
                "suite": {"command": "pytest"},
                "baseline_red": [
                    {
                        "node_id": "test_foo.py",
                        "reason": "current failure",
                        "as_of": "2026-08-14",
                    },
                ],
            },
        })

        result = load_ship_config(project, staleness_days=30)
        assert isinstance(result, ShipConfig)
        assert result.stale_exclusions == []


# ── Edge cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Empty file, invalid JSON, empty ship block."""

    def test_empty_json_object(self, tmp_path: Path) -> None:
        """{} → NotConfigured."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {})
        result = load_ship_config(project)
        assert isinstance(result, NotConfigured)

    def test_invalid_json(self, tmp_path: Path) -> None:
        """Invalid JSON → MalformedConfig."""
        project = _make_project(tmp_path)
        f = project / ".ilk-launch.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{invalid json", encoding="utf-8")
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file → MalformedConfig (not valid JSON)."""
        project = _make_project(tmp_path)
        f = project / ".ilk-launch.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("", encoding="utf-8")
        result = load_ship_config(project)
        assert isinstance(result, MalformedConfig)


# ── CLI smoke ───────────────────────────────────────────────────────────────

class TestCLI:
    """The --validate CLI verb reports the resolved file."""

    def test_validate_not_configured(self, tmp_path: Path, capsys) -> None:
        """--validate on a project with no config prints not configured."""
        project = _make_project(tmp_path)
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "ship_config.py"),
             "--validate", "--project", str(project)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 2
        assert "not configured" in proc.stdout.lower() or "not configured" in proc.stderr.lower()

    def test_validate_with_config(self, tmp_path: Path) -> None:
        """--validate on a project with a valid config prints the path."""
        project = _make_project(tmp_path)
        _write_json(project / ".ilk-launch.json", {
            "ship": {"suite": {"command": "pytest"}},
        })
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "ship_config.py"),
             "--validate", "--project", str(project)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert ".ilk-launch.json" in proc.stdout


# ── consumer_reads_config gate test (step 2's third gate) ───────────────────

class TestConsumerReadsConfig:
    """Assert that a consumer reads the resolved file, not a hardcoded default.

    This is the gate that step 2's third local_check exercises. It must
    assert the consumer reads the resolved file, not a hardcoded default.
    """

    def test_consumer_reads_from_actual_file(self, tmp_path: Path) -> None:
        """The loader returns the ship: block from the file, not a built-in."""
        project = _make_project(tmp_path)
        custom_command = "my-custom-test-runner --special-flag"
        _write_json(project / ".ilk-launch.json", {
            "ship": {"suite": {"command": custom_command}},
        })

        result = load_ship_config(project)
        assert isinstance(result, ShipConfig)
        # The command must come from the file, not a default
        assert result.ship["suite"]["command"] == custom_command
