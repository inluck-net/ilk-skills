"""Tests for verify_attribution.py — the batch-verification step-1 gate.

The template told step 1 to run `python3 <path>/verify_attribution.py` from the
day it was written, and the script never existed. A planner that resolved the
placeholder shipped a gate that exits 127 (kira-cloudflare pv-verify,
2026-09-15); a planner that inlined its own checker survived (gh-resolve). One
coin flip per batch. These pin the script now that it is real.

Two bugs are pinned specifically because both were made while writing checkers
of this kind on 2026-09-15:

* reading the whole row for "YES" also matches the `yes` in `in baseline_red`
  and fails a correctly-exonerated row;
* an unparseable record must refuse, never read as zero failures.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_attribution as va  # noqa: E402


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "record.md"
    p.write_text(body, encoding="utf-8")
    return p


TABLE_HEAD = (
    "| node id | at base | in baseline_red | attributed |\n"
    "|---|---|---|---|\n"
)


# ── the record must exist and say something ─────────────────────────────────

class TestRecordPresence:
    def test_missing_record_fails(self, tmp_path: Path) -> None:
        """Missing is a failure, never a skip."""
        with pytest.raises(va.VerificationError, match="not found"):
            va.verify(tmp_path / "absent.md")

    def test_empty_record_fails(self, tmp_path: Path) -> None:
        with pytest.raises(va.VerificationError, match="empty"):
            va.verify(_write(tmp_path, "   \n\n"))


# ── the failure count: both dialects, and refusal ───────────────────────────

class TestFailureCount:
    def test_suite_failed_form(self) -> None:
        assert va.parse_failure_count("suite_failed: 3\n") == 3

    def test_suite_line_form(self) -> None:
        """gh-resolve writes `Suite: N passed, F failed, S skipped`."""
        assert va.parse_failure_count("Suite: 5123 passed, 2 failed, 2 skipped\n") == 2

    def test_unparseable_refuses_rather_than_reading_zero(self) -> None:
        """The load-bearing negative: no count is not a clean batch.

        Defaulting to 0 would turn every unreadable record into a pass — the
        'empty answer nobody looked for' shape these gates exist to prevent.
        """
        with pytest.raises(va.VerificationError, match="no failure count"):
            va.parse_failure_count("## At-base rerun\n\n_(no failures)_\n")


# ── the at-base section and its rows ────────────────────────────────────────

class TestSectionAndRows:
    def test_missing_section_fails(self, tmp_path: Path) -> None:
        with pytest.raises(va.VerificationError, match="At-base rerun"):
            va.verify(_write(tmp_path, "suite_failed: 0\n\nnothing here\n"))

    def test_green_passes(self, tmp_path: Path) -> None:
        rec = _write(tmp_path, "suite_failed: 0\n\n## At-base rerun\n\n_(no failures)_\n")
        msg, excused = va.verify(rec)
        assert "none attributed" in msg
        assert excused == 0

    def test_rows_must_equal_failures(self, tmp_path: Path) -> None:
        """2 failures explained in prose, 0 rows ⇒ cannot pass.

        This is the batch-2026-09-07 defect: the record argued both failures
        were environmental and the gate read back its own rendered count.
        """
        rec = _write(tmp_path, "suite_failed: 2\n\nAttributed regressions: 0\n\n"
                               "## At-base rerun\n\nBoth were environmental.\n")
        with pytest.raises(va.VerificationError, match="row"):
            va.verify(rec)

    def test_zero_claimed_but_rows_present_fails(self, tmp_path: Path) -> None:
        rec = _write(tmp_path, "suite_failed: 0\n\n## At-base rerun\n\n" + TABLE_HEAD +
                               "| a::t1 | passed | no | YES |\n")
        with pytest.raises(va.VerificationError, match="disagree"):
            va.verify(rec)


# ── the verdict cell ────────────────────────────────────────────────────────

class TestAttributionCell:
    def test_attributed_row_fails(self, tmp_path: Path) -> None:
        rec = _write(tmp_path, "suite_failed: 2\n\n## At-base rerun\n\n" + TABLE_HEAD +
                               "| a::t1 | passed | no | YES |\n"
                               "| b::t2 | failed | no | no |\n")
        with pytest.raises(va.VerificationError, match="attributed regression"):
            va.verify(rec)

    def test_baseline_red_yes_is_not_an_attribution(self, tmp_path: Path) -> None:
        """The bug this pins: `yes` in the baseline_red column is NOT a verdict.

        A substring search for YES across the row fails this record, which is
        correctly exonerated — both failures also fail at base.
        """
        rec = _write(tmp_path, "suite_failed: 2\n\n## At-base rerun\n\n" + TABLE_HEAD +
                               "| a::t1 | failed | no | no |\n"
                               "| b::t2 | failed | yes | no |\n")
        msg, excused = va.verify(rec)
        assert "2 failure(s), none attributed" in msg
        assert excused == 2, "both rows were accounted for and exonerated"

    def test_reads_the_last_cell_not_the_row(self) -> None:
        rows = [["a::t1", "failed", "yes", "no"]]
        assert va.attributed_rows(rows) == []
        rows = [["a::t1", "passed", "no", "YES"]]
        assert len(va.attributed_rows(rows)) == 1


# ── the CLI contract the gate depends on ────────────────────────────────────

class TestCli:
    def test_exit_codes(self, tmp_path: Path, capsys) -> None:
        good = _write(tmp_path, "suite_failed: 0\n\n## At-base rerun\n\n_(no failures)_\n")
        assert va.main([str(good)]) == 0
        bad = tmp_path / "nope.md"
        assert va.main([str(bad)]) == 1


# ── the bridge: a verified batch becomes a PROVEN one ───────────────────────
#
# Verification and proof were different files: this script validates
# logs/verification/<batch>-batch.md, while loop_status/ship_audit read
# runtime/batch-gate.json. Nothing bridged them, so both batches verified green
# on 2026-09-15 and both still read SHIP PROOF MISSING.

class TestGateRecordBridge:
    def _project(self, tmp_path: Path, monkeypatch, suite_cmd="echo hi"):
        import json as _json
        import subprocess
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        proj = tmp_path / "proj"
        proj.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "i"],
                       cwd=proj, check=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"),
                            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        if suite_cmd is not None:
            (proj / ".ilk-launch.json").write_text(
                _json.dumps({"ship": {"suite": {"command": suite_cmd, "flags": []}}}),
                encoding="utf-8")
        return proj

    def test_pass_writes_a_fresh_record(self, tmp_path: Path, monkeypatch) -> None:
        """The whole point: after a clean verify, the proof check sees it."""
        proj = self._project(tmp_path, monkeypatch)
        ok, detail = va.write_gate_record(proj, excused=0)
        assert ok, detail

        sys.path.insert(0, str(SCRIPTS_DIR))
        import batch_gate  # noqa: E402
        from ship_audit import _resolve_expected_invocation  # noqa: E402
        rd = batch_gate.resolve_runtime_dir(proj)
        assert batch_gate.validate_record(
            batch_gate.record_path(rd),
            batch_gate._git_head_sha(proj),
            _resolve_expected_invocation(proj),
            batch_gate._git_head_tree(proj),
        ) == "fresh"

    def test_records_the_expected_invocation_verbatim(self, tmp_path: Path, monkeypatch) -> None:
        """A different string is rejected as stale_invocation, so it must match."""
        proj = self._project(tmp_path, monkeypatch, suite_cmd="pytest -q")
        va.write_gate_record(proj, excused=0)
        sys.path.insert(0, str(SCRIPTS_DIR))
        import batch_gate  # noqa: E402
        from ship_audit import _resolve_expected_invocation  # noqa: E402
        rec = batch_gate.read_record(batch_gate.resolve_runtime_dir(proj))
        assert rec.invocation == _resolve_expected_invocation(proj)
        assert rec.writer == "verify_attribution"
        assert rec.undeclared == []          # computed-empty, not "not recorded"

    def test_refuses_when_suite_unconfigured(self, tmp_path: Path, monkeypatch) -> None:
        """A pass naming no invocation is rejected as 'unenforced' downstream.

        So the bridge must refuse to write rather than emit a vague one.
        """
        proj = self._project(tmp_path, monkeypatch, suite_cmd=None)
        ok, detail = va.write_gate_record(proj, excused=0)
        assert not ok
        assert "ship.suite" in detail

    def test_writes_tree_sha_so_an_empty_marker_does_not_invalidate_it(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """This step makes an empty marker commit; head moves, tree does not."""
        proj = self._project(tmp_path, monkeypatch)
        va.write_gate_record(proj, excused=0)
        sys.path.insert(0, str(SCRIPTS_DIR))
        import batch_gate  # noqa: E402
        rec = batch_gate.read_record(batch_gate.resolve_runtime_dir(proj))
        assert rec.tree_sha, "without tree_sha the marker commit invalidates the proof"
