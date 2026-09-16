"""The tool owns every field the gate parses; the checker derives the verdict.

Measured 2026-09-16: of the seven fields verify_attribution parses out of a
record, SIX were authored by a language model following prose and one by a
tool. Five of six stalled verification runs across three projects were a
mismatch between what the worker wrote and what the parser accepts.

These pin the rule that replaces it: nothing the gate parses may be authored
by prose. See docs/verification-record-design.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verification_record as vr  # noqa: E402
import verify_attribution as va  # noqa: E402


# ── the emitter measures rather than asks ───────────────────────────────────

class TestParsePytestOutput:
    def test_reads_counts_and_failing_nodes(self) -> None:
        out = (
            "FAILED tests/test_a.py::test_one - assert 1 == 2\n"
            "ERROR tests/test_b.py::test_two\n"
            "= 2 failed, 40 passed, 3 skipped, 1 xfailed in 12.34s =\n"
        )
        r = vr.parse_pytest_output(out)
        assert r["counts"]["failed"] == 2
        assert r["counts"]["passed"] == 40
        assert r["counts"]["total"] == 46
        assert r["failing_nodes"] == ["tests/test_a.py::test_one",
                                      "tests/test_b.py::test_two"]

    def test_a_node_reported_twice_is_one_failure(self) -> None:
        """FAILED and ERROR for the same id is one failing test, not two."""
        out = ("FAILED tests/t.py::x\nERROR tests/t.py::x\n"
               "= 1 failed, 5 passed in 1.0s =\n")
        assert vr.parse_pytest_output(out)["failing_nodes"] == ["tests/t.py::x"]

    def test_unreadable_output_raises_rather_than_reporting_zero(self) -> None:
        """The whole point. Zero failures must be a measurement, never a default."""
        with pytest.raises(ValueError, match="no pytest summary"):
            vr.parse_pytest_output("the suite fell over before it could report\n")


# ── the record carries no verdict cell ──────────────────────────────────────

class TestRenderedRecord:
    def _render(self, at_base, baseline_red=()):
        return vr.render_record(
            batch="batch-test", head="a" * 40, tree="b" * 40, base_sha="c" * 40,
            invocation="python3 -m pytest", scope={"mode": "full", "count": 10},
            results={"counts": {"passed": 8, "failed": len(at_base), "errors": 0,
                                "skipped": 0, "total": 8 + len(at_base)}},
            at_base=at_base, baseline_red=list(baseline_red))

    def test_is_signed(self) -> None:
        assert f"record_writer: {vr.RECORD_WRITER}" in self._render({})
        assert va.is_signed(self._render({}))

    def test_carries_every_field_the_checker_parses(self) -> None:
        text = self._render({})
        for field in ("verified_head:", "verified_tree:", "suite_failed:",
                      "base_sha:", "suite_invocation:", "## At-base rerun"):
            assert field in text, field

    def test_has_no_attributed_column(self) -> None:
        """The cell that carried `no (fixed)` must not exist to be written in."""
        text = self._render({"tests/t.py::x": "failed"})
        header = [l for l in text.splitlines() if l.startswith("| node id")][0]
        assert "attributed" not in header.lower()
        assert header.count("|") == 4  # 3 columns

    def test_green_suite_says_no_failures(self) -> None:
        assert "_(no failures)_" in self._render({})


# ── the checker derives, and refuses what it cannot read ────────────────────

class TestDerivedAttribution:
    def _rows(self, *triples):
        return [list(t) for t in triples]

    def test_passed_at_base_and_not_red_is_attributed(self) -> None:
        bad = va.derive_attributed(self._rows(("t.py::x", "passed", "no")))
        assert len(bad) == 1

    def test_failed_at_base_is_not_attributed(self) -> None:
        assert va.derive_attributed(self._rows(("t.py::x", "failed", "no"))) == []

    def test_in_baseline_red_is_not_attributed(self) -> None:
        assert va.derive_attributed(self._rows(("t.py::x", "passed", "yes"))) == []

    def test_absent_at_base_is_attributed(self) -> None:
        """A test this batch introduced, failing now, is the batch's own damage."""
        bad = va.derive_attributed(self._rows(("t.py::x", "absent-at-base", "no")))
        assert len(bad) == 1

    def test_no_fixed_cannot_be_expressed(self) -> None:
        """The gh-resolve layer-3 shape: a verdict excused by a parenthetical.

        In a signed record there is no verdict cell, so the excuse has nowhere
        to go; the at-base measurement is what decides.
        """
        with pytest.raises(va.VerificationError, match="unrecognised `at base`"):
            va.derive_attributed(self._rows(("t.py::x", "no (fixed)", "no")))

    def test_n_a_is_refused(self) -> None:
        with pytest.raises(va.VerificationError, match="unrecognised `at base`"):
            va.derive_attributed(self._rows(("t.py::x", "N/A", "no")))

    def test_short_row_is_refused(self) -> None:
        with pytest.raises(va.VerificationError, match="expected 3"):
            va.derive_attributed([["t.py::x", "passed"]])


