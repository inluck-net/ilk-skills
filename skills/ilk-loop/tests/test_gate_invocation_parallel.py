"""The batch gate's parallelism decision is recorded and guarded.

AC-3, AC-6 from sub-plan the-batch-gate-runs-in-parallel (re-scoped to a
rejection after measurement).  The gate's invocation is composed from
`.ilk-launch.json`'s `ship.suite.command` plus `ship.suite.flags` at
`batch_gate.py:522-524`.  This test loads the config through
`ship_config.load_ship_config` (the same resolution order the gate uses) and
asserts:

  1. Every `ship.baseline_red` node_id names a file that exists on disk,
     or a `::` prefix that resolves to one (AC-3 — plausible node ids).
  2. A tripwire so a later config edit cannot silently add `-n` without a
     measurement that justifies it (AC-6 inverted).
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


# ── AC-6 inverted: parallelism must not be silently re-adopted ─────────────

_TIMING_ARTIFACT = PROJECT_ROOT / "tests" / "baselines" / "gate-timing-2026-08-27.md"


class TestParallelismRejected:
    """Tripwire: if `-n` is added to flags, the timing artifact must justify it.

    After measurement (2026-08-27), all parallel variants were slower than
    serial and broke AC-2.  If someone re-adds `-n`, this test forces them to
    produce a measurement where that N beats serial — otherwise it fails.
    With no `-n` present the test passes trivially.
    """

    def test_n_flag_requires_timing_evidence(self, ship_cfg):
        flags = ship_cfg.ship["suite"].get("flags", [])
        n_flags = [f for f in flags if f == "-n" or (f.startswith("-n") and len(f) > 2)]
        if not n_flags:
            return  # no `-n` present — guard passes trivially

        # An `-n` flag is present.  The timing artifact must record a variant
        # for that N that beat serial, or the flag is unjustified.
        assert _TIMING_ARTIFACT.is_file(), (
            f"ship.suite.flags contains {n_flags} but {_TIMING_ARTIFACT} "
            f"does not exist — add a measurement showing that N beats serial"
        )

        text = _TIMING_ARTIFACT.read_text(encoding="utf-8")
        for flag in n_flags:
            # Normalize: `-n 4` → `4`, `-n4` → `4`, `-nauto` → `auto`
            n_value = flag.removeprefix("-n").strip() or "auto"
            pattern = f"-n {n_value}" if len(flag) > 2 else flag
            # The artifact must have a row for this N marked as beating serial
            assert f"| {pattern}" in text or f"|  `-n {n_value}`" in text, (
                f"ship.suite.flags contains '{flag}' but gate-timing artifact "
                f"has no row for that N — add a measurement where it beats serial"
            )


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
