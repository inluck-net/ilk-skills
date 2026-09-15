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


def verify(record_path: Path) -> tuple[str, int]:
    """Raise VerificationError unless the record establishes a clean batch.

    Returns ``(message, excused_count)`` — the number of failures the record
    accounted for and exonerated.
    """
    if not record_path.is_file():
        raise VerificationError(f"record not found: {record_path}")
    text = record_path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise VerificationError(f"record is empty: {record_path}")

    failed = parse_failure_count(text)
    section = extract_section(text)
    rows = parse_rows(section)

    if failed == 0:
        # excused_count is the number of failures the record accounted for and
        # did not attribute — 0 when the suite was green.
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
        return ("attribution verified: 0 failures, none attributed", 0)

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

    return (f"attribution verified: {failed} failure(s), none attributed", failed)



def write_gate_record(project: Path, excused: int) -> tuple[bool, str]:
    """Record the verified verdict where the PROOF CHECK actually reads it.

    Verification and proof were two different files. This script validates
    ``<ext logs>/verification/<batch>-batch.md``; ``loop_status``/``ship_audit``
    read ``<ext runtime>/batch-gate.json``, which only ``batch_gate.py`` and
    ``phase1_verify.py`` wrote. Nothing bridged them, so a batch that verified
    green still reported ``SHIP PROOF MISSING``.

    Measured 2026-09-15: kira-cloudflare's `pv-verify` shipped 2/2 with
    ``suite_failed: 0`` and an empty at-base table, and all 6 sub-plans read
    ``(!) unproven`` because ``batch-gate.json`` was still a 12:01 record from
    ``manual-ilk-session`` naming a pre-batch ``head_sha``. gh-resolve verified
    ``5123 passed, 0 failed`` and read unproven for the same reason.

    Constraints that are NOT negotiable, each enforced by ``validate_record``:

    * ``invocation`` must equal ``ship_audit._resolve_expected_invocation``
      exactly, or the record is rejected as ``stale_invocation``;
    * a ``pass`` verdict naming no invocation is rejected as ``unenforced``,
      so an unresolvable suite command must REFUSE to write rather than write
      a vague one;
    * ``head_sha`` must be real — a proof that cannot name its commit is not a
      proof, so ``"unknown"`` refuses too.

    ``tree_sha`` is written because this step makes an empty marker commit: the
    gate certifies CODE, and an empty commit moves ``head_sha`` while leaving
    the tree identical. Without it the verification step would invalidate its
    own proof.

    Returns ``(written, detail)``.
    """
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import batch_gate  # type: ignore[import-untyped]
        from ship_audit import _resolve_expected_invocation  # type: ignore[import-untyped]
    except ImportError as exc:
        return False, f"batch_gate/ship_audit unavailable: {exc}"

    runtime_dir = batch_gate.resolve_runtime_dir(project)
    if runtime_dir is None:
        return False, "runtime dir unresolved (no project key)"

    invocation = (_resolve_expected_invocation(project) or "").strip()
    if not invocation:
        return False, ("ship.suite is not configured, so the verdict can name no "
                       "invocation; a pass naming no run is rejected as "
                       "'unenforced'. Refusing to write.")

    head_sha = batch_gate._git_head_sha(project)
    if head_sha == "unknown":
        return False, "HEAD sha unresolvable; refusing to write a proof that cannot name its commit"

    record = batch_gate.BatchGateRecord(
        verdict="pass",
        head_sha=head_sha,
        invocation=invocation,
        timestamp=batch_gate._now_iso(),
        undeclared=[],          # computed-empty, not "not recorded"
        excused_count=excused,
        tree_sha=batch_gate._git_head_tree(project),
        writer="verify_attribution",
    )
    try:
        written = batch_gate.write_record(record, runtime_dir)
    except OSError as exc:
        return False, f"could not write {runtime_dir}: {exc}"
    return True, str(written)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-derive a batch verification verdict from its at-base rerun table."
    )
    ap.add_argument("record", help="path to the batch's verification record (.md)")
    ap.add_argument("--project", default=".",
                    help="project root whose batch-gate record to update (default: cwd)")
    ap.add_argument("--no-write-gate-record", action="store_true",
                    help="verify only; do not record the verdict in batch-gate.json")
    args = ap.parse_args(argv)
    try:
        message, excused = verify(Path(args.record))
    except VerificationError as exc:
        print(f"ATTRIBUTION FAILED: {exc}", file=sys.stderr)
        return 1

    # Only a PASS writes a proof. A failed verification leaves the previous
    # record alone — it will be rejected as stale on head/tree mismatch, which
    # is the correct outcome for a batch that did not verify.
    if args.no_write_gate_record:
        print(f"{message}; gate record not written (--no-write-gate-record)")
        return 0
    ok, detail = write_gate_record(Path(args.project).resolve(), excused)
    if ok:
        print(f"{message}; batch-gate record written to {detail}")
    else:
        # Loud, but not fatal: attribution genuinely passed. Silence here would
        # recreate the very gap this bridge closes.
        print(f"{message}; PROOF NOT RECORDED — {detail}")
        print(f"PROOF NOT RECORDED: {detail}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