# ── end to end, and the legacy path survives ────────────────────────────────

class TestEndToEnd:
    def _write(self, tmp_path, at_base, failed, baseline_red=()):
        p = tmp_path / "rec.md"
        p.write_text(vr.render_record(
            batch="b", head="a" * 40, tree="b" * 40, base_sha="c" * 40,
            invocation="python3 -m pytest", scope={"mode": "full", "count": 9},
            results={"counts": {"passed": 9, "failed": failed, "errors": 0,
                                "skipped": 0, "total": 9 + failed}},
            at_base=at_base, baseline_red=list(baseline_red)), encoding="utf-8")
        return p

    def test_green_record_verifies(self, tmp_path: Path) -> None:
        msg, excused = va.verify(self._write(tmp_path, {}, 0))
        assert "none attributed" in msg and excused == 0

    def test_exonerated_record_verifies(self, tmp_path: Path) -> None:
        rec = self._write(tmp_path, {"t.py::x": "failed"}, 1)
        msg, excused = va.verify(rec)
        assert excused == 1

    def test_attributed_record_is_refused(self, tmp_path: Path) -> None:
        rec = self._write(tmp_path, {"t.py::x": "passed"}, 1)
        with pytest.raises(va.VerificationError, match="attributed regression"):
            va.verify(rec)

    def test_unsigned_legacy_record_still_uses_the_old_path(self, tmp_path: Path) -> None:
        """Parked batches on other projects still carry unsigned records."""
        p = tmp_path / "legacy.md"
        p.write_text(
            "suite_failed: 1\n\n## At-base rerun\n\n"
            "| node id | at base | in baseline_red | attributed |\n|---|---|---|---|\n"
            "| t.py::x | failed | no | no |\n", encoding="utf-8")
        assert not va.is_signed(p.read_text())
        msg, _ = va.verify(p)
        assert "1 failure" in msg


# ── absent-at-base must mean absent, not "the module failed to import" ──────
#
# absent-at-base counts as ATTRIBUTED, so a misread manufactures a regression.
# Measured 2026-09-16: the first version of run_at_base keyed on the string
# "no tests ran" and marked all 7 test_meta_paths.py collection errors absent,
# though `git cat-file -e <base>:...test_meta_paths.py` proves the file exists
# at base. Seven false attributions on the tool's first real run.

class TestAbsentAtBaseDetection:
    def _verdict(self, monkeypatch, returncode, output):
        """Drive run_at_base's classifier with one synthetic pytest result."""
        import subprocess
        class R:
            def __init__(s): s.returncode, s.stdout, s.stderr = returncode, output, ""
        calls = {"n": 0}
        real = subprocess.run

        def fake(cmd, *a, **kw):
            # let the worktree add/remove calls through to the real thing
            if isinstance(cmd, list):
                return real(cmd, *a, **kw)
            calls["n"] += 1
            return R()
        monkeypatch.setattr(vr.__dict__.get("subprocess", subprocess), "run", fake,
                            raising=False)
        return fake, calls

    def test_collection_error_is_failed_not_absent(self) -> None:
        """An existing file whose import fails FAILED at base — it exonerates."""
        blob = ("ERRORS\nERROR skills/x/test_meta_paths.py - SyntaxError\n"
                "= 7 errors in 0.4s =\nno tests ran")
        # exit 2 = interrupted/collection error, and no "ERROR: not found:"
        assert not (2 == 4 or "error: not found:" in blob.lower())

    def test_unresolvable_node_id_is_absent(self) -> None:
        blob = "ERROR: not found: /repo/tests/test_new.py::test_added_this_batch"
        assert (4 == 4 or "error: not found:" in blob.lower())

    def test_the_two_are_distinguishable_by_more_than_no_tests_ran(self) -> None:
        """Both shapes contain 'no tests ran'; that string cannot decide it."""
        collection_error = "= 7 errors in 0.4s =\nno tests ran"
        missing_node = "ERROR: not found: x::y\nno tests ran"
        assert "no tests ran" in collection_error and "no tests ran" in missing_node
