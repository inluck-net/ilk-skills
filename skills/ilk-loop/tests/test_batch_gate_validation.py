"""Red-first tests for batch-gate record validation.

A record that cannot be trusted must say why, in words a reader can act
on.  Five outcomes, five distinct words:

  fresh          — head_sha and invocation both match the project today
  stale_head     — head_sha differs from current HEAD
  stale_invocation — invocation differs from what ship.suite builds
  incomplete     — a required field is missing
  absent         — no record file exists

AC-7: the real production stale record on this host is the fixture for
      stale_head — anonymise nothing; its literal content is the point.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────────

# The real production record, copied byte-for-byte (AC-7).
STALE_RECORD = {
    "verdict": "pass",
    "head_sha": "879f33f105bac3b1d5c5a7c1b43bac71980bca71",
    "invocation": (
        "python3 -m pytest skills/ilk-loop/tests/test_batch_gate.py "
        "-q --timeout=60 --timeout-method=signal"
    ),
    "timestamp": "2026-08-25T16:46:29+08:00",
}

REQUIRED_FIELDS = ("verdict", "head_sha", "invocation", "timestamp")


@pytest.fixture()
def expected_invocation() -> str:
    """The invocation the project's ship.suite currently builds."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                           / "ilk-ship" / "scripts"))
    from ship_config import NotConfigured, load_ship_config
    config = load_ship_config(Path(__file__).resolve().parent.parent.parent.parent)
    if isinstance(config, NotConfigured):
        return ""
    invocation = config.ship["suite"]["command"]
    flags = config.ship["suite"].get("flags", [])
    return invocation if not flags else f"{invocation} {' '.join(flags)}"


@pytest.fixture()
def current_head() -> str:
    """Current HEAD sha."""
    import subprocess
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    out = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()
    return out


@pytest.fixture()
def stale_record_path(tmp_path: Path) -> Path:
    """Write the real stale record as a fixture file."""
    p = tmp_path / "batch-gate.json"
    p.write_text(json.dumps(STALE_RECORD, indent=2) + "\n",
                 encoding="utf-8")
    return p


@pytest.fixture()
def fresh_record_path(tmp_path: Path, current_head: str,
                      expected_invocation: str) -> Path:
    """Write a record that matches the project today."""
    p = tmp_path / "batch-gate.json"
    record = {
        "verdict": "pass",
        "head_sha": current_head,
        "invocation": expected_invocation,
        "timestamp": "2026-08-25T20:00:00+08:00",
    }
    p.write_text(json.dumps(record, indent=2) + "\n",
                 encoding="utf-8")
    return p


# ── AC-1: stale head_sha ────────────────────────────────────────────────────

class TestAC1StaleHead:
    """A record whose head_sha differs from current HEAD is stale_head."""

    def test_stale_head_with_real_fixture(
        self, stale_record_path: Path, current_head: str,
    ) -> None:
        from batch_gate import validate_record
        result = validate_record(stale_record_path, current_head, "")
        assert result == "stale_head"

    def test_stale_head_different_sha(
        self, tmp_path: Path, expected_invocation: str,
    ) -> None:
        from batch_gate import validate_record
        p = tmp_path / "batch-gate.json"
        record = {
            "verdict": "fail",
            "head_sha": "0000000000000000000000000000000000000000",
            "invocation": expected_invocation,
            "timestamp": "2026-08-25T20:00:00+08:00",
        }
        p.write_text(json.dumps(record), encoding="utf-8")
        result = validate_record(p, "a" * 40, expected_invocation)
        assert result == "stale_head"

    def test_stale_head_names_both_shas(self, stale_record_path: Path) -> None:
        """The outcome must carry enough information for a reader to see
        how far behind the record is."""
        from batch_gate import validate_record_detail
        detail = validate_record_detail(
            stale_record_path, "a" * 40, "")
        assert "879f33f" in detail
        assert "aaaaaaa" in detail


# ── AC-2: stale invocation (head matches) ───────────────────────────────────

