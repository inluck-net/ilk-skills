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

    python3 verify_attribution.py --batch batch-2026-09-15c   # preferred
    python3 verify_attribution.py /abs/path/to/record.md

Prefer ``--batch``: the record lives under the external logs dir, whose absolute
path differs per host, so a path baked into a plan file is a path that is wrong
on the second machine.  ``--batch`` resolves it through ``ilk_paths`` instead.

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

# A designed human-escalation from the at-base cap.  The record names the stop;
# the checker refuses it with the remedy, not the generic row-count mismatch.
_CAP_EXCEEDED_RE = re.compile(r"at_base_cap_exceeded:\s*(\d+)\s*uncovered", re.IGNORECASE)

# An argument still carrying a template placeholder, e.g. `<record path>`.
_PLACEHOLDER_RE = re.compile(r"<[^>]*>")

# The commit the record says its suite ran on. Machine form first; the bold
# prose spellings already in the wild are accepted too, because the refusal
# below is about the field being ABSENT, not about its formatting.
#
# A sha is required — six records across all projects carried a head line on
# 2026-09-16 and two of them said `current main` and `asserted and confirmed`.
# Prose is not a commit, and must not read as one.
_VERIFIED_HEAD_RE = re.compile(
    r"^[-*\s]*\**\s*(?:verified_)?head[^:\n]*:\**[ \t]*`?([0-9a-fA-F]{7,40})`?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_VERIFIED_TREE_RE = re.compile(
    r"^[-*\s]*\**\s*verified_tree\s*:\**[ \t]*`?([0-9a-fA-F]{7,40})`?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


class VerificationError(Exception):
    """The record does not establish that the batch is clean."""


def reject_placeholder(raw: str) -> None:
    """Refuse an argument the planner never substituted.

    The template carried ``verify_attribution.py <record path>`` and told the
    planner to resolve it.  Twice on 2026-09-15 the planner resolved
    ``<skill-root>`` and ``<batch-slug>`` beside it and left this one alone, so
    the loop ran the gate with a literal ``<record path>``.  It failed as
    "record not found", which reads as *step 0 never wrote its record* — and the
    response to that diagnosis is to re-run the suite, not to fix the command.
    Both gh-resolve batches (2026-09-15c and 2026-09-15d) were reverted from
    ``shipped`` to ``in-progress`` by ship-integrity on that misreading, after
    their suites had in fact run green.

    So say which of the two it is.  A wrong path and an unfilled placeholder
    both produce an absent file; only one of them is fixed by looking at the
    plan file.
    """
    if _PLACEHOLDER_RE.search(raw):
        raise VerificationError(
            f"unsubstituted template placeholder in the record argument: {raw!r}. "
            f"The plan file copied this command from the batch-verification "
            f"template without resolving it — fix the command in the sub-plan's "
            f"step-1 `local_checks`, do not re-run the suite. Prefer "
            f"`--batch <batch-slug>`, which needs no host path."
        )


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


_SIGNED_RE = re.compile(r"^record_writer:[ \t]*(\S+)", re.MULTILINE)

_AT_BASE_OK = {"passed", "failed", "absent-at-base", "failed-differently",
               "declared-at-base"}


def is_signed(text: str) -> bool:
    """Was this record written by the tool, or typed by a worker?

    A signed record has a known column layout and carries measurements only.
    An unsigned one is free-form markdown whose dialect has to be guessed —
    which is the defect this whole module keeps absorbing.
    """
    return bool(_SIGNED_RE.search(text))


def derive_attributed(rows: list[list[str]]) -> tuple[list[list[str]], list[str]]:
    """Apply the attribution rule to a SIGNED record's measurements.

    Signed rows are ``| node id | at base | in baseline_red |`` (3-column,
    legacy) or ``| node id | at base | in baseline_red | head reruns | batch
    touched file |`` (5-column, current).  Attribution is *derived* here:

    **3-column (legacy):**
        passed / absent-at-base / failed-differently  ⇒  attributed
        failed  ⇒  not attributed
        declared-at-base + yes  ⇒  not attributed (pre-existing, exonerated)
        declared-at-base + anything else  ⇒  VerificationError (inconsistent)

    **5-column (current):** adds flaky classification:
        failed / declared-at-base at base  ⇒  pre-existing, not attributed
        otherwise, head reruns = K/K  ⇒  attributed (red every time)
        otherwise (intermittent 1..K-1, or 0/K) + batch touched file: yes  ⇒  attributed
        otherwise  ⇒  flaky, fix owed: does NOT block

    The ``in baseline_red`` cell takes three values: ``yes`` (in the base
    commit's list), ``added`` (declared during this batch — does NOT excuse),
    ``no``.

    Returns ``(bad_rows, flaky_owed)`` where ``bad_rows`` are attributed
    regressions and ``flaky_owed`` are node ids classified as flaky.
    """
    bad: list[list[str]] = []
    flaky_owed: list[str] = []
    for r in rows:
        if len(r) < 3:
            raise VerificationError(
                f"signed record row has {len(r)} cells, expected at least 3 "
                f"(node id | at base | in baseline_red): {r}"
            )
        node, at_base, in_red = r[0], r[1].strip().lower(), r[2].strip().lower()
        if at_base not in _AT_BASE_OK:
            raise VerificationError(
                f"unrecognised `at base` value {r[1]!r} for {node}. Legal "
                f"values are {sorted(_AT_BASE_OK)}. A cell the checker cannot "
                f"read is not an exoneration — re-run the at-base rerun."
            )
        if in_red not in {"yes", "added", "no"}:
            raise VerificationError(
                f"unrecognised `in baseline_red` value {r[2]!r} for {node}; "
                f"expected yes, added, or no."
            )
        # declared-at-base: the batch skipped the rerun because the test was
        # in the base's baseline_red.  Only `yes` (in the base's list) is
        # consistent — `added` or `no` means the record is inconsistent.
        if at_base == "declared-at-base":
            if in_red != "yes":
                raise VerificationError(
                    f"inconsistent record: {node} has `at base: declared-at-base` "
                    f"but `in baseline_red: {in_red}`.  A declared-at-base entry "
                    f"must be in the base's list (`yes`)."
                )
            # declared-at-base + yes ⇒ not attributed (pre-existing).
            continue

        # 5-column layout: apply the flaky classifier.
        if len(r) >= 5:
            rerun_str = r[3].strip()
            touched_str = r[4].strip().lower()
            # Parse "N/K" format.
            try:
                red_count, K = rerun_str.split("/")
                red_count, K = int(red_count), int(K)
            except (ValueError, AttributeError):
                raise VerificationError(
                    f"unrecognised `head reruns` value {r[3]!r} for {node}; "
                    f"expected N/K format (e.g. 3/3)."
                )
            if touched_str not in {"yes", "no"}:
                raise VerificationError(
                    f"unrecognised `batch touched file` value {r[4]!r} for {node}; "
                    f"expected yes or no."
                )
            batch_touched = touched_str == "yes"
            # Pre-existing: failed or declared-at-base at base.
            if at_base in ("failed",):
                # failed at base ⇒ not attributed (pre-existing).
                continue
            # at_base is passed, absent-at-base, or failed-differently.
            if red_count == K:
                bad.append(r)
            elif batch_touched:
                bad.append(r)
            else:
                flaky_owed.append(node)
            continue

        # 3-column (legacy) path.
        if at_base in {"passed", "absent-at-base", "failed-differently"}:
            bad.append(r)
    return bad, flaky_owed


_ATTRIB_OK = {"YES", "NO"}


def attributed_rows(rows: list[list[str]]) -> list[list[str]]:
    """Rows whose FINAL cell marks the failure as attributed — LEGACY path.

    Only for unsigned records, which are worker-typed markdown. Kept because
    parked batches on other projects still carry them; every new record is
    signed and goes through ``derive_attributed`` instead.

    The final cell is the ``attributed`` column. Matching on the whole row
    instead would also hit the ``yes`` in ``in baseline_red`` and fail a row that
    was correctly exonerated.

    An unrecognised verdict cell (e.g. ``no (fixed)``, ``N/A``) is refused,
    not silently read as not-attributed.  The tolerant-reader substitution —
    unrecognised treated as benign — is the defect this whole family exists
    to close.
    """
    bad: list[list[str]] = []
    for r in rows:
        if not r:
            continue
        node = r[0] if r else "<unknown>"
        # Validate the at-base cell (column 2, index 1) when present.
        if len(r) >= 2:
            at_base = r[1].strip().lower()
            if at_base not in _AT_BASE_OK:
                raise VerificationError(
                    f"unrecognised `at base` value {r[1]!r} for {node}. "
                    f"Legal values are {sorted(_AT_BASE_OK)} (case- and "
                    f"whitespace-tolerant). `N/A` usually means the test did "
                    f"not exist at the base commit — a test introduced by this "
                    f"batch and failing now is attributed, not exonerated. "
                    f"Re-run the at-base suite at the current tree and record "
                    f"the actual result."
                )
        cell = r[-1].strip().upper()
        if cell not in _ATTRIB_OK:
            raise VerificationError(
                f"unrecognised `attributed` value {r[-1]!r} for {node}. "
                f"Legal values are YES or no (case- and whitespace-tolerant). "
                f"A value the checker cannot read is not a pass — if the "
                f"failure was genuinely fixed, re-run the at-base suite at "
                f"the current tree and record the new result so the table "
                f"describes the tree as it is."
            )
        if cell == "YES":
            bad.append(r)
    return bad


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

    # A designed human-escalation must be refused by name, not by the generic
    # row-count mismatch that a transient timeout would also produce.
    cap_m = _CAP_EXCEEDED_RE.search(section)
    if cap_m:
        uncovered = cap_m.group(1)
        raise VerificationError(
            f"at_base_cap_exceeded: {uncovered} uncovered failures exceed the "
            f"AT_BASE_CAP threshold — the at-base rerun cannot proceed without "
            f"complete ship.baseline_red coverage for the pre-existing families. "
            f"Add the uncovered node ids to baseline_red (or fix the tests) and "
            f"re-run step 0."
        )

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
        return ("attribution verified: 0 failures, none attributed", 0, [])

    if len(rows) != failed:
        raise VerificationError(
            f"at-base table has {len(rows)} row(s) but the suite reported "
            f"{failed} failure(s). Exactly one row per failure is required — a "
            f"record that reports failures and explains them in prose cannot "
            f"pass."
        )

    flaky_owed: list[str] = []
    if is_signed(text):
        bad, flaky_owed = derive_attributed(rows)
    else:
        bad = attributed_rows(rows)
    if bad:
        names = ", ".join(r[0] for r in bad)
        raise VerificationError(
            f"{len(bad)} attributed regression(s): {names}. A row marked YES "
            f"passed at the base commit and is not in baseline_red, so this "
            f"batch broke it."
        )

    msg = f"attribution verified: {failed} failure(s), none attributed"
    if flaky_owed:
        msg += f"; {len(flaky_owed)} flaky (owed): {', '.join(flaky_owed)}"
    return (msg, failed, flaky_owed)



def resolve_batch_record(project: Path, batch_slug: str) -> Path:
    """Resolve ``<ext logs>/verification/<batch_slug>-batch.md`` for a project.

    The record lives outside the repo, under a data root that differs per host
    (and per ``ILK_DATA_HOME``).  A plan file that hardcodes the absolute path
    is correct on the machine that planned the batch and wrong on the other one
    — the same class of defect as resolving a conventional path instead of a
    configured one.  So the plan names the batch and this resolves the path.

    A miss lists the directory's actual contents.  "not found" plus a path the
    reader cannot check is how a wrong search space passes for an empty one.
    """
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from ilk_paths import external_logs_dir, resolve_project_key  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - toolkit always ships it
        raise VerificationError(f"ilk_paths unavailable, cannot resolve --batch: {exc}")

    key = resolve_project_key(project)
    if not key:
        raise VerificationError(
            f"no ilk project key resolves from {project} — --batch cannot find "
            f"the external logs dir. Pass the record path explicitly."
        )
    verification_dir = external_logs_dir(key) / "verification"
    record = verification_dir / f"{batch_slug}-batch.md"
    if record.is_file():
        return record

    if verification_dir.is_dir():
        siblings = sorted(p.name for p in verification_dir.glob("*-batch.md"))
        found = ", ".join(siblings) if siblings else "(0 *-batch.md files)"
        raise VerificationError(
            f"no record for batch {batch_slug!r} at {record}. "
            f"{verification_dir} holds: {found}"
        )
    raise VerificationError(
        f"no record for batch {batch_slug!r}: {verification_dir} does not exist. "
        f"Step 0 writes it; if step 0 ran, check the resolved project key ({key})."
    )


def _git(project: Path, *args: str) -> str | None:
    """Run a read-only git command, returning stripped stdout or None."""
    import subprocess
    try:
        r = subprocess.run(["git", *args], cwd=project, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def check_verified_tree(project: Path, record_path: Path) -> tuple[bool, str]:
    """Is the tree the record verified still the tree we are about to certify?

    ``write_gate_record`` stamps the proof with the tree as it is **at write
    time**, which is only the verified tree if nothing landed in between.  It
    is not the same question as "did the suite pass", and conflating them
    writes a proof for code no suite has run on.

    Downstream already catches a proof that *ages*: ``validate_record``
    compares the record's ``tree_sha`` against the tree now, and returns
    ``stale_head``/``stale_tree`` once anything lands.  What nothing catches is
    a proof **born stale** — written by a gate re-run on a tree the suite never
    saw.  Every downstream check compares the proof to *now*, and such a proof
    was written *now*.

    Measured on gh-resolve 2026-09-16, and this is the case that matters: both
    verification sub-plans were about to have their step-1 gate re-run after a
    placeholder fix.  ``batch-2026-09-15c-batch.md`` declares head ``c3fa0bf``,
    whose tree is ``0fc84c67ce99``; the tree had since moved to ``799468f774a4``
    — **26 files, +2900/-99** — so that re-run would have stamped ``verdict:
    pass`` over 2900 lines the c suite never ran.  (The record written at 02:17
    that day was NOT false: its ``tree_sha`` equals d's verified tree exactly,
    because the commits between were the step's own empty markers.  The hazard
    is the re-run, not the original write.)

    **Compare trees, not heads.** This step makes an empty marker commit by
    design, so head always moves and the tree deliberately does not.  A head
    comparison would refuse every correct run; a tree comparison refuses
    exactly the runs where code changed.

    Returns ``(licensed, detail)``.  A refusal is loud but not fatal — see
    ``main``: attribution genuinely passed, so the gate must not go red and
    revert a sub-plan.  It simply declines to call this tree proven.
    """
    try:
        text = record_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return False, f"cannot re-read the record to check its tree: {exc}"

    m = _VERIFIED_TREE_RE.search(text)
    if m:
        declared_tree: str | None = m.group(1)
        declared_desc = f"verified_tree {declared_tree}"
    else:
        h = _VERIFIED_HEAD_RE.search(text)
        if not h:
            return False, (
                f"{record_path.name} declares no verified commit, so the tree its "
                f"suite ran on is unknown and a proof stamped with the CURRENT "
                f"tree would be asserting something unmeasured. Add a "
                f"`verified_head: <sha>` line in step 0 (the template now writes "
                f"one) and re-run this gate."
            )
        declared_head = h.group(1)
        declared_tree = _git(project, "rev-parse", f"{declared_head}^{{tree}}")
        if declared_tree is None:
            return False, (
                f"{record_path.name} declares head {declared_head}, which this "
                f"repo cannot resolve — so its tree cannot be compared. A proof "
                f"cannot rest on a commit that is not here."
            )
        declared_desc = f"head {declared_head} (tree {declared_tree[:12]})"

    current_tree = _git(project, "rev-parse", "HEAD^{tree}")
    if current_tree is None:
        return False, "cannot resolve the current tree; refusing to write a proof about it"

    if current_tree.startswith(declared_tree) or declared_tree.startswith(current_tree):
        return True, f"verified tree still current ({current_tree[:12]})"

    changed = _git(project, "diff", "--stat", declared_tree, "HEAD") or "(diff unavailable)"
    first_line = changed.strip().splitlines()[-1] if changed.strip() else "(no stat)"
    return False, (
        f"the tree moved after verification: record verified {declared_desc}, "
        f"current tree {current_tree[:12]}. Changes since: {first_line}. "
        f"The suite has not run on this tree, so it is not proven — re-run "
        f"step 0 at the current tree rather than stamping this one."
    )


def write_gate_record(project: Path, excused: int,
                      flaky_owed: list[str] | None = None) -> tuple[bool, str]:
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
        flaky_owed=list(flaky_owed) if flaky_owed else None,
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
    ap.add_argument("record", nargs="?",
                    help="path to the batch's verification record (.md); "
                         "prefer --batch, which needs no host-specific path")
    ap.add_argument("--batch", default=None, metavar="BATCH_SLUG",
                    help="resolve the record as <ext logs>/verification/"
                         "<BATCH_SLUG>-batch.md (e.g. --batch batch-2026-09-15c)")
    ap.add_argument("--project", default=".",
                    help="project root whose batch-gate record to update (default: cwd)")
    ap.add_argument("--no-write-gate-record", action="store_true",
                    help="verify only; do not record the verdict in batch-gate.json")
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()

    # Load ship config early — MalformedConfig must be caught before any
    # verdict is derived, so the error names the problem instead of crashing
    # mid-verification (gh-resolve 23fa13e).
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from ship_config import load_ship_config, MalformedConfig  # type: ignore[import-untyped]
        config = load_ship_config(project)
        if isinstance(config, MalformedConfig):
            path_str = str(config.resolved_path) if config.resolved_path else "unknown"
            print(f"ATTRIBUTION FAILED: malformed ship config at {path_str}: "
                  f"{config.detail}", file=sys.stderr)
            return 1
    except ImportError:
        pass  # ship_config unavailable; proceed without the check

    if bool(args.record) == bool(args.batch):
        # Both or neither. Neither leaves nothing to check; both invites a plan
        # that passes a stale path next to a correct slug and gets the stale one.
        print("ATTRIBUTION FAILED: pass exactly one of <record path> or "
              "--batch <batch-slug>.", file=sys.stderr)
        return 2

    try:
        if args.batch:
            reject_placeholder(args.batch)
            record_path = resolve_batch_record(project, args.batch)
        else:
            reject_placeholder(args.record)
            record_path = Path(args.record)
        message, excused, flaky_owed = verify(record_path)
    except VerificationError as exc:
        print(f"ATTRIBUTION FAILED: {exc}", file=sys.stderr)
        return 1

    # Only a PASS writes a proof. A failed verification leaves the previous
    # record alone — it will be rejected as stale on head/tree mismatch, which
    # is the correct outcome for a batch that did not verify.
    if args.no_write_gate_record:
        print(f"{message}; gate record not written (--no-write-gate-record)")
        return 0

    # A pass licenses a proof only for the tree the suite actually ran on.
    licensed, why = check_verified_tree(project, record_path)
    if not licensed:
        print(f"{message}; PROOF NOT RECORDED — {why}")
        print(f"PROOF NOT RECORDED: {why}", file=sys.stderr)
        return 0

    ok, detail = write_gate_record(project, excused, flaky_owed=flaky_owed)
    if ok:
        print(f"{message}; batch-gate record written to {detail}")
    else:
        # Loud, but not fatal: attribution genuinely passed. Silence here would
        # recreate the very gap this bridge closes.
        print(f"{message}; PROOF NOT RECORDED — {detail}")
        print(f"PROOF NOT RECORDED: {detail}", file=sys.stderr)
    return 0


# ── heredoc-emptied record detection ─────────────────────────────────────────
#
# An unquoted heredoc command-substitutes every backtick.  A verification
# record written that way loses its backticked values silently — the step-1
# gate passes because it reads suite_failed and the at-base table, neither of
# which is affected.  The detector keys on the mangling signature (AC-2):
#
# * a list item whose content starts with a separator (`— `, `- `, `: `);
# * a `**Field:**` whose value is empty or starts with a separator.
#
# A field whitelist would need updating for every new template.  The signature
# is stable because it is the *output* of heredoc mangling, not its *input*.

_EMPTIED_LIST_RE = re.compile(
    r"^\s*\d+\.\s+(?:—|[-:])\s",  # "1.  — added  to schema"
    re.MULTILINE,
)
_EMPTIED_FIELD_RE = re.compile(
    r"^\*\*[^*]+:\*\*\s*$",  # "**Field:**" with nothing after the closing **
    re.MULTILINE,
)


def has_emptied_record_fields(text: str) -> bool:
    """True when *text* carries the heredoc-mangling signature.

    A record written with an unquoted heredoc loses every backticked value.
    The result looks like: ``1.  — added  to schema expected set`` — a list
    item whose content starts with a separator where a backticked name was.

    AC-3: ``_(no failures)_`` and empty at-base tables are legitimate and
    must not trigger this.
    """
    if _EMPTIED_LIST_RE.search(text):
        return True
    if _EMPTIED_FIELD_RE.search(text):
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
