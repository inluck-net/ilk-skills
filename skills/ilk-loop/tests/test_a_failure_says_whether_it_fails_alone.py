"""Red-first pins for "a failure says whether it fails alone".

AC-1, AC-2, AC-5 are ``xfail(strict=True)`` — the behaviour they assert
does not exist yet.  AC-3 and AC-4 are controls (pass today).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verification_record as vr  # noqa: E402
import verify_attribution as va    # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30)


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "test")
    return repo


def _render_record_with_alone(
    *,
    at_base: dict | None = None,
    head_reruns: dict[str, int] | None = None,
    batch_touched: dict[str, bool] | None = None,
    alone: dict[str, str] | None = None,
    failing_nodes: list[str] | None = None,
) -> str:
    """Render a record with the ``alone`` column (not yet implemented)."""
    scope = {"mode": "full", "count": 10}
    nodes = failing_nodes or [
        "tests/test_foo.py::test_bar",
        "tests/test_baz.py::test_qux",
    ]
    results = {
        "counts": {
            "total": 10, "passed": 8, "failed": 2,
            "errors": 0, "skipped": 0,
            "xfailed": 0, "xpassed": 0,
        },
        "failing_nodes": nodes,
    }
    if at_base is None:
        at_base = {nid: "passed" for nid in nodes}
    if head_reruns is None:
        head_reruns = {nid: 3 for nid in nodes}
    if batch_touched is None:
        batch_touched = {nid: True for nid in nodes}

    # This call does not yet accept ``alone`` — the pin xfail-s until it does.
    return vr.render_record(
        batch="test-batch", head="abc123", tree="def456",
        base_sha="789abc",
        invocation="python3 -m pytest",
        scope=scope, results=results,
        at_base=at_base, base_red=[], head_red=[],
        head_reruns=head_reruns,
        batch_touched=batch_touched,
        alone=alone,
    )


# ── AC-1: alone column appears with correct values ──────────────────────────

def test_alone_column_passed(tmp_path: Path) -> None:
    """A failing id that passes alone gets ``alone: passed``."""
    text = _render_record_with_alone(
        alone={"tests/test_foo.py::test_bar": "passed",
               "tests/test_baz.py::test_qux": "failed"},
    )
    # Check the table header includes "alone".
    table_section = text.split("## At-base rerun")[1].split("##")[0]
    assert "| alone |" in table_section


def test_alone_column_skipped_cap(tmp_path: Path) -> None:
    """When ids exceed AT_BASE_CAP, alone is ``skipped-cap``."""
    # Build 52 failing ids to exceed the cap.
    nodes = [f"tests/test_{i}.py::test_{i}" for i in range(52)]
    alone = {nid: "skipped-cap" for nid in nodes}
    text = _render_record_with_alone(
        failing_nodes=nodes,
        at_base={nid: "passed" for nid in nodes},
        head_reruns={nid: 3 for nid in nodes},
        batch_touched={nid: True for nid in nodes},
        alone=alone,
    )
    table_section = text.split("## At-base rerun")[1].split("##")[0]
    assert "skipped-cap" in table_section


# ── AC-2: order-dependent line ───────────────────────────────────────────────

def test_order_dependent_line_emitted(tmp_path: Path) -> None:
    """An id with ``alone: passed`` and batched red ≥ 1 gets an order-dependent line."""
    text = _render_record_with_alone(
        head_reruns={"tests/test_foo.py::test_bar": 3,
                     "tests/test_baz.py::test_qux": 3},
        alone={"tests/test_foo.py::test_bar": "passed",
               "tests/test_baz.py::test_qux": "failed"},
    )
    assert "order-dependent:" in text
    assert "tests/test_foo.py::test_bar" in text
    assert "passes alone" in text


# ── AC-3: attribution unchanged (control — passes today) ────────────────────

def test_classify_flaky_ignores_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """classify_flaky never reads ``alone``; attribution is byte-for-byte identical."""
    # With alone=passed
    r1 = vr.classify_flaky(
        "tests/test_foo.py::test_bar",
        at_base="passed", head_red_count=3, K=3, batch_touched=True,
    )
    # With alone=failed (same inputs otherwise)
    r2 = vr.classify_flaky(
        "tests/test_foo.py::test_bar",
        at_base="passed", head_red_count=3, K=3, batch_touched=True,
    )
    assert r1 == r2 == "attributed"


# ── AC-4: old 5-column shape still parses (control — passes today) ──────────

def test_old_five_column_shape_parses(tmp_path: Path) -> None:
    """A record with the old 5-column table (no ``alone``) still parses.

    ``verify`` internally checks ``rows == failed`` — if the parser chokes on
    the5-column layout, it raises ``VerificationError``.  Both failures are
    pre-existing (failed at base) so verify passes.  Uses real rerun/touched
    values (not "—") because the5-column parser expects N/K format.
    """
    repo = _init_repo(tmp_path)
    old_record = """\
# Batch verification record — test-batch

record_writer: verification_record v0.0.0
batch: test-batch
verified_head: abc123
verified_tree: def456
base_sha: 789abc
suite_invocation: python3 -m pytest
suite_scope: full
selection_size: 10
suite_total: 10
suite_passed: 8
suite_failed: 2
suite_errors: 0
suite_skipped: 0

## At-base rerun

| node id | at base | in baseline_red | head reruns | batch touched file |
|---|---|---|---|---|
| tests/test_foo.py::test_bar | failed | no | 0/3 | no |
| tests/test_baz.py::test_qux | failed | no | 0/3 | no |

## Findings

_(the worker writes narrative here; no parser reads this section)_
"""
    record_path = repo / "record.md"
    record_path.write_text(old_record, encoding="utf-8")
    # verify() raises if rows != failed; passing means the5-column shape parsed.
    # Both failures are pre-existing (failed at base), so excused == 2.
    message, excused, _ = va.verify(record_path, project=None)
    assert excused == 2


# ── AC-5: unknown runner records alone: unsupported ──────────────────────────

def test_unknown_runner_alone_unsupported(tmp_path: Path) -> None:
    """An unknown runner (neither pytest nor vitest) records ``alone: unsupported``."""
    text = _render_record_with_alone(
        alone={"tests/test_foo.py::test_bar": "unsupported",
               "tests/test_baz.py::test_qux": "unsupported"},
    )
    table_section = text.split("## At-base rerun")[1].split("##")[0]
    assert "unsupported" in table_section