#!/usr/bin/env python3
"""Tests for plan_lint anti-hardcode integration gate (GRIDLOCK Gap-B).

Detects sub-plans that introduce per-instance data (per-stage path, per-tenant
config) and say an existing module should consume it, but no local_check
verifies the consumer actually reads the new data vs a hardcoded constant.
This is the "data-present but runtime-broken" shape where the data exists but
the consumer is still hardcoded to a different source.

Part of sub-plan 2026-06-28-planlint-vertical-slice-anti-hardcode, step 1.
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

from plan_lint import lint_anti_hardcode_integration  # noqa: E402

_PLAN_LINT = SCRIPTS_DIR / "plan_lint.py"


def _run_lint(tmp_path: Path, filename: str, content: str) -> subprocess.CompletedProcess:
    """Write a temp sub-plan and run plan_lint.py against it."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), str(p)],
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )


# ── Should fire: data-present but no consumer read-assertion ────────────

# GRIDLOCK Gap-B shape: per-stage path data, enemy module should consume it,
# but checks only verify the path data exists (not that enemy reads it).
_STAGE_PATH_HARDCODED = """\
---
plan: test-stage-path-hardcode
scope_paths:
  - "src/game/stages/registry.ts"
  - "src/game/enemy.ts"
local_checks:
  - command: npx vitest run tests/test_registry.ts
    timeout: 60
---

# Sub-plan: per-stage path data

Defines distinct path arrays per stage in registry.ts.
The enemy module should consume the active stage path
but is currently hardcoded to the Stage-1 WAYPOINTS constant.
"""

# Per-tenant config introduced, handler should use it, but checks only verify
# the config file parses correctly.
_TENANT_CONFIG_HARDCODED = """\
---
plan: test-tenant-config
scope_paths:
  - "src/config/tenants.py"
  - "src/handlers/request.py"
local_checks:
  - command: python -m pytest tests/test_tenants.py -q
    timeout: 60
---

# Sub-plan: per-tenant config

Adds per-tenant configuration data (rate limits, feature flags).
The request handler should consume tenant config but reads
a hardcoded default value instead.
"""

# Different rails per level, consumer not verified.
_LEVEL_RAILS_HARDCODED = """\
---
plan: test-level-rails
scope_paths:
  - "src/game/levels/registry.py"
local_checks:
  - command: python -m pytest tests/test_levels.py -q
    timeout: 60
---

# Sub-plan: level-specific rail configs

Introduces distinct rail config per level.
Existing enemy module should read the active level's rail data.
"""


class TestAntiHardcodeFires:
    def test_stage_path_hardcoded_flagged(self):
        f = lint_anti_hardcode_integration(_STAGE_PATH_HARDCODED, "test-stage")
        assert len(f) == 1, f
        assert "hardcoded" in f[0].lower() or "Gap-B" in f[0]

    def test_tenant_config_hardcoded_flagged(self):
        f = lint_anti_hardcode_integration(_TENANT_CONFIG_HARDCODED, "test-tenant")
        assert len(f) == 1, f

    def test_level_rails_hardcoded_flagged(self):
        f = lint_anti_hardcode_integration(_LEVEL_RAILS_HARDCODED, "test-level")
        assert len(f) == 1, f


# ── Should NOT fire: has read-assertion ─────────────────────────────────

# Consumer read-assertion present: check verifies enemy reads active stage path.
_HAS_READ_ASSERTION = """\
---
plan: test-read-assertion
scope_paths:
  - "src/game/stages/registry.ts"
  - "src/game/enemy.ts"
local_checks:
  - command: npx vitest run tests/test_registry.ts
    timeout: 60
---

# Sub-plan: per-stage path data with consumer verification

Defines distinct path arrays per stage in registry.ts.
The enemy module should consume the active stage path.
Test verifies enemy reads the active stage path data
and follows it correctly.
"""

# Integration test that checks consumer follows the new data.
_HAS_INTEGRATION_CHECK = """\
---
plan: test-integration-check
scope_paths:
  - "src/game/stages/registry.ts"
  - "src/game/enemy.ts"
local_checks:
  - command: npx vitest run tests/test_stage_integration.ts
    timeout: 60
---

# Sub-plan: stage integration

Per-stage path data with integration test.
Integration test verifies enemy consumes the active stage path.
"""


class TestAntiHardcodeQuiet:
    def test_read_assertion_not_flagged(self):
        assert lint_anti_hardcode_integration(_HAS_READ_ASSERTION, "test-assert") == []

    def test_integration_check_not_flagged(self):
        assert lint_anti_hardcode_integration(_HAS_INTEGRATION_CHECK, "test-integ") == []


# ── Should NOT fire: structural guards (missing one signal) ─────────────

# No per-instance data signal.
_NO_PER_INSTANCE = """\
---
plan: test-no-per-instance
scope_paths:
  - "src/game/enemy.py"
local_checks:
  - command: python -m pytest tests/test_enemy.py -q
    timeout: 60
---

# Sub-plan: enemy speed refactor

Refactors enemy speed calculation. No per-instance data involved.
The enemy module reads from a single config value.
"""

# No consumer-should-read signal.
_NO_CONSUMER_SIGNAL = """\
---
plan: test-no-consumer
scope_paths:
  - "src/game/stages/registry.py"
local_checks:
  - command: python -m pytest tests/test_registry.py -q
    timeout: 60
---

# Sub-plan: stage path data

Defines distinct path arrays per stage.
Tests verify the path data is correctly computed.
"""

# Body doesn't mention hardcoded/consumer at all.
_DATA_ONLY = """\
---
plan: test-data-only
scope_paths:
  - "src/config/settings.py"
local_checks:
  - command: python -m pytest tests/test_settings.py -q
    timeout: 60
---

# Sub-plan: settings refactor

Updates the settings module configuration format.
"""


class TestAntiHardcodeStructural:
    def test_no_per_instance_not_flagged(self):
        assert lint_anti_hardcode_integration(_NO_PER_INSTANCE, "test-no-pi") == []

    def test_no_consumer_signal_not_flagged(self):
        assert lint_anti_hardcode_integration(_NO_CONSUMER_SIGNAL, "test-no-cs") == []

    def test_data_only_not_flagged(self):
        assert lint_anti_hardcode_integration(_DATA_ONLY, "test-data") == []


# ── CLI main() entrypoint ───────────────────────────────────────────────

class TestMainEntrypoint:
    def test_main_surfaces_gap_b(self, tmp_path):
        result = _run_lint(tmp_path, "a.md", _STAGE_PATH_HARDCODED)
        assert result.returncode == 1
        assert "WARN" in result.stdout
        assert "hardcoded" in result.stdout.lower() or "Gap-B" in result.stdout

    def test_main_clean_on_read_assertion(self, tmp_path):
        result = _run_lint(tmp_path, "b.md", _HAS_READ_ASSERTION)
        assert result.returncode == 0
        assert "OK: plan_lint clean" in result.stdout

    def test_main_clean_on_no_per_instance(self, tmp_path):
        result = _run_lint(tmp_path, "c.md", _NO_PER_INSTANCE)
        assert result.returncode == 0
        assert "OK: plan_lint clean" in result.stdout
