"""Red-first tests: a batch verdict must carry its own attribution.

Measured 2026-09-03 against d7c378e (v0.9.80).  ``/ilk-ship`` Phase 0
refused the batch with one line — ``batch gate recorded: fail``.  The real
cause was one undeclared failure out of 32; the other 31 were declared
``baseline_red``.  The gate computed that 31/1 split exactly
(``batch_gate.py:593-598``), printed it to the launcher log
(``:652-658``), and then dropped it: the persisted record has four fields
(``REQUIRED_FIELDS``, ``:42``) and none of them is the attribution, so
``ship_audit.py:211`` can only say ``f"batch gate recorded: {verdict}"``.

The computation is right; the *transport* drops it.  Five ACs:

AC-1  a gate run with undeclared failures persists ``undeclared`` (node ids)
      and ``excused_count`` on the record.
AC-2  a four-field record written by an older version still loads and
      validates — ``REQUIRED_FIELDS`` unchanged, no KeyError — and the
      optional fields read as "not recorded" (None), never [].
AC-3  attribution round-trips through save -> load unchanged (order included).
AC-4  ship_audit's refusal names each undeclared node id and the excused count.
AC-5  for a record with no attribution, ship_audit says attribution was NOT
      recorded — it must not render as "0 undeclared".

The verdict rule itself is correct and out of scope; see MASTER
2026-09-03b for the diagnosis that first got that backwards.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import ship_audit
from batch_gate import BatchGateRecord, read_record, record_path, run_batch_gate, write_record

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
HELPER = SCRIPTS / "wait_for_background_output.sh"

#: The node id whose undeclared-ness triggered the 2026-09-03 Phase 0 refusal.
REGRESSION_NODE = (
    "skills/ilk-loop/tests/test_vl_describe.py::"
    "TestSmokeGateway::test_hello_image_returns_answer"
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _fake_suite(tmp: Path, failures: list[str], exit_code: int = 1) -> str:
    """A command that prints pytest-shaped FAILED lines and exits non-zero."""
    s = tmp / "fake_suite.sh"
    lines = "\n".join(f'echo "FAILED {f} - AssertionError"' for f in failures)
    s.write_text(f"#!/bin/bash\n{lines}\nexit {exit_code}\n", encoding="utf-8")
    s.chmod(0o755)
    return str(s)


def _gate_project(tmp: Path, baseline_red: list[dict], suite_cmd: str) -> Path:
    """A git repo whose ship block declares *baseline_red* and *suite_cmd*."""
    proj = tmp / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=proj, check=True, capture_output=True)
    (proj / ".ilk-launch.json").write_text(json.dumps({
        "ship": {"suite": {"command": suite_cmd, "timeout": 60},
                 "baseline_red": baseline_red}
    }), encoding="utf-8")
    return proj


def _audit_repo(path: Path) -> str:
    """A git repo whose ship config builds 'python3 -m pytest -q'.

    The ship config is not incidental: the validator compares the record's
    ``invocation`` against what ``ship.suite`` builds, and a project with no
    .ilk-launch.json resolves to "" — every record would then read as
    ``stale_invocation`` and the verdict path under test is unreachable.
    Returns the repo's HEAD sha.
    """
    (path / ".ilk-launch.json").write_text(
        json.dumps({"ship": {"suite": {"command": "python3 -m pytest",
                                       "flags": ["-q"]}}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    (path / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t",
         "add", ".gitkeep"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=path, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path,
        capture_output=True, text=True, check=True).stdout.strip()


def _write_raw_record(runtime_dir: Path, data: dict) -> None:
    """Write a raw JSON dict as batch-gate.json (bypasses validation)."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "batch-gate.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8",
    )


LEGACY_FOUR_FIELD = {
    "verdict": "fail",
    "head_sha": "a" * 40,
    "invocation": "python3 -m pytest -q",
    "timestamp": "2026-09-03T02:00:00+08:00",
}


# ── AC-1: the record carries the attribution the gate computed ──────────────

