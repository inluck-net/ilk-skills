#!/usr/bin/env python3
"""Re-derive a batch's verification verdict from its record's at-base rerun table.

Step 1 of a batch-verification sub-plan asks one question: *did this batch break
anything?*  The record written by step 0 already contains the answer, as a table
with one row per failing node id.  This script re-derives the verdict from that
table.

**It must never read a rendered conclusion.**  A record commonly contains a line
like ``Attributed regressions: 0``, written by the same agent, in the same
breath, as the failures it was excusing.  A gate that greps for that number is a
self-graded exam.  So the only inputs here are measurements: how many failures
the suite reported, how many rows the table has, and what each row's verdict
cell says.

Three assertions, all of them measurements:

1. The record exists and is non-empty.  **Missing is a failure, never a skip.**
2. It carries an ``## At-base rerun`` section whose table has **exactly one row
   per reported failure**.  This is the load-bearing one: it makes "2 failures,
   explained away in prose, count 0" impossible to pass.
3. **No row is marked attributed.**

Usage::

    python3 verify_attribution.py <record path>

Exit 0 when the batch is verified; non-zero, with the reason on stderr, when it
is not.  Stdlib only.

Two subtleties, both learned by getting them wrong (2026-09-15):

* **Read the row's LAST cell, not the whole row.**  A substring search for
  ``YES`` across the row also matches the ``yes`` in the ``in baseline_red``
  column, and fails a correctly-exonerated row.
* **Never let an unparseable record read as zero failures.**  If neither
  failure-count form is present, that is a refusal, not a pass — an empty answer
  must be unconstructible without actually looking.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECTION_HEADING = "## At-base rerun"

# Two record dialects are in use and both are legitimate:
#   suite_failed: 2                              (kira-cloudflare)
#   Suite: 5123 passed, 0 failed, 2 skipped      (gh-resolve)
# Support both; refuse when neither is present.
_SUITE_FAILED_RE = re.compile(r"^suite_failed:[ \t]*([0-9]+)[ \t]*$", re.MULTILINE)
_SUITE_LINE_RE = re.compile(r"^Suite:.*?\b([0-9]+)[ \t]+failed\b", re.MULTILINE)

# The "no failures" marker step 0 writes for a green suite.
_NO_FAILURES_RE = re.compile(r"_\(\s*no failures\s*\)_", re.IGNORECASE)


class VerificationError(Exception):
    """The record does not establish that the batch is clean."""


def parse_failure_count(text: str) -> int:
    """Return the number of failures the suite reported.

    Raises rather than defaulting to 0.  A record whose failure count cannot be
    read has not told us the batch is clean; it has told us nothing, and those
    are different.
    """
    m = _SUITE_FAILED_RE.search(text)
    if m:
        return int(m.group(1))
    m = _SUITE_LINE_RE.search(text)
    if m:
        return int(m.group(1))
    raise VerificationError(
        "record carries no failure count: expected a `suite_failed: N` line or a "
        "`Suite: <N> passed, <F> failed, ...` line. Without one the verdict "
        "cannot be re-derived, and assuming zero would turn an unreadable record "
        "into a pass."
    )


def extract_section(text: str) -> str:
    """Return the body of the ``## At-base rerun`` section."""
    idx = text.find(SECTION_HEADING)
    if idx == -1:
        raise VerificationError(
            f"record has no `{SECTION_HEADING}` section. That section is the "
            "measurement that exonerates a failure; without it the record is an "
            "argument, not evidence."
        )
    rest = text[idx + len(SECTION_HEADING):]
    nxt = rest.find("\n## ")
    return rest if nxt == -1 else rest[:nxt]


def parse_rows(section: str) -> list[list[str]]:
    """Return the table's data rows as lists of stripped cells.

    Drops the header row and the ``|---|---|`` separator.  A row is any line
    starting with ``|``.
    """
    rows: list[list[str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if set(line) <= set("|- :"):  # separator
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows[1:] if rows else []  # first surviving row is the header


def attributed_rows(rows: list[list[str]]) -> list[list[str]]:
    """Rows whose FINAL cell marks the failure as attributed to this batch.

    The final cell is the ``attributed`` column.  Matching on the whole row
    instead would also hit the ``yes`` in ``in baseline_red`` and fail a row that
    was correctly exonerated.
    """
    return [r for r in rows if r and r[-1].strip().upper() == "YES"]


def verify(record_path: Path) -> str:
    """Raise VerificationError unless the record establishes a clean batch."""
    if not record_path.is_file():
        raise VerificationError(f"record not found: {record_path}")
    text = record_path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise VerificationError(f"record is empty: {record_path}")

    failed = parse_failure_count(text)
    section = extract_section(text)
    rows = parse_rows(section)

    if failed == 0:
        # A green suite may say so with the marker or with an empty table; it may
        # not say so with rows it did not account for.
        if rows:
            raise VerificationError(
                f"record reports 0 failures but the at-base table has {len(rows)} "
                f"row(s). The two disagree, so one of them is wrong."
            )
        if not _NO_FAILURES_RE.search(section) and section.strip():
            # Prose in the section with no rows and no marker is tolerated only
            # when the section is genuinely empty; say so rather than guess.
            pass
        return f"attribution verified: 0 failures, none attributed"

    if len(rows) != failed:
        raise VerificationError(
            f"at-base table has {len(rows)} row(s) but the suite reported "
            f"{failed} failure(s). Exactly one row per failure is required — a "
            f"record that reports failures and explains them in prose cannot "
            f"pass."
        )

    bad = attributed_rows(rows)
    if bad:
        names = ", ".join(r[0] for r in bad)
        raise VerificationError(
            f"{len(bad)} attributed regression(s): {names}. A row marked YES "
            f"passed at the base commit and is not in baseline_red, so this "
            f"batch broke it."
        )

    return f"attribution verified: {failed} failure(s), none attributed"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-derive a batch verification verdict from its at-base rerun table."
    )
    ap.add_argument("record", help="path to the batch's verification record (.md)")
    args = ap.parse_args(argv)
    try:
        print(verify(Path(args.record)))
    except VerificationError as exc:
        print(f"ATTRIBUTION FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
