"""Pin two defects that redispatched gh-resolve 10a every poll with no worker.

Reported by gh-resolve-a1, 2026-10-10 (runs 20261010-083327 and -083830, each
~25 s, stop_reason "timeout"); reproduced on the real record before the fix:
"head_source: ledger entry's failing_nodes (17) do not match the record's
at-base rows (12)".

(a) verify_attribution's head_source check kept only table ids containing
    "::", so a file-level row (tests/test_x.py) dropped out and the step-0
    record could never pass step 1.
(b) suite_ledger return-reds exited 0 ("returned") when nothing was
    attributed, and the runner then skipped the verify worker.

AC-1: a record whose at-base table mixes node ids and file-level ids, cited
      against a ledger entry with the same ids, verifies.
AC-2 (control): a ledger entry with a different id set is still refused.
AC-3: return-reds on a record with no attributed row exits 5, not 0.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import suite_ledger  # noqa: E402
import verify_attribution as va  # noqa: E402

TREE = "c1b739185f84e938aed44fd8c086d25664d7c453"
DIGEST = "0123456789abcdef"
IDS = ["tests/test_a.py::test_one", "tests/test_b.py", "tests/test_c.py::TestX::test_two"]


def _record(ids: list[str]) -> str:
    rows = "\n".join(f"| {i} | passed | no | 0/1 | no |" for i in ids)
    return (
        "# Batch verification record — x\n\n"
        "ledger_mode: require\n"
        f"head_source: ledger {TREE} {DIGEST}\n\n"
        "## At-base rerun\n\n"
        "| node id | at base | in baseline_red | head reruns | batch touched file |\n"
        "|---|---|---|---|---|\n"
        f"{rows}\n\n"
        "## Owners\n\n| node id | slug |\n|---|---|\n| tests/test_z.py::not_a_row | s |\n"
    )


def _entry(ids):
    return {"digest": DIGEST + "ffff", "failing_nodes": list(ids)}


def test_file_level_rows_count(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(suite_ledger, "lookup", lambda project, tree, *a, **k: _entry(IDS))
    va._verify_ledger_citations(_record(IDS), tmp_path)  # no raise


def test_control_a_different_id_set_is_refused(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(suite_ledger, "lookup", lambda project, tree, *a, **k: _entry(IDS[:1]))
    with pytest.raises(va.VerificationError, match="do not match"):
        va._verify_ledger_citations(_record(IDS), tmp_path)


def test_nothing_attributed_is_not_returned(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    rec = tmp_path / "record.md"
    rec.write_text(_record(IDS), encoding="utf-8")
    monkeypatch.setattr(va, "resolve_batch_record", lambda project, batch, *a, **k: rec)
    monkeypatch.setattr(va, "derive_attributed", lambda rows: ([], []))
    rc = suite_ledger.return_reds(tmp_path, batch="b", plans_dir=tmp_path)
    assert rc == 5
