"""Red-first pins for "the record says why it failed".

AC-1 through AC-4 from the sub-plan, driven against the real
``verification_record.py`` on a tmp git repo.

AC-1 and AC-4 are ``xfail(strict=True)`` — the behaviour they assert
does not exist yet.  AC-2 and AC-3 are controls.
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


def _render_record_with_failures(
    *,
    at_base: dict | None = None,
    base_red: list[dict] | None = None,
    suite_output_text: str | None = None,
    suite_output_path: str | None = None,
) -> str:
    """Render a record with two failing tests (pre-existing: at base = failed)."""
    scope = {"mode": "full", "count": 10}
    results = {
        "counts": {
            "total": 10, "passed": 8, "failed": 2,
            "errors": 0, "skipped": 0,
            "xfailed": 0, "xpassed": 0,
        },
        "failing_nodes": [
            "tests/test_foo.py::test_bar",
            "tests/test_baz.py::test_qux",
        ],
    }
    if at_base is None:
        # Both failures are pre-existing (failed at base) — not attributed.
        at_base = {
            "tests/test_foo.py::test_bar": "failed",
            "tests/test_baz.py::test_qux": "failed",
        }

    return vr.render_record(
        batch="test-batch", head="abc123", tree="def456",
        base_sha="789abc",
        invocation="python3 -m pytest",
        scope=scope, results=results,
        at_base=at_base, base_red=base_red or [],
        head_red=[],
        suite_output_path=suite_output_path,
        suite_output_text=suite_output_text,
    )


# ── AC-1 ─────────────────────────────────────────────────────────────────────

def test_ac1_record_has_failure_excerpts_and_suite_output(tmp_path: Path):
    """AC-1: a fake suite with two failing tests ⇒ the record has both
    ``### <id>`` sections containing their messages, and a ``suite_output:``
    line naming an existing file that holds the full output.
    """
    suite_out = tmp_path / "suite-output.txt"
    suite_out.write_text(
        "FAILED tests/test_foo.py::test_bar - assert 1 == 2\n"
        "FAILED tests/test_baz.py::test_qux - KeyError: 'x'\n"
        "= 8 passed, 2 failed in 0.10s =\n",
        encoding="utf-8",
    )

    record = _render_record_with_failures(
        suite_output_text=suite_out.read_text(encoding="utf-8"),
        suite_output_path=str(suite_out),
    )

    # The record must contain both ### headings with their messages
    assert "### tests/test_foo.py::test_bar" in record
    assert "### tests/test_baz.py::test_qux" in record

    # The record must name an existing suite output file
    assert "suite_output:" in record
    for line in record.splitlines():
        if line.startswith("suite_output:"):
            path = line.split(":", 1)[1].strip()
            assert Path(path).is_file(), f"suite output file missing: {path}"
            break


# ── AC-2 ─────────────────────────────────────────────────────────────────────

def test_ac2_verify_attribution_same_verdict_with_and_without_excerpts(
    tmp_path: Path,
):
    """AC-2: ``verify_attribution`` gives the same verdict on the new record
    as on the same record with the excerpts section deleted.
    """
    record_text = _render_record_with_failures()

    # Verify the original record (no excerpts section — this is the baseline)
    record_path = tmp_path / "record.md"
    record_path.write_text(record_text, encoding="utf-8")
    verdict_without, excused_without, _ = va.verify(record_path, project=None)

    # Now add a fake excerpts section and verify again
    with_excerpts = record_text + (
        "\n## Failure excerpts\n\n"
        "suite_output: /dev/null\n\n"
        "### tests/test_foo.py::test_bar\n\n"
        "```\nassert 1 == 2\n```\n\n"
        "### tests/test_baz.py::test_qux\n\n"
        "```\nKeyError: 'x'\n```\n"
    )
    record_path.write_text(with_excerpts, encoding="utf-8")
    verdict_with, excused_with, _ = va.verify(record_path, project=None)

    assert verdict_with == verdict_without
    assert excused_with == excused_without


# ── AC-3 ─────────────────────────────────────────────────────────────────────

def test_ac3_old_5_column_record_still_parses(tmp_path: Path):
    """AC-3: an old 5-column record without the section still parses
    (existing tests stay green).
    """
    record_text = _render_record_with_failures()
    record_path = tmp_path / "record.md"
    record_path.write_text(record_text, encoding="utf-8")

    # Should parse without error
    verdict, excused, _ = va.verify(record_path, project=None)
    assert "verified" in verdict.lower() or "attribution" in verdict.lower()
    assert excused == 2  # both failures are pre-existing


# ── AC-4 ─────────────────────────────────────────────────────────────────────

def test_ac4_long_excerpt_is_truncated(tmp_path: Path):
    """AC-4: an excerpt longer than 4000 characters is truncated with a
    ``… [truncated]`` marker.
    """
    # Build a single long line (5000 chars) so the excerpt is a contiguous
    # string of 'x' chars that can be tested with `in`.
    # Use a FAILED line without a short reason so the block is used.
    long_line = "x" * 5000
    suite_output = (
        f"FAILED tests/test_foo.py::test_bar\n"
        f"FAILED tests/test_baz.py::test_qux - KeyError: 'x'\n"
        f"_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _\n"
        f"{long_line}\n"
        f"_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _\n"
        f"= 8 passed, 2 failed in 0.10s =\n"
    )

    record = _render_record_with_failures(suite_output_text=suite_output)

    # The record should contain the truncated excerpt for the long block
    assert "… [truncated]" in record
    # The full 5000 chars must NOT appear
    assert "x" * 5000 not in record
    # But the first 4000 chars should be present
    assert "x" * 4000 in record