class TestRecordCarriesAttribution:

    def test_record_carries_undeclared_node_ids(self, tmp_path: Path) -> None:
        """AC-1: 31 excused + 1 undeclared must survive into the record."""
        cmd = _fake_suite(tmp_path, ["tests/test_known.py::test_a",
                                     REGRESSION_NODE])
        proj = _gate_project(
            tmp_path,
            [{"node_id": "tests/test_known.py", "reason": "inherited",
              "as_of": "2026-09-03"}],
            cmd,
        )
        runtime = tmp_path / "rt"

        rec = run_batch_gate(proj, runtime, _wait_helper=HELPER,
                             _poll_timeout=30)

        assert rec is not None and rec.verdict == "fail"
        assert rec.undeclared == [REGRESSION_NODE], (
            "the record must name the undeclared failure the gate computed "
            f"— got {getattr(rec, 'undeclared', '<absent>')!r}"
        )
        assert rec.excused_count == 1, (
            f"excused_count must match the excused total, got "
            f"{getattr(rec, 'excused_count', '<absent>')!r}"
        )
        # Persisted, not just printed: the launcher log is not the transport.
        on_disk = json.loads(record_path(runtime).read_text(encoding="utf-8"))
        assert on_disk["undeclared"] == [REGRESSION_NODE]
        assert on_disk["excused_count"] == 1


# ── AC-2: the back-compat promise ────────────────────────────────────────────

class TestLegacyRecordStillLoads:

    def test_legacy_four_field_record_still_loads(self, tmp_path: Path) -> None:
        """AC-2: REQUIRED_FIELDS unchanged — a four-field record still loads.

        Built by hand with only the four REQUIRED_FIELDS; this guards the
        back-compat promise and must never be deleted.
        """
        runtime = tmp_path / "rt"
        _write_raw_record(runtime, dict(LEGACY_FOUR_FIELD))

        rec = read_record(runtime)

        assert rec is not None, (
            "a record carrying exactly the four REQUIRED_FIELDS must still "
            "validate — adding optional fields must not break older writers"
        )
        assert rec.verdict == "fail"
        assert rec.head_sha == "a" * 40
        # Absence means "not recorded" — never [], which would read as zero.
        assert rec.undeclared is None
        assert rec.excused_count is None


# ── AC-3: attribution survives save -> load ─────────────────────────────────

def test_attribution_round_trips(tmp_path: Path) -> None:
    """AC-3: list order and contents preserved through disk."""
    undeclared = ["tests/b.py::test_two", "tests/a.py::test_one"]
    runtime = tmp_path / "rt"
    write_record(
        BatchGateRecord(
            verdict="fail",
            head_sha="b" * 40,
            invocation="python3 -m pytest -q",
            timestamp="2026-09-03T02:00:00+08:00",
            undeclared=undeclared,
            excused_count=31,
        ),
        runtime,
    )

    back = read_record(runtime)

    assert back is not None
    assert back.undeclared == undeclared, "order and contents must survive"
    assert back.excused_count == 31


# ── AC-4/AC-5: the reader names what the writer stored ──────────────────────

class TestShipAuditReadsAttribution:

    def test_ship_audit_names_the_undeclared(self, tmp_path: Path) -> None:
        """AC-4: the refusal names each undeclared node id and the count."""
        head = _audit_repo(tmp_path)
        runtime = tmp_path / "runtime"
        _write_raw_record(runtime, {
            **LEGACY_FOUR_FIELD,
            "head_sha": head,
            "undeclared": [REGRESSION_NODE],
            "excused_count": 31,
        })

        verdict, reason = ship_audit._resolve_batch_record(runtime, cwd=tmp_path)

        assert verdict == "fail"
        assert reason is not None
        assert REGRESSION_NODE in reason, (
            "the refusal must name the undeclared failure — 'batch gate "
            f"recorded: fail' is the one line this defect exists to replace.  "
            f"Got: {reason!r}"
        )
        assert "31" in reason, (
            f"the refusal must say how many were excused.  Got: {reason!r}"
        )

    def test_absent_attribution_is_not_zero(self, tmp_path: Path) -> None:
        """AC-5: a pre-attribution record says 'not recorded', not '0 undeclared'."""
        head = _audit_repo(tmp_path)
        runtime = tmp_path / "runtime"
        _write_raw_record(runtime, {**LEGACY_FOUR_FIELD, "head_sha": head})

        verdict, reason = ship_audit._resolve_batch_record(runtime, cwd=tmp_path)

        assert verdict == "fail"
        assert reason is not None
        assert "0 undeclared" not in reason, (
            "absence of attribution must never render as a count of zero — "
            f"that reproduces the defect with more fields.  Got: {reason!r}"
        )
        assert "not recorded" in reason, (
            "a record written before the gate recorded attribution must say "
            f"so, not stay silent about the gap.  Got: {reason!r}"
        )
