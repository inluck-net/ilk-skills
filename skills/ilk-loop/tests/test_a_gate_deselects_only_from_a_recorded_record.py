"""Red-first tests: the verify's pytest gate deselects only from a recorded record.

Part of `a-gate-deselects-only-from-a-recorded-record` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 1 removes.

The contract (from the sub-plan):

- ``_load_base_red_ids`` imports and reuses ``resolve_batch_record``,
  ``_read_history``, ``_compute_record_digest``, and ``parse_rows`` from
  ``verify_attribution``.  It returns
  ``([], "[gates] no recorded record for <batch> (<reason>); pytest gate runs
  as authored")`` unless ``_read_history(rec_path)`` is non-empty and the
  latest parsed entry's ``digest`` is a string exactly equal to
  ``_compute_record_digest(text)``.  Reasons: ``no history``, ``digest
  mismatch``.
- The ``record_digest:`` line branch is removed (no record carries it).
- Missing history, an empty file, only malformed JSONL rows, a latest row
  with no/string-invalid digest, and a latest-row mismatch all fail closed
  and run the authored gate.
- A recorder-written record plus its latest matching history entry still
  deselects exactly the same ``failed`` and ``declared-at-base`` ids as
  before.
- A prose edit below ``## Findings`` remains allowed because
  ``_compute_record_digest`` hashes only the surface above that heading.
- A machine-readable edit above ``## Findings`` fails closed.
- No other change to which rows are deselected.
- judgment call: require history for deselection even when the record has no
  ``attempt:`` header because deselection is an optimization that may safely
  fail closed.
"""
from __future__ import annotations

import hashlib
import json
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import run_local_checks  # noqa: E402
import verify_attribution as va  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_record(
    tmp_path: Path,
    *,
    table_rows: str = "",
    findings: str = "",
    extra_above_findings: str = "",
) -> Path:
    """Write a verification record file and return its path."""
    body = textwrap.dedent("""\
        ---
        batch: test-batch
        record_writer: verify_attribution
        ---

        ## At-base rerun

        | Node | Verdict |
        |---|---|
    """)
    body += table_rows
    if extra_above_findings:
        body += extra_above_findings
    if findings:
        body += f"\n## Findings\n\n{findings}\n"
    rec = tmp_path / "record.md"
    rec.write_text(body, encoding="utf-8")
    return rec


def _write_history(record_path: Path, entries: list[dict]) -> None:
    """Write a history JSONL file next to the record."""
    hist = record_path.with_suffix(".history.jsonl")
    lines = [json.dumps(e, separators=(",", ":")) for e in entries]
    hist.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _compute_digest(record_path: Path) -> str:
    """Compute the record digest the same way verify_attribution does."""
    text = record_path.read_text(encoding="utf-8-sig")
    return va._compute_record_digest(text)


def _load_with_stub(tmp_path: Path, batch: str = "test-batch"):
    """Call _load_base_red_ids with resolve_batch_record stubbed.

    Stub returns the record at tmp_path/record.md if it exists.
    """
    rec = tmp_path / "record.md"

    def fake_resolve(project: Path, batch_slug: str) -> Path:
        if rec.is_file():
            return rec
        raise FileNotFoundError(f"no record for {batch_slug}")

    # _load_base_red_ids imports resolve_batch_record from verify_attribution
    # inside the function body, so we must patch the source module.
    import verify_attribution as _va
    original = _va.resolve_batch_record
    _va.resolve_batch_record = fake_resolve
    try:
        return run_local_checks._load_base_red_ids(tmp_path, batch)
    finally:
        _va.resolve_batch_record = original


# ── tests ────────────────────────────────────────────────────────────────────

class TestNoHistoryDeselectsNothing:
    """When there is no history file, deselect nothing and run authored gate."""

    @pytest.mark.xfail(
        strict=True,
        reason="base code has no history check; deselects when record_digest line absent",
    )
    def test_no_history_returns_empty(self, tmp_path: Path) -> None:
        _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n| test_beta | passed |\n",
        )
        ids, diag = _load_with_stub(tmp_path)
        assert ids == []
        assert "no history" in diag or "no recorded record" in diag

    def test_no_history_base_deselects(self, tmp_path: Path) -> None:
        """Control: at base, no history still deselects (the bug)."""
        _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n| test_beta | passed |\n",
        )
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids


class TestDigestMismatchDeselectsNothing:
    """When history's latest digest doesn't match the record, deselect nothing."""

    def _make_mismatched(self, tmp_path: Path) -> Path:
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest[::-1], "attempt": 1}])
        return rec

    @pytest.mark.xfail(
        strict=True,
        reason="base code has no history digest check; deselects when record_digest line absent",
    )
    def test_mismatch_returns_empty(self, tmp_path: Path) -> None:
        self._make_mismatched(tmp_path)
        ids, diag = _load_with_stub(tmp_path)
        assert ids == []
        assert "digest" in diag.lower() or "no recorded record" in diag

    def test_mismatch_base_deselects(self, tmp_path: Path) -> None:
        """Control: at base, digest mismatch still deselects (the bug)."""
        self._make_mismatched(tmp_path)
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids


class TestMatchingDigestDeselects:
    """When history's latest digest matches, deselect failed + declared-at-base."""

    def test_matching_digest_deselects_failed(self, tmp_path: Path) -> None:
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n| test_beta | passed |\n",
        )
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest, "attempt": 1}])
        ids, diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids
        assert "test_beta" not in ids
        assert "deselected" in diag

    def test_matching_digest_deselects_declared_at_base(self, tmp_path: Path) -> None:
        rec = _make_record(
            tmp_path,
            table_rows=(
                "| test_alpha | declared-at-base |\n"
                "| test_beta | passed |\n"
            ),
        )
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest, "attempt": 1}])
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids
        assert "test_beta" not in ids

    def test_matching_digest_skips_other_verdicts(self, tmp_path: Path) -> None:
        rec = _make_record(
            tmp_path,
            table_rows=(
                "| test_a | failed |\n"
                "| test_b | passed |\n"
                "| test_c | absent-at-base |\n"
                "| test_d | declared-at-base |\n"
                "| test_e | unmeasured-at-base |\n"
            ),
        )
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest, "attempt": 1}])
        ids, _diag = _load_with_stub(tmp_path)
        assert set(ids) == {"test_a", "test_d"}


class TestMalformedHistoryRows:
    """History with only malformed rows is treated as no history."""

    @pytest.mark.xfail(
        strict=True,
        reason="base code has no history check; deselects regardless",
    )
    def test_all_malformed_returns_empty(self, tmp_path: Path) -> None:
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        hist = rec.with_suffix(".history.jsonl")
        hist.write_text("not json\n{bad\n\n", encoding="utf-8")
        ids, diag = _load_with_stub(tmp_path)
        assert ids == []
        assert "no history" in diag or "no recorded record" in diag

    def test_all_malformed_base_deselects(self, tmp_path: Path) -> None:
        """Control: at base, malformed history still deselects."""
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        hist = rec.with_suffix(".history.jsonl")
        hist.write_text("not json\n{bad\n\n", encoding="utf-8")
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids


class TestLatestRowMissingDigest:
    """History latest row with no digest field → treated as no history."""

    @pytest.mark.xfail(
        strict=True,
        reason="base code has no history check; deselects regardless",
    )
    def test_no_digest_field_returns_empty(self, tmp_path: Path) -> None:
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        _write_history(rec, [{"attempt": 1}])  # no "digest" key
        ids, diag = _load_with_stub(tmp_path)
        assert ids == []
        assert "no history" in diag or "no recorded record" in diag

    def test_no_digest_field_base_deselects(self, tmp_path: Path) -> None:
        """Control: at base, missing digest field still deselects."""
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        _write_history(rec, [{"attempt": 1}])
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids


class TestLatestRowNonStringDigest:
    """History latest row with non-string digest → treated as no history."""

    @pytest.mark.xfail(
        strict=True,
        reason="base code has no history check; deselects regardless",
    )
    def test_int_digest_returns_empty(self, tmp_path: Path) -> None:
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        _write_history(rec, [{"digest": 42, "attempt": 1}])
        ids, diag = _load_with_stub(tmp_path)
        assert ids == []
        assert "no history" in diag or "no recorded record" in diag

    def test_int_digest_base_deselects(self, tmp_path: Path) -> None:
        """Control: at base, non-string digest still deselects."""
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        _write_history(rec, [{"digest": 42, "attempt": 1}])
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids


class TestNoAttemptHeaderRequiresHistory:
    """Records without attempt: header still require history for deselection.

    judgment call: require history for deselection even when the record has no
    ``attempt:`` header because deselection is an optimization that may safely
    fail closed.
    """

    @pytest.mark.xfail(
        strict=True,
        reason="base code has no history requirement; deselects when record_digest line absent",
    )
    def test_no_attempt_no_history_returns_empty(self, tmp_path: Path) -> None:
        """No attempt header + no history → fail closed."""
        _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        ids, diag = _load_with_stub(tmp_path)
        assert ids == []

    def test_no_attempt_no_history_base_deselects(self, tmp_path: Path) -> None:
        """Control: at base, no attempt + no history still deselects."""
        _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids

    def test_no_attempt_with_matching_history_deselects(self, tmp_path: Path) -> None:
        """No attempt header + matching history → deselects as before."""
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n| test_beta | passed |\n",
        )
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest, "attempt": 1}])
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids
        assert "test_beta" not in ids


class TestProseEditBelowFindingsAllowed:
    """A prose edit below ## Findings doesn't affect digest match."""

    def test_findings_edit_keeps_digest(self, tmp_path: Path) -> None:
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
            findings="Some worker narrative here.",
        )
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest, "attempt": 1}])
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids


class TestMachineEditAboveFindingsFailsClosed:
    """A machine-readable edit above ## Findings causes digest mismatch."""

    @pytest.mark.xfail(
        strict=True,
        reason="base code checks record_digest line, not history; edit above Findings is undetected",
    )
    def test_extra_field_causes_mismatch(self, tmp_path: Path) -> None:
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest, "attempt": 1}])
        # Now edit the record above ## Findings (add a field).
        text = rec.read_text(encoding="utf-8")
        text = text.replace("record_writer: verify_attribution",
                            "record_writer: verify_attribution\nextra_field: oops")
        rec.write_text(text, encoding="utf-8")
        ids, diag = _load_with_stub(tmp_path)
        assert ids == []
        assert "digest" in diag.lower() or "no recorded record" in diag

    def test_extra_field_base_deselects(self, tmp_path: Path) -> None:
        """Control: at base, edit above Findings still deselects (the bug)."""
        rec = _make_record(
            tmp_path,
            table_rows="| test_alpha | failed |\n",
        )
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest, "attempt": 1}])
        text = rec.read_text(encoding="utf-8")
        text = text.replace("record_writer: verify_attribution",
                            "record_writer: verify_attribution\nextra_field: oops")
        rec.write_text(text, encoding="utf-8")
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids


class TestRecordDigestLineRemoved:
    """The record_digest: line branch is removed.

    After step 1, the record_digest: line is ignored — only history matters.
    """

    @pytest.mark.xfail(
        strict=True,
        reason="base code checks record_digest line (abc123 != real digest → mismatch → empty)",
    )
    def test_record_digest_line_ignored_when_history_matches(
        self, tmp_path: Path,
    ) -> None:
        """After step 1: even if record_digest: line mismatches, history wins."""
        body = textwrap.dedent("""\
            ---
            batch: test-batch
            record_writer: verify_attribution
            record_digest: abc123

            ## At-base rerun

            | Node | Verdict |
            |---|---|
            | test_alpha | failed |
        """)
        rec = tmp_path / "record.md"
        rec.write_text(body, encoding="utf-8")
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest, "attempt": 1}])
        ids, _diag = _load_with_stub(tmp_path)
        assert "test_alpha" in ids


class TestEmptyRecordFile:
    """An empty record file → no at-base section → deselect nothing."""

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        rec = tmp_path / "record.md"
        rec.write_text("", encoding="utf-8")
        ids, diag = _load_with_stub(tmp_path)
        assert ids == []


class TestMissingRecordFile:
    """No record file at all → deselect nothing."""

    def test_missing_record_returns_empty(self, tmp_path: Path) -> None:
        ids, diag = _load_with_stub(tmp_path)
        assert ids == []


class TestEmptyAtBaseTable:
    """Record with at-base section but no data rows → deselect nothing."""

    def test_empty_table_returns_empty(self, tmp_path: Path) -> None:
        rec = _make_record(tmp_path, table_rows="")
        digest = _compute_digest(rec)
        _write_history(rec, [{"digest": digest, "attempt": 1}])
        ids, _diag = _load_with_stub(tmp_path)
        assert ids == []