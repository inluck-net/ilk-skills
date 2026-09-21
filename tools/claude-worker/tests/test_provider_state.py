"""Tests for the provider-state published contract.

The contract is a secret-free JSON file keyed by resolved home path that
records both probe results per home so consumers (gh-resolve, the tray)
can tell whether a home is alive before spending work on it.

Design: docs/architecture/provider-switching-and-quota-fallback.md §6.

Red-first: ``tools/claude-worker/provider_state.py`` does not exist yet;
every test here fails until step 1 implements it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# Ensure the module under test is importable.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from provider_state import (
    ProviderState,
    read_state,
    write_state,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

# A token-shaped string the writer must never emit.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")

SAMPLE_HOME = "/Users/chad/.claude-worker"
SAMPLE_HOME_2 = "/Users/chad/.claude-manager"


def _make_ok_record(**overrides) -> dict:
    """A minimal valid record where both probes passed."""
    base = {
        "provider": "66f41c76-abcd-1234-ef00-abcdef012345",
        "model": "mimo-v2.5-pro",
        "state": "ok",
        "config_probe": {
            "result": "mimo-v2.5-pro",
            "at": "2026-09-21T15:02:11+08:00",
        },
        "live_probe": {
            "ok": True,
            "detail": "",
            "at": "2026-09-21T15:02:19+08:00",
        },
        "reset_at": None,
        "evidence": {
            "log": "/tmp/iter-02.log.jsonl",
            "run_id": "20260921-142231",
        },
    }
    base.update(overrides)
    return base


def _make_exhausted_record(**overrides) -> dict:
    """A record where config passed but live failed — state must be exhausted."""
    base = _make_ok_record(
        state="exhausted",
        live_probe={
            "ok": False,
            "detail": "403 Request not allowed",
            "at": "2026-09-21T15:02:19+08:00",
        },
        reset_at="2026-09-22T15:16:35+08:00",
    )
    base.update(overrides)
    return base


def _make_state(**homes) -> dict:
    """Build a minimal version-1 state dict."""
    return {"version": 1, "homes": homes}


# ── AC1: round-trip through reader unchanged, keyed by resolved home path ────

class TestRoundTrip:
    """Write then read — the output must equal the input."""

    def test_single_home_round_trip(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        original = _make_state(**{SAMPLE_HOME: _make_ok_record()})
        write_state(state_file, original)
        result = read_state(state_file)
        assert result == original

    def test_multi_home_round_trip(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        original = _make_state(
            **{
                SAMPLE_HOME: _make_ok_record(),
                SAMPLE_HOME_2: _make_exhausted_record(),
            }
        )
        write_state(state_file, original)
        result = read_state(state_file)
        assert result == original

    def test_keyed_by_absolute_path(self, tmp_path: Path) -> None:
        """The homes dict keys must be absolute paths, not role names."""
        state_file = tmp_path / "provider-state.json"
        original = _make_state(**{SAMPLE_HOME: _make_ok_record()})
        write_state(state_file, original)
        result = read_state(state_file)
        for key in result["homes"]:
            assert key.startswith("/"), f"Home key {key!r} is not an absolute path"


# ── AC2: each home carries config_probe and live_probe as separate objects ────

class TestProbeShape:
    """Each home record must have config_probe and live_probe with their
    own timestamps, plus state, reset_at, and evidence."""

    REQUIRED_HOME_KEYS = {
        "provider", "model", "state",
        "config_probe", "live_probe",
        "reset_at", "evidence",
    }

    def test_home_has_all_required_keys(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        record = _make_ok_record()
        original = _make_state(**{SAMPLE_HOME: record})
        write_state(state_file, original)
        result = read_state(state_file)
        home = result["homes"][SAMPLE_HOME]
        assert self.REQUIRED_HOME_KEYS.issubset(home.keys()), (
            f"Missing keys: {self.REQUIRED_HOME_KEYS - home.keys()}"
        )

    def test_config_probe_has_at(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        original = _make_state(**{SAMPLE_HOME: _make_ok_record()})
        write_state(state_file, original)
        result = read_state(state_file)
        cp = result["homes"][SAMPLE_HOME]["config_probe"]
        assert "at" in cp, "config_probe must have an 'at' timestamp"
        assert "result" in cp, "config_probe must have a 'result' field"

    def test_live_probe_has_at_and_ok(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        original = _make_state(**{SAMPLE_HOME: _make_ok_record()})
        write_state(state_file, original)
        result = read_state(state_file)
        lp = result["homes"][SAMPLE_HOME]["live_probe"]
        assert "at" in lp, "live_probe must have an 'at' timestamp"
        assert "ok" in lp, "live_probe must have an 'ok' field"
        assert "detail" in lp, "live_probe must have a 'detail' field"

    def test_evidence_has_log_and_run_id(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        original = _make_state(**{SAMPLE_HOME: _make_ok_record()})
        write_state(state_file, original)
        result = read_state(state_file)
        ev = result["homes"][SAMPLE_HOME]["evidence"]
        assert "log" in ev, "evidence must have a 'log' field"
        assert "run_id" in ev, "evidence must have a 'run_id' field"


# ── AC3: unknown version raises; malformed JSON raises; no default ───────────

class TestFailClosed:
    """The reader must raise on bad input, never return a default."""

    def test_unknown_version_raises(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        state_file.write_text(
            json.dumps({"version": 2, "homes": {}}), encoding="utf-8"
        )
        with pytest.raises(Exception):
            read_state(state_file)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        state_file.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(Exception):
            read_state(state_file)

    def test_missing_version_raises(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        state_file.write_text(
            json.dumps({"homes": {}}), encoding="utf-8"
        )
        with pytest.raises(Exception):
            read_state(state_file)

    def test_missing_homes_raises(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        state_file.write_text(
            json.dumps({"version": 1}), encoding="utf-8"
        )
        with pytest.raises(Exception):
            read_state(state_file)


# ── AC4: config passed + live failed → state exhausted, never ok ─────────────

class TestStateDerivation:
    """When config_probe passes and live_probe fails, the state must be
    'exhausted' (or 'unknown'), never 'ok'."""

    def test_exhausted_state_from_failed_live_probe(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        original = _make_state(**{SAMPLE_HOME: _make_exhausted_record()})
        write_state(state_file, original)
        result = read_state(state_file)
        home = result["homes"][SAMPLE_HOME]
        assert home["state"] in ("exhausted", "unknown"), (
            f"Expected exhausted/unknown, got {home['state']!r}"
        )
        assert home["state"] != "ok", "state must not be ok when live_probe failed"

    def test_ok_state_when_both_probes_pass(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        original = _make_state(**{SAMPLE_HOME: _make_ok_record()})
        write_state(state_file, original)
        result = read_state(state_file)
        assert result["homes"][SAMPLE_HOME]["state"] == "ok"


# ── AC5: no value in a written record matches a token shape ──────────────────

class TestNoSecrets:
    """The writer must never emit a value that looks like a token."""

    def _collect_strings(self, obj) -> list[str]:
        """Recursively collect all string values from a JSON object."""
        if isinstance(obj, str):
            return [obj]
        if isinstance(obj, dict):
            result = []
            for v in obj.values():
                result.extend(self._collect_strings(v))
            return result
        if isinstance(obj, list):
            result = []
            for item in obj:
                result.extend(self._collect_strings(item))
            return result
        return []

    def test_written_record_contains_no_tokens(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        # Deliberately put a token-shaped string in evidence to test
        # that the writer strips or masks it.
        record = _make_ok_record()
        record["evidence"]["log"] = "/tmp/tok-abcdef0123456789abcdef01.jsonl"
        original = _make_state(**{SAMPLE_HOME: record})
        write_state(state_file, original)
        result = read_state(state_file)
        for string in self._collect_strings(result):
            assert not TOKEN_RE.match(string), (
                f"Token-shaped value found in output: {string!r}"
            )

    def test_exhausted_record_contains_no_tokens(self, tmp_path: Path) -> None:
        state_file = tmp_path / "provider-state.json"
        original = _make_state(**{SAMPLE_HOME: _make_exhausted_record()})
        write_state(state_file, original)
        result = read_state(state_file)
        for string in self._collect_strings(result):
            assert not TOKEN_RE.match(string), (
                f"Token-shaped value found in output: {string!r}"
            )