class TestAC2StaleInvocation:
    """A record whose invocation doesn't match ship.suite is stale_invocation,
    even when head_sha is current."""

    def test_stale_invocation_fresh_head(
        self, tmp_path: Path, current_head: str,
    ) -> None:
        from batch_gate import validate_record
        p = tmp_path / "batch-gate.json"
        record = {
            "verdict": "pass",
            "head_sha": current_head,
            "invocation": "python3 -m pytest wrong_file.py -q",
            "timestamp": "2026-08-25T20:00:00+08:00",
        }
        p.write_text(json.dumps(record), encoding="utf-8")
        expected = "python3 -m pytest --timeout=60 --timeout-method=signal"
        result = validate_record(p, current_head, expected)
        assert result == "stale_invocation"

    def test_stale_invocation_is_independent_of_head(
        self, tmp_path: Path,
    ) -> None:
        """Even with a matching sha, wrong invocation is stale_invocation,
        not stale_head."""
        from batch_gate import validate_record
        sha = "b" * 40
        p = tmp_path / "batch-gate.json"
        record = {
            "verdict": "pass",
            "head_sha": sha,
            "invocation": "pytest -q",
            "timestamp": "2026-08-25T20:00:00+08:00",
        }
        p.write_text(json.dumps(record), encoding="utf-8")
        result = validate_record(p, sha, "python3 -m pytest --timeout=60")
        assert result == "stale_invocation"

    def test_stale_invocation_names_both(
        self, tmp_path: Path, current_head: str,
    ) -> None:
        """The outcome must carry both invocations so the reader can see
        what changed."""
        from batch_gate import validate_record_detail
        actual_inv = "python3 -m pytest wrong.py -q"
        expected_inv = "python3 -m pytest --timeout=60 --timeout-method=signal"
        p = tmp_path / "batch-gate.json"
        record = {
            "verdict": "pass",
            "head_sha": current_head,
            "invocation": actual_inv,
            "timestamp": "2026-08-25T20:00:00+08:00",
        }
        p.write_text(json.dumps(record), encoding="utf-8")
        detail = validate_record_detail(p, current_head, expected_inv)
        assert "wrong.py" in detail
        assert "--timeout=60" in detail


# ── AC-3: fresh ─────────────────────────────────────────────────────────────

class TestAC3Fresh:
    """A record matching on both head_sha and invocation is fresh."""

    def test_fresh_record(
        self, fresh_record_path: Path, current_head: str,
        expected_invocation: str,
    ) -> None:
        from batch_gate import validate_record
        result = validate_record(
            fresh_record_path, current_head, expected_invocation)
        assert result == "fresh"

    def test_fresh_with_not_configured(self, tmp_path: Path) -> None:
        """NotConfigured + empty invocation = fresh."""
        from batch_gate import validate_record
        sha = "c" * 40
        p = tmp_path / "batch-gate.json"
        record = {
            "verdict": "not_configured",
            "head_sha": sha,
            "invocation": "",
            "timestamp": "2026-08-25T20:00:00+08:00",
        }
        p.write_text(json.dumps(record), encoding="utf-8")
        result = validate_record(p, sha, "")
        assert result == "fresh"


# ── AC-4: incomplete ────────────────────────────────────────────────────────

class TestAC4Incomplete:
    """A record missing any required field is incomplete."""

    @pytest.mark.parametrize("missing", list(REQUIRED_FIELDS))
    def test_missing_field_is_incomplete(
        self, tmp_path: Path, missing: str,
    ) -> None:
        from batch_gate import validate_record
        data = {
            "verdict": "pass",
            "head_sha": "a" * 40,
            "invocation": "pytest -q",
            "timestamp": "2026-08-25T20:00:00+08:00",
        }
        del data[missing]
        p = tmp_path / "batch-gate.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        result = validate_record(p, "a" * 40, "pytest -q")
        assert result == "incomplete"

    def test_incomplete_names_missing_field(
        self, tmp_path: Path,
    ) -> None:
        from batch_gate import validate_record_detail
        data = {
            "verdict": "pass",
            "head_sha": "a" * 40,
            "timestamp": "2026-08-25T20:00:00+08:00",
        }
        p = tmp_path / "batch-gate.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        detail = validate_record_detail(p, "a" * 40, "pytest -q")
        assert "invocation" in detail

    def test_empty_dict_is_incomplete(self, tmp_path: Path) -> None:
        from batch_gate import validate_record
        p = tmp_path / "batch-gate.json"
        p.write_text("{}", encoding="utf-8")
        result = validate_record(p, "a" * 40, "pytest -q")
        assert result == "incomplete"


# ── AC-5: absent ────────────────────────────────────────────────────────────

class TestAC5Absent:
    """A missing record file is absent — distinct from incomplete and fail."""

    def test_no_file_is_absent(self, tmp_path: Path) -> None:
        from batch_gate import validate_record
        result = validate_record(
            tmp_path / "batch-gate.json", "a" * 40, "pytest -q")
        assert result == "absent"

    def test_absent_is_not_incomplete(self, tmp_path: Path) -> None:
        """Absent and incomplete must be different words."""
        from batch_gate import validate_record
        result = validate_record(
            tmp_path / "batch-gate.json", "a" * 40, "pytest -q")
        assert result != "incomplete"

    def test_absent_is_not_fail(self, tmp_path: Path) -> None:
        from batch_gate import validate_record
        result = validate_record(
            tmp_path / "batch-gate.json", "a" * 40, "pytest -q")
        assert result != "fail"
