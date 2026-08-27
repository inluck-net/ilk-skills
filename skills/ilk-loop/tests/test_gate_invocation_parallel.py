"""The batch gate invocation must declare its parallelism flag.

AC-1, AC-3, AC-6 from sub-plan the-batch-gate-runs-in-parallel.  The gate's
invocation is composed from `.ilk-launch.json`'s `ship.suite.command` plus
`ship.suite.flags` at `batch_gate.py:522-524`.  This test loads the config
through `ship_config.load_ship_config` (the same resolution order the gate
uses) and asserts:

  1. The composed invocation contains an `-n` flag (AC-1).
  2. Every `ship.baseline_red` node_id names a file that exists on disk,
     or a `::` prefix that resolves to one (AC-3 — plausible node ids).
  3. A regression test so a later config edit cannot silently drop the flag
     (AC-6).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ILK_SHIP_SCRIPTS = PROJECT_ROOT / "skills" / "ilk-ship" / "scripts"


@pytest.fixture()
def ship_cfg():
    """Load the real ship config via the canonical loader."""
    sys.path.insert(0, str(ILK_SHIP_SCRIPTS))
    try:
        from ship_config import ShipConfig, load_ship_config
    finally:
        sys.path.pop(0)

    result = load_ship_config(PROJECT_ROOT)
    if not isinstance(result, ShipConfig):
        pytest.skip(f"ship config not available: {result}")
    return result


def _composed_invocation(cfg) -> str:
    """Reproduce batch_gate.py:522-524 — command + flags."""
    cmd = cfg.ship["suite"]["command"]
    flags = cfg.ship["suite"].get("flags", [])
    return cmd if not flags else f"{cmd} {' '.join(flags)}"


# ── AC-1: the invocation carries a parallelism flag ────────────────────────

class TestInvocationDeclaresParallelism:
    """The composed invocation must contain an `-n` flag."""

    def test_invocation_contains_n_flag(self, ship_cfg):
        inv = _composed_invocation(ship_cfg)
        tokens = inv.split()
        has_n = any(
            t == "-n" or t.startswith("-n") and len(t) > 2
            for t in tokens
        )
        # Also accept `--dist=loadscope` or similar xdist patterns,
        # but the expected form is `-n <N>` or `-n auto`.
        assert has_n, (
            f"composed invocation has no -n flag: {inv!r}"
        )

    def test_flags_list_has_n_entry(self, ship_cfg):
        """Direct check on the flags list, not the composed string."""
        flags = ship_cfg.ship["suite"].get("flags", [])
        has_n = any(f == "-n" or f.startswith("-n") for f in flags)
        assert has_n, f"flags list has no -n entry: {flags}"


# ── AC-3: baseline_red node_ids are plausible ─────────────────────────────

class TestBaselineRedPlausibility:
    """Every baseline_red node_id must name an existing file or resolvable prefix."""

    @staticmethod
    def _resolve_node_id(node_id: str) -> Path | None:
        """Resolve a node_id to a filesystem path.

        Plain path: ``skills/foo/tests/test_bar.py``
        With ``::``: ``skills/foo/tests/test_bar.py::TestClass::test_method``

        Returns the path if the file exists, else None.
        """
        prefix = node_id.split("::")[0]
        candidate = PROJECT_ROOT / prefix
        return candidate if candidate.is_file() else None

    def test_all_baseline_red_files_exist(self, ship_cfg):
        baseline_red = ship_cfg.ship.get("baseline_red", [])
        if not baseline_red:
            pytest.skip("no baseline_red entries")

        missing = []
        for entry in baseline_red:
            nid = entry["node_id"]
            resolved = self._resolve_node_id(nid)
            if resolved is None:
                missing.append(nid)

        assert not missing, (
            f"{len(missing)} baseline_red node_id(s) do not resolve to "
            f"existing files: {missing}"
        )

    def test_baseline_red_count(self, ship_cfg):
        """Guard against accidental removal of baseline_red entries."""
        baseline_red = ship_cfg.ship.get("baseline_red", [])
        assert len(baseline_red) >= 5, (
            f"expected >= 5 baseline_red entries (sub-plan documents 5), "
            f"got {len(baseline_red)}"
        )
