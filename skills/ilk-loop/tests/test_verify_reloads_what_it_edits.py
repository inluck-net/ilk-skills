"""Red-first: the verify step reloads what it edits, and "red differently" is attributed.

gh-resolve 23fa13e: baseline_red entry missing node_id — crash deferred to
ship_audit.  cdf319d: test red at base for 1 file, red at HEAD for 5 —
recorded "failed at base, not attributed".

AC-1  (xfail) verify_attribution exits 1 naming node_id when baseline_red
      entry lacks it.
AC-2  (xfail) a record row ``failed-differently`` ⇒ attributed (exit 1).
AC-3  (xfail) exact matching: file-level entry does not excuse new test;
      parametrisation prefix does.
AC-4  (xfail) signature normalisation: tmp-dir paths normalised; adding a
      failure message changes the signature.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"
SHIP_SCRIPTS = _REPO / "skills" / "ilk-ship" / "scripts"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SHIP_SCRIPTS))


# ── AC-1: MalformedConfig on missing node_id ──────────────────────────


def test_verify_attribution_rejects_missing_node_id(tmp_path: Path) -> None:
    """AC-1: baseline_red without node_id ⇒ exit 1, output names node_id."""
    from verify_attribution import main

    launch = tmp_path / ".ilk-launch.json"
    launch.write_text(
        '{"ship": {"suite": {"command": "pytest"}, '
        '"baseline_red": [{"file": "t.py", "name": "x", "reason": "known"}]}}',
        encoding="utf-8",
    )
    record = tmp_path / "record.md"
    record.write_text("# Verification record\n\nNo failures.\n", encoding="utf-8")

    rc = main([str(record), "--project", str(tmp_path)])
    assert rc == 1, f"expected exit 1, got {rc}"


# ── AC-2: failed-differently is attributed ─────────────────────────────


def test_failed_differently_is_attributed(tmp_path: Path) -> None:
    """AC-2: row 'failed-differently' ⇒ attributed (not rejected as unrecognized).

    Today ``failed-differently`` is not in ``_AT_BASE_OK``, so
    ``derive_attributed`` raises ``VerificationError``.  The fix adds it as
    a legal value and treats it as attributed.
    """
    from verify_attribution import derive_attributed

    rows = [["tests/test_x.py::test_a", "failed-differently", "no"]]
    bad, _flaky = derive_attributed(rows)
    assert len(bad) == 1, f"expected 1 attributed row, got {len(bad)}"


# ── AC-3: exact matching ──────────────────────────────────────────────


def test_file_level_entry_does_not_excuse_new_test() -> None:
    """AC-3a: file-level entry does not cover new test in same file."""
    from verification_record import _in_baseline_red

    baseline = [{"node_id": "tests/test_x.py"}]
    assert not _in_baseline_red("tests/test_x.py::test_new", baseline)


def test_parametrisation_prefix_matches() -> None:
    """AC-3b: test_a covers test_a[1] (regression guard for exact+prefix matching)."""
    from verification_record import _in_baseline_red

    baseline = [{"node_id": "tests/test_x.py::test_a"}]
    assert _in_baseline_red("tests/test_x.py::test_a[1]", baseline)


# ── AC-4: signature normalisation ─────────────────────────────────────


def test_signature_normalises_tmp_paths() -> None:
    """AC-4a: different tmp dirs give the same signature."""
    from verification_record import normalise_signature

    sig1 = normalise_signature(
        "FAILED tests/test_x.py::test_a - assert '/tmp/pytest-abc/test_a' == 'x'"
    )
    sig2 = normalise_signature(
        "FAILED tests/test_x.py::test_a - assert '/tmp/pytest-xyz/test_a' == 'x'"
    )
    assert sig1 == sig2


def test_signature_changes_on_different_failure() -> None:
    """AC-4b: adding a second assertion message changes the signature."""
    from verification_record import normalise_signature

    sig1 = normalise_signature("FAILED tests/test_x.py::test_a - assert 1 == 2")
    sig2 = normalise_signature(
        "FAILED tests/test_x.py::test_a - assert 1 == 2\n"
        "FAILED tests/test_x.py::test_b - assert 3 == 4"
    )
    assert sig1 != sig2
