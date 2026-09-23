#!/usr/bin/env python3
"""Write machine-readable record fields that the batch-verification runtime depends on.

The batch-verification template's step 0 runs a test suite and writes a
verification record.  Two fields the runtime reads — ``verified_head`` (the
HEAD the suite ran on) and ``suite_scope`` (scoped vs full) — historically
lived only in prose bullets.  A planner that paraphrases prose drops them;
measured on gh-resolve 2026-09-16: both fields survived in 0 of 2 rendered
sub-plans.

This script is invoked **from a gate command** so it cannot be paraphrased
away.  It reads HEAD from git (never accepts it as an argument — a caller
that can pass it can pass the wrong one, which is the class of defect this
batch is about) and writes it to the record file.

Usage::

    python3 verification_record.py --project <dir> --record <path>
    python3 verification_record.py --project . --record /abs/path/to/record.md

Idempotent: re-running on an existing record updates the fields rather than
appending a second copy.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _git(project: Path, *args: str) -> str | None:
    """Run a read-only git command, returning stripped stdout or None."""
    try:
        r = subprocess.run(["git", *args], cwd=project, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def read_head_from_git(project: Path) -> str | None:
    """Return the full 40-char HEAD sha, or None if unresolvable."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project,
            capture_output=True,
            text=True,
                encoding="utf-8", errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    sha = r.stdout.strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        return sha
    return None


_VERIFIED_HEAD_RE = re.compile(
    r"^[-*\s]*\**\s*(?:verified_)?head[^:\n]*:\**[ \t]*`?([0-9a-fA-F]{7,40})`?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def inject_verified_head(text: str, sha: str) -> str:
    """Return *text* with ``verified_head: <sha>`` present (updated or appended).

    Idempotent: if the field already exists, replaces it.  If absent, appends
    after the last non-blank line.
    """
    field_line = f"**verified_head:** `{sha}`"
    if _VERIFIED_HEAD_RE.search(text):
        return _VERIFIED_HEAD_RE.sub(field_line, text)
    # Append after the last non-blank line.
    stripped = text.rstrip("\n")
    if stripped:
        return stripped + "\n\n" + field_line + "\n"
    return field_line + "\n"


_SUITE_SCOPE_RE = re.compile(
    r"^[-*\s]*\**\s*suite_scope[ \t]*:\**[ \t]*`?\w+`?.*$",
    re.MULTILINE | re.IGNORECASE,
)

# Global/config/fixture patterns whose change invalidates any narrow scope.
_GLOBAL_PATTERNS = (
    "conftest.py",
    "fixtures",
    "pytest.ini",
    "setup.cfg",
    "pyproject.toml",
    "tox.ini",
    ".github",
    "Makefile",
)


def _is_global_change(path: str) -> bool:
    """True if *path* touches conftest, fixtures, or build config."""
    norm = path.replace("\\", "/").lower()
    return any(pat in norm for pat in _GLOBAL_PATTERNS)


def _module_test_name(module_stem: str) -> str:
    """Derive the expected test-file name for a Python module stem."""
    if module_stem.startswith("test_"):
        return module_stem + ".py"
    return f"test_{module_stem}.py"


def compute_suite_scope(project: Path, base_sha: str) -> dict:
    """Derive the suite scope from ``base_sha..HEAD``.

    Returns ``{"mode": "scoped"|"full", "count": N, "reason": str}``.

    Widen to ``full`` (AC-2) when:
    - the importer set cannot be computed;
    - the diff touches conftest, fixtures, or build config;
    - no test files are in the changed set and no changed module maps to an
      existing test file.
    """
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}..HEAD"],
            cwd=project, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return {"mode": "full", "count": 0,
                "reason": "git diff failed — importer set uncomputable"}

    if r.returncode != 0:
        return {"mode": "full", "count": 0,
                "reason": f"git diff exited {r.returncode}"}

    changed = [p for p in r.stdout.strip().splitlines() if p.strip()]
    if not changed:
        return {"mode": "scoped", "count": 0, "reason": "empty diff"}

    # Direct test-file changes (Python test files only).
    test_files = {p for p in changed
                  if p.endswith(".py")
                  and ("/test" in p.replace("\\", "/")
                       or p.replace("\\", "/").endswith("_test.py"))}

    # Non-test Python files: try to map to their test counterparts.
    non_test_py = [p for p in changed
                   if p.endswith(".py") and p not in test_files]
    has_global = any(_is_global_change(p) for p in changed)

    if has_global:
        return {"mode": "full", "count": 0,
                "reason": "diff touches conftest/fixtures/build config"}

    # Map each changed module to the test files that cover it.
    #
    # Degrade PER MODULE, never globally. The previous version returned
    # mode=full from inside this loop on the first unmapped module, throwing
    # away every selection already computed. MEASURED 2026-09-16: it returned
    # `count: 0, reason: "no test file found for changed module
    # skills/ilk-loop/scripts/verification_record.py"` and ran 3102 tests —
    # because that module's tests are test_verification_record_emission.py and
    # test_record_is_measured.py, neither of which is test_verification_record.py.
    # One naming mismatch cost the whole selection.
    #
    # Three ways a module maps, cheapest first. A module that matches none of
    # them contributes nothing and is RECORDED; it does not veto the rest.
    mapped: set[str] = set()
    unmapped: list[str] = []
    for py_path in non_test_py:
        stem = Path(py_path).stem
        hits: set[str] = set()

        # 1. exact conventional name, e.g. foo.py -> test_foo.py
        for c in project.rglob(_module_test_name(stem)):
            hits.add(str(c.relative_to(project)))

        # 2. prefixed variants, e.g. foo.py -> test_foo_emission.py
        if not hits:
            for c in project.rglob(f"test_{stem}*.py"):
                hits.add(str(c.relative_to(project)))

        # 3. importers — the actual contract the docstring promises. A test
        #    that imports the module covers it whatever the file is called.
        if not hits:
            for c in project.rglob("test_*.py"):
                try:
                    src = c.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if f"import {stem}" in src or f"from {stem} import" in src:
                    hits.add(str(c.relative_to(project)))

        if hits:
            mapped |= hits
        else:
            unmapped.append(py_path)

    # Only a module with NO coverage at all forces a broad run, and only when
    # nothing else was selected — an unresolved import graph is not an empty one.
    if unmapped and not (test_files | mapped):
        return {"mode": "full", "count": 0,
                "reason": f"no tests found for any changed module "
                          f"({len(unmapped)} unmapped, first: {unmapped[0]})"}

    combined = test_files | mapped
    if not combined:
        # Only non-Python files changed (docs, configs not in _GLOBAL_PATTERNS).
        return {"mode": "scoped", "count": 0, "reason": "no test-eligible changes"}

    reason = f"{len(combined)} test file(s) selected"
    if unmapped:
        # Name them: a scoped run that silently omits a changed module is the
        # narrowing this whole mechanism exists to prevent.
        reason += (f"; {len(unmapped)} changed module(s) have no discoverable "
                   f"tests and are NOT covered by this selection: "
                   f"{', '.join(unmapped[:3])}")
    return {"mode": "scoped", "count": len(combined),
            "reason": reason, "selection": sorted(combined),
            "unmapped": unmapped}


def inject_suite_scope(text: str, mode: str, count: int) -> str:
    """Return *text* with ``suite_scope: <mode> (N files)`` present (idempotent)."""
    field_line = f"**suite_scope:** `{mode}` ({count} file{'s' if count != 1 else ''})"
    if _SUITE_SCOPE_RE.search(text):
        return _SUITE_SCOPE_RE.sub(field_line, text)
    stripped = text.rstrip("\n")
    if stripped:
        return stripped + "\n" + field_line + "\n"
    return field_line + "\n"


def _resolve_project_verification_dir(project: Path) -> Path:
    """Return ``<ext logs>/verification/`` for *project*, raising if unresolvable."""
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from ilk_paths import external_logs_dir, resolve_project_key  # type: ignore[import-untyped]
    except ImportError as exc:
        raise FileNotFoundError(f"ilk_paths unavailable: {exc}")

    key = resolve_project_key(project)
    if not key:
        raise FileNotFoundError(
            f"no ilk project key resolves from {project} — cannot locate verification dir"
        )
    return external_logs_dir(key) / "verification"


# Regexes for fields written by ``inject_verified_head`` and the step-0 gate.
_RESOLVED_TREE_RE = re.compile(
    r"^[-*\s]*\**\s*verified_tree\s*:\**[ \t]*`?([0-9a-fA-F]{7,40})`?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def resolve_previous_batch_tree(project: Path) -> tuple[str, Path]:
    """Find the most recent ``*-batch.md`` and resolve the tree it verified.

    Reads ``verified_tree`` directly when present; falls back to resolving
    ``verified_head`` through git so an older record that only names the head
    still works (the tree it measured is what matters, not the head).

    **A miss lists what is present**, as ``resolve_batch_record`` already does:
    an absence a reader cannot check is how a wrong search space passes for an
    empty one.

    Returns ``(tree_sha, record_path)``.  Raises ``FileNotFoundError`` when no
    records exist or when the chosen record names neither a tree nor a head.
    """
    vdir = _resolve_project_verification_dir(project)
    records = sorted(vdir.glob("*-batch.md"), reverse=True)
    if not records:
        raise FileNotFoundError(
            f"no verification records found in {vdir} — the previous batch's "
            f"tree cannot be resolved.  Measure the baseline from scratch."
        )

    record = records[0]
    text = record.read_text(encoding="utf-8-sig", errors="replace")

    # Prefer verified_tree (machine-form, no git dependency).
    m = _RESOLVED_TREE_RE.search(text)
    if m:
        return m.group(1), record

    # Fall back to verified_head → resolve its tree through git.
    h = _VERIFIED_HEAD_RE.search(text)
    if not h:
        raise FileNotFoundError(
            f"{record.name} names neither verified_tree nor verified_head — "
            f"its tree is unknown and cannot be compared.  Add one of those "
            f"fields in step 0 and re-run."
        )
    head_sha = h.group(1)
    tree_sha = _git(project, "rev-parse", f"{head_sha}^{{tree}}")
    if tree_sha is None:
        raise FileNotFoundError(
            f"{record.name} declares head {head_sha}, which this repo cannot "
            f"resolve — so its tree cannot be compared."
        )
    return tree_sha, record


def resolve_baseline(
    project: Path, *, base_tree: str
) -> tuple[dict | None, Path] | None:
    """Reuse the previous batch's result when its tree matches *base_tree*.

    Returns ``(counts, source_record)`` on a match, or ``None`` when the
    previous record's tree differs or cannot be established (caller must
    measure).  The counts dict is a placeholder until step 2 fills in the
    extraction; for now a tree match returns ``{}`` and a miss returns ``None``.
    """
    try:
        record_tree, record_path = resolve_previous_batch_tree(project)
    except FileNotFoundError:
        return None

    if record_tree == base_tree:
        return {}, record_path
    return None


def resolve_batch_record(project: Path, batch_slug: str) -> Path:
    """Resolve ``<ext logs>/verification/<batch_slug>-batch.md`` for a project.

    Same resolution logic as ``verify_attribution.resolve_batch_record``.
    """
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from ilk_paths import external_logs_dir, resolve_project_key  # type: ignore[import-untyped]
    except ImportError as exc:
        raise FileNotFoundError(f"ilk_paths unavailable: {exc}")

    key = resolve_project_key(project)
    if not key:
        raise FileNotFoundError(
            f"no ilk project key resolves from {project}"
        )
    verification_dir = external_logs_dir(key) / "verification"
    record = verification_dir / f"{batch_slug}-batch.md"
    if record.is_file():
        return record
    if verification_dir.is_dir():
        siblings = sorted(p.name for p in verification_dir.glob("*-batch.md"))
        found = ", ".join(siblings) if siblings else "(0 *-batch.md files)"
        raise FileNotFoundError(
            f"no record for batch {batch_slug!r} at {record}. "
            f"{verification_dir} holds: {found}"
        )
    raise FileNotFoundError(
        f"no record for batch {batch_slug!r}: {verification_dir} does not exist"
    )


# ── the machine-read surface: measure it, do not ask for it ──────────────────
#
# Everything below exists because of one measured fact (2026-09-16): of the
# seven fields ``verify_attribution`` parses out of a record, six were authored
# by a language model following prose and one by a tool.  Five of six stalled
# verification runs across three projects were a mismatch between what the
# worker wrote and what the parser accepts.
#
# The rule this implements: **nothing the gate parses may be authored by
# prose.**  The tool measures and writes; the checker re-derives; the worker
# writes narrative into sections no parser reads.
#
# See docs/runtime/verification-record-design.md.

RECORD_WRITER = "verification_record.py"

_SUMMARY_RE = re.compile(
    r"^=+\s(.*?)\sin\s[\d.]+s.*?=+$", re.MULTILINE)
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")
_NODE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_pytest_output(out: str) -> dict:
    """Extract counts and failing node ids from pytest output.

    Raises ValueError when no summary line is present.  A run whose outcome
    cannot be read has not said the batch is clean — it has said nothing, and
    those are different.  Returning zeros here is the exact substitution this
    module exists to remove.
    """
    # Strip ANSI color escapes BEFORE matching: pytest on some hosts emits
    # color into pipes (measured 2026-09-20 on chad-mbp: the banner line
    # opens with \x1b[32m before the ==== rule), and both _SUMMARY_RE's
    # ^=+ anchor and _NODE_RE's ^FAILED anchor then never match — the tool
    # parsed its own captured suite as "no summary" and left the unmeasured
    # stub on every attempt of the selfmod-merge-visibility batch.
    out = _ANSI_RE.sub("", out)

    m = _SUMMARY_RE.search(out)
    if not m:
        raise ValueError(
            "no pytest summary line found in the suite output; the record "
            "cannot state a failure count it did not measure"
        )
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0,
              "xfailed": 0, "xpassed": 0}
    for n, word in _COUNT_RE.findall(m.group(1)):
        key = "errors" if word.startswith("error") else word
        counts[key] = int(n)
    # dict.fromkeys preserves first-seen order and de-duplicates: a node id
    # reported as both FAILED and ERROR is one failing test, not two.
    nodes = list(dict.fromkeys(_NODE_RE.findall(out)))
    counts["total"] = (counts["passed"] + counts["failed"] + counts["skipped"]
                       + counts["xfailed"] + counts["xpassed"])
    return {"counts": counts, "failing_nodes": nodes}


_VITEST_TESTS_RE = re.compile(r"^\s*Tests\s+(.*?\(\s*\d+\s*\))", re.MULTILINE)
_VITEST_FAIL_RE = re.compile(r"^\s*FAIL\s+(\S+)", re.MULTILINE)
_VITEST_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|failed|skipped|todo|errored)")


def parse_vitest_output(out: str) -> dict:
    """Extract counts and failing files from vitest output.

    Same contract as ``parse_pytest_output``: raise on an unreadable run
    rather than reporting zeros.  The difference the gate depends on:
    ``counts["failed"]`` counts FAIL LINES — failing FILES — not the
    Tests line's per-test failure count, because a file path is the unit
    ``run_at_base`` can pass back to ``vitest run``; ``render_record``
    writes ``suite_failed = failed + errors`` and one table row per
    failing node, so the two must be equal by construction.  The Tests
    line still fills ``passed``/``skipped``/``total`` (per-test, the only
    place vitest states them).
    """
    out = _ANSI_RE.sub("", out)

    m = _VITEST_TESTS_RE.search(out)
    if not m:
        raise ValueError(
            "no vitest summary line found in the suite output; the record "
            "cannot state a failure count it did not measure"
        )
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0,
              "xfailed": 0, "xpassed": 0}
    for n, word in _VITEST_COUNT_RE.findall(m.group(1)):
        if word == "todo":
            counts["skipped"] += int(n)
        elif word == "errored":
            counts["errors"] += int(n)
        else:
            counts[word] = int(n)
    # A detail section repeats `FAIL  <file> > suite > test` per test; the
    # first token is the file either way, and fromkeys de-dupes to files.
    nodes = list(dict.fromkeys(_VITEST_FAIL_RE.findall(out)))
    tests_line_failed = counts["failed"] + counts["errors"]
    if tests_line_failed and not nodes:
        # A reporter shape whose FAIL lines this regex does not know: the
        # run failed but no rerun unit can be named. That is unreadable,
        # not zero — the same rule as the missing summary.
        raise ValueError(
            "vitest reported failing tests but no FAIL file lines parsed; "
            "the record cannot name what it would re-run at base"
        )
    counts["failed"] = len(nodes)
    counts["total"] = counts["passed"] + counts["skipped"] + counts["failed"]
    return {"counts": counts, "failing_nodes": nodes}


def _is_vitest(invocation: str) -> bool:
    return "vitest" in invocation


def _parse_for(invocation: str):
    """Pick the output parser by invocation shape, never by output sniffing.

    Sniffing would guess "pytest" for a vitest run that crashed before its
    summary — the wrong parser's error, hiding the real one.  The invocation
    names the runner; the record carries it in ``suite_invocation``.
    """
    return parse_vitest_output if _is_vitest(invocation) else parse_pytest_output


def run_suite(project: Path, invocation: str, timeout: int,
              selection: list[str] | None = None) -> dict:
    """Run the project's configured suite and return parsed results.

    ``selection`` APPLIES the computed scope. Without it the scope was
    computed, written into the record, and then ignored — the run was always
    the full suite. MEASURED 2026-09-16: `suite_scope: scoped, 14 files` sat in
    a record produced by a 3102-test run. A scope that is recorded but not
    applied is a label, not a saving, and it made the record's own
    `selection_size` a claim about something that never happened.
    """
    import subprocess
    cmd = invocation
    if selection:
        cmd = f"{invocation} {' '.join(selection)}"
    try:
        r = subprocess.run(cmd, shell=True, cwd=project, timeout=timeout,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
    except subprocess.TimeoutExpired:
        raise TimeoutError(
            f"suite exceeded {timeout}s; the record keeps `suite_failed: "
            f"unmeasured` rather than a count nothing measured"
        )
    return {**_parse_for(invocation)((r.stdout or "") + (r.stderr or "")),
            "exit_code": r.returncode}


AT_BASE_CAP = 50

# Judgment call: K = 3.  Basis: keeps the worst case at 3 small pytest
# processes over the failing set only.  Wrong if a 1-in-4 flake routinely
# reads 3/3; then raise K, which is a single module constant.
FLAKY_RERUN_COUNT = 3


def run_at_base(project: Path, base_sha: str, node_ids: list[str],
                invocation: str, timeout: int = 600,
                baseline_red: list[dict] | None = None) -> dict:
    """Re-run each failing node id at the batch's base commit.

    This is the step the template describes as a procedure a worker performs.
    Every input is available to a program — the ids come from the suite run,
    the base sha from the master, the runner from config — and "did this node
    id pass at base" is a measurement, not a judgment.  So it is code.

    Returns ``{node_id: "passed" | "failed" | "absent-at-base" | "declared-at-base"}``.
    ``absent-at-base`` means the test did not exist at the base commit, which
    makes a present failure this batch's own damage rather than an exoneration.
    ``declared-at-base`` means the test was in the base commit's ``baseline_red``
    and is already exonerated — no subprocess is spawned.
    """
    import shutil
    import subprocess
    import tempfile
    if not node_ids:
        return {}

    # A declared baseline_red entry at the BASE commit is ALREADY an exoneration
    # — re-measuring it pays a subprocess to rediscover a recorded fact.
    # MEASURED 2026-09-16: 24 of 25 at-base rows had verdict "failed", i.e. 24
    # individual pytest runs confirming known-failing tests.
    #
    # Declared entries are still RECORDED as rows (the table keeps one row per
    # failure), with the verdict taken from the declaration rather than a rerun.
    #
    # Only entries in the BASE's list are skipped.  Entries added during this
    # batch (present in head_red but not base_red) are MEASURED like any other.
    declared = {n for n in node_ids if _in_baseline_red(n, baseline_red or [])}
    node_ids = [n for n in node_ids if n not in declared]
    verdicts_declared = {n: "declared-at-base" for n in declared}
    if not node_ids:
        return verdicts_declared

    if len(node_ids) > AT_BASE_CAP:
        raise ValueError(
            f"{len(node_ids)} failing node ids exceeds the {AT_BASE_CAP} cap; "
            f"a batch failing this widely needs a human, not an at-base rerun"
        )
    tmp = Path(tempfile.mkdtemp(prefix="ilk-at-base-"))
    wt = tmp / "base-wt"
    verdicts: dict[str, str] = {}
    try:
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), base_sha],
            cwd=project, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180)
        if add.returncode != 0:
            raise RuntimeError(
                f"could not create a worktree at {base_sha}: "
                f"{(add.stderr or '').strip()[:200]}"
            )
        # Strip any -n/--dist xdist flags: one node id per process is slower
        # under xdist, not faster, and the selection is tiny by construction.
        runner = re.sub(r"\s-n\s+\S+|\s--dist\s+\S+", "", invocation)
        for nid in node_ids:
            r = subprocess.run(f"{runner} {nid}", shell=True, cwd=wt,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
            blob = (r.stdout or "") + (r.stderr or "")
            # Distinguish "this test did not exist at base" from "this test
            # exists and its module fails to import". Both produce "no tests
            # ran"; only the first is absent.
            #
            # pytest exits 4 (usage error) and prints `ERROR: not found:` for an
            # unresolvable node id. A collection error in a file that DOES exist
            # exits 2 with an ERRORS section — that is a failure at base, and
            # exonerates the batch.
            #
            # Getting this backwards is costly in one direction only:
            # absent-at-base counts as ATTRIBUTED, so a misread manufactures a
            # regression. Measured 2026-09-16: an earlier version keyed on
            # "no tests ran" and marked all 7 test_meta_paths.py collection
            # errors absent — the file exists at base (`git cat-file -e` proves
            # it), so all 7 were false attributions.
            if _is_vitest(runner):
                # vitest exits nonzero for a failing file AND for a missing
                # one ("no test files found") — exit codes cannot tell
                # absent-at-base from failed, and the difference decides
                # attribution (absent ⇒ the batch's own damage). The
                # worktree AT base is the ground truth: a file that is not
                # there did not exist at base. Node ids are file paths for
                # vitest (see parse_vitest_output), so this is exact.
                if r.returncode == 0:
                    verdicts[nid] = "passed"
                elif not (wt / nid).exists():
                    verdicts[nid] = "absent-at-base"
                else:
                    verdicts[nid] = "failed"
            elif r.returncode == 4 or "error: not found:" in blob.lower():
                verdicts[nid] = "absent-at-base"
            elif r.returncode == 0:
                verdicts[nid] = "passed"
            else:
                verdicts[nid] = "failed"
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=project, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
        shutil.rmtree(tmp, ignore_errors=True)
    verdicts.update(verdicts_declared)
    return verdicts


def read_baseline_red(project: Path) -> list[dict]:
    """The project's declared platform failures, as ``{node_id, reason, as_of}``.

    TWO bugs lived here, and both produced the same silent wrong answer.

    1. **Wrong location.** The list lives at ``ship.baseline_red``, not at the
       top level — `ship_config.py:129` reads `ship.get("baseline_red")`. This
       function read the top level, found nothing, and reported an empty list.
       MEASURED 2026-09-16: ilk-skills has **6** declared entries and every
       record written that day said `in baseline_red: no` for all 35 rows,
       against a list that was never read.

    2. **Wrong shape.** Entries are DICTS with a required ``node_id`` and
       ``reason`` (`ship_config.py:140-157`), not strings. The old substring
       match would have raised on the first real entry; it only survived
       because it was reading an empty list from the wrong place.

    An empty answer from the wrong location is indistinguishable from an empty
    answer from the right one — which is the defect this module exists to
    refuse, committed inside the module itself.
    """
    import json
    cfg = project / ".ilk-launch.json"
    if not cfg.is_file():
        return []
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    ship = data.get("ship")
    raw = (ship or {}).get("baseline_red") if isinstance(ship, dict) else None
    if raw is None:                       # tolerate a top-level list too
        raw = data.get("baseline_red")
    out: list[dict] = []
    for e in (raw or []):
        if isinstance(e, dict) and e.get("node_id"):
            out.append(e)
        elif isinstance(e, str) and e.strip():
            out.append({"node_id": e, "reason": "(legacy string entry)"})
    return out


def read_baseline_red_at(project: Path, sha: str) -> list[dict]:
    """Read ``ship.baseline_red`` from ``.ilk-launch.json`` at a specific commit.

    This is the base-commit version of :func:`read_baseline_red`.  It parses
    ``git show <sha>:.ilk-launch.json`` with the same shape rules.  If the
    file does not exist at *sha*, returns ``[]``.  If it is unreadable (bad
    JSON, encoding error), raises — an unreadable base list is not an empty
    one, and the record must say so.
    """
    import json
    r = subprocess.run(
        ["git", "show", f"{sha}:.ilk-launch.json"],
        cwd=project, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30)
    if r.returncode != 0:
        # File did not exist at that commit — treat as empty.
        return []
    raw_text = r.stdout
    try:
        data = json.loads(raw_text)
    except (ValueError, OSError) as exc:
        raise RuntimeError(
            f".ilk-launch.json at {sha[:12]} is not valid JSON: {exc}"
        ) from exc
    ship = data.get("ship")
    raw = (ship or {}).get("baseline_red") if isinstance(ship, dict) else None
    if raw is None:
        raw = data.get("baseline_red")
    out: list[dict] = []
    for e in (raw or []):
        if isinstance(e, dict) and e.get("node_id"):
            out.append(e)
        elif isinstance(e, str) and e.strip():
            out.append({"node_id": e, "reason": "(legacy string entry)"})
    return out


def run_head_reruns(project: Path, node_ids: list[str], invocation: str,
                    K: int = FLAKY_RERUN_COUNT,
                    timeout: int = 600) -> dict[str, int]:
    """Run failing node ids at HEAD K times and return {node_id: red_count}.

    Each rerun is ONE pytest process over all the given ids, using the suite's
    own invocation and flags (xdist included).  A test run on its own does not
    reproduce suite load, which is how gh-resolve 23d's 3 failures "passed on
    rerun".

    Returns ``{node_id: <number of times it failed>}`` out of K reruns.
    """
    if not node_ids:
        return {}
    runner = re.sub(r"\s-n\s+\S+|\s--dist\s+\S+", "", invocation)
    red_counts: dict[str, int] = {nid: 0 for nid in node_ids}
    for _ in range(K):
        r = subprocess.run(f"{runner} {' '.join(node_ids)}", shell=True,
                           cwd=project, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        blob = (r.stdout or "") + (r.stderr or "")
        # Parse which ids failed in this rerun.
        failed_this_run = set(_NODE_RE.findall(blob))
        for nid in node_ids:
            if nid in failed_this_run:
                red_counts[nid] += 1
    return red_counts


def batch_touched_files(project: Path, base_sha: str,
                        node_ids: list[str]) -> dict[str, bool]:
    """For each node id, check whether the batch touched its test file.

    Uses ``git diff --name-only <base_sha> HEAD`` to get the changed file set,
    then checks whether each node id's file path appears in that set.

    Returns ``{node_id: True/False}``.
    """
    if not node_ids:
        return {}
    changed = _git(project, "diff", "--name-only", base_sha, "HEAD") or ""
    changed_set = set(changed.splitlines())
    result: dict[str, bool] = {}
    for nid in node_ids:
        file_path = nid.split("::")[0]
        result[nid] = file_path in changed_set
    return result


def classify_flaky(node_id: str, at_base: str, head_red_count: int,
                   K: int, batch_touched: bool) -> str | None:
    """Classify a failing node id's flaky status.

    Returns one of:
    - ``"pre-existing"`` — failed or declared-at-base at base; not attributed
    - ``"attributed"`` — red every time (K/K), or intermittent with batch touch
    - ``"flaky-owed"`` — intermittent or did-not-reproduce, batch did NOT touch
    - ``None`` — not a flaky test (passed at base and no reruns needed)
    """
    if at_base in ("failed", "declared-at-base"):
        return "pre-existing"
    # at_base is passed, absent-at-base, or failed-differently.
    if head_red_count == K:
        return "attributed"
    # Intermittent (1..K-1) or did-not-reproduce (0).
    if batch_touched:
        return "attributed"
    return "flaky-owed"


def _in_baseline_red(node_id: str, baseline_red: list[dict]) -> bool:
    """Is this node id covered by a declared entry?

    Exact match, or prefix match for parametrisations:
    ``tests/test_x.py::test_a`` covers ``tests/test_x.py::test_a[1]``
    (``id`` starts with ``node_id + "["``).  File-level and class-level
    entries no longer excuse individual tests — exact matching prevents
    a declaration from excusing tests that did not exist when it was written.
    """
    for e in baseline_red:
        nid = (e.get("node_id") or "").strip()
        if not nid:
            continue
        if node_id == nid:
            return True
        if node_id.startswith(nid + "["):
            return True
    return False


# ── Signature normalisation for "failed-differently" detection ──────────

_TMP_DIR_RE = re.compile(r"/tmp/[^\s]+")
_HEX_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*")


def normalise_signature(text: str) -> str:
    """Normalise a pytest failure signature for stable comparison.

    Replaces volatile substrings so two runs of the same failing test in
    different tmp dirs, with different hex addresses or timestamps, produce
    the same signature.

    Normalisations:
    - Absolute paths under ``/tmp/…`` → ``<tmp>``
    - Hex addresses (``0x…``) → ``<addr>``
    - Timestamps (ISO-like) → ``<t>``
    """
    text = _TMP_DIR_RE.sub("<tmp>", text)
    text = _HEX_ADDR_RE.sub("<addr>", text)
    text = _TIMESTAMP_RE.sub("<t>", text)
    return text


def render_record(*, batch: str, head: str, tree: str, base_sha: str,
                  invocation: str, scope: dict, results: dict,
                  at_base: dict, base_red: list[dict], head_red: list[dict],
                  at_base_error: str | None = None,
                  head_reruns: dict[str, int] | None = None,
                  batch_touched: dict[str, bool] | None = None,
                  flaky_owed: list[str] | None = None) -> str:
    """Render the complete record.  Measurements only — no verdict column.

    The ``attributed`` column is deliberately absent.  It was the cell that
    carried ``no (fixed)`` on gh-resolve's layer-3 batch, where four failures
    that passed at base were excused by a parenthetical.  With no cell to write
    into, that outcome is not fixed — it is unrepresentable.  The checker
    derives attribution from the two measurements beside each node id.

    ``at_base_error`` carries the error message when the at-base phase could
    not complete.  A cap-exceeded stop writes its own named classification
    instead of the generic ``_(suite did not finish)_`` stub, so the checker
    and the operator can distinguish a designed human-escalation from a
    transient timeout.

    ``base_red`` is the baseline_red list from the BASE commit (excuses in
    this batch).  ``head_red`` is the list from HEAD (includes entries added
    during this batch).  The ``in baseline_red`` cell takes three values:
    ``yes`` (in base_red), ``added`` (only in head_red — declared during this
    batch, does NOT excuse), ``no``.
    """
    c = results["counts"]
    lines = [
        f"# Batch verification record — {batch}",
        "",
        f"record_writer: {RECORD_WRITER}",
        f"batch: {batch}",
        f"verified_head: {head}",
        f"verified_tree: {tree}",
        f"base_sha: {base_sha}",
        f"suite_invocation: {invocation}",
        f"suite_scope: {scope['mode']}",
        f"selection_size: {scope['count']}",
        f"suite_total: {c['total']}",
        f"suite_passed: {c['passed']}",
        f"suite_failed: {c['failed'] + c['errors']}",
        f"suite_errors: {c['errors']}",
        f"suite_skipped: {c['skipped']}",
        "",
        "## At-base rerun",
        "",
    ]
    if at_base_error and "exceeds the" in at_base_error and "cap" in at_base_error:
        # Designed human-escalation: name the stop, not the symptom.
        uncovered = c['failed'] + c['errors']
        lines += [
            f"at_base_cap_exceeded: {uncovered} uncovered (>50 cap) — complete "
            f"ship.baseline_red coverage for the pre-existing families and re-run",
            "",
        ]
    elif not at_base:
        lines += ["_(no failures)_", ""]
    else:
        has_reruns = head_reruns is not None and batch_touched is not None
        if has_reruns:
            lines += ["| node id | at base | in baseline_red | head reruns | batch touched file |",
                      "|---|---|---|---|---|"]
        else:
            lines += ["| node id | at base | in baseline_red |",
                      "|---|---|---|"]
        for nid, verdict in at_base.items():
            in_base = _in_baseline_red(nid, base_red)
            in_head = _in_baseline_red(nid, head_red)
            if in_base:
                red = "yes"
            elif in_head:
                red = "added"
            else:
                red = "no"
            if has_reruns and head_reruns is not None and batch_touched is not None:
                rerun_str = f"{head_reruns.get(nid, 0)}/{FLAKY_RERUN_COUNT}"
                touched_str = "yes" if batch_touched.get(nid, False) else "no"
                lines.append(f"| {nid} | {verdict} | {red} | {rerun_str} | {touched_str} |")
            else:
                lines.append(f"| {nid} | {verdict} | {red} |")
        lines.append("")
    if flaky_owed:
        lines += ["## Flaky (owed)", ""]
        for nid in flaky_owed:
            lines.append(f"- {nid}")
        lines.append("")
    lines += [
        "## Findings",
        "",
        "_(the worker writes narrative here; no parser reads this section)_",
        "",
    ]
    return "\n".join(lines)


def _write_measured_record(project: Path, record: Path, args) -> int:
    """Own the whole machine-read surface: measure it, then write it.

    Ordering is deliberate.  The record is written TWICE — a signed stub with
    ``suite_failed: unmeasured`` before the long run, then the real thing after.
    A suite that exceeds its bound is killed with the gate, and without the stub
    the step leaves nothing at all: no record, and a next iteration that fails
    "record missing" for a reason that has nothing to do with the code.  The
    stub is refused by the checker (``unmeasured`` is not a count), which is the
    correct outcome — loudly incomplete beats silently absent.
    """
    if not args.base_sha:
        print("ERROR: --run-suite requires --base-sha", file=sys.stderr)
        return 2

    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        from ship_audit import _resolve_expected_invocation
    except ImportError as exc:
        print(f"ERROR: cannot import ship_audit: {exc}", file=sys.stderr)
        return 1

    invocation = (_resolve_expected_invocation(project) or "").strip()
    if not invocation:
        print("ERROR: ship.suite is not configured; refusing to invent a suite "
              "command. A record naming no invocation cannot be verified.",
              file=sys.stderr)
        return 1

    head = read_head_from_git(project)
    tree = _git(project, "rev-parse", "HEAD^{tree}")
    if not head or not tree:
        print(f"ERROR: cannot read HEAD/tree from git in {project}", file=sys.stderr)
        return 1
    if head.startswith(args.base_sha) or args.base_sha.startswith(head):
        print(f"ERROR: base_sha equals HEAD ({head[:12]}); the comparison would "
              f"be HEAD against itself and could not detect a regression.",
              file=sys.stderr)
        return 1

    scope = compute_suite_scope(project, args.base_sha)
    # --scope full overrides the computed scope.
    if getattr(args, "scope", "auto") == "full":
        scope["mode"] = "full"
        scope["reason"] = "override: --scope full"
    record.parent.mkdir(parents=True, exist_ok=True)

    stub = (f"# Batch verification record — {args.batch or record.stem}\n\n"
            f"record_writer: {RECORD_WRITER}\n"
            f"verified_head: {head}\n"
            f"verified_tree: {tree}\n"
            f"base_sha: {args.base_sha}\n"
            f"suite_invocation: {invocation}\n"
            f"suite_scope: {scope['mode']}\n"
            f"suite_failed: unmeasured\n\n"
            f"## At-base rerun\n\n_(suite did not finish)_\n")
    record.write_text(stub, encoding="utf-8")

    try:
        # Apply the scope, do not merely record it.
        selection = scope.get("selection") if scope.get("mode") == "scoped" else None
        results = run_suite(project, invocation, args.suite_timeout,
                            selection=selection)
    except (TimeoutError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"stub record left at {record}", file=sys.stderr)
        return 1

    nodes = results["failing_nodes"]
    try:
        base_red = read_baseline_red_at(project, args.base_sha)
        head_red = read_baseline_red(project)
        at_base = run_at_base(project, args.base_sha, nodes, invocation,
                              baseline_red=base_red)
    except (ValueError, RuntimeError) as exc:
        if "exceeds the" in str(exc) and "cap" in str(exc):
            # Designed human-escalation: write the named stop, not the stub.
            record.write_text(render_record(
                batch=args.batch or record.stem,
                head=head, tree=tree, base_sha=args.base_sha,
                invocation=invocation, scope=scope, results=results,
                at_base={}, base_red=base_red, head_red=head_red,
                at_base_error=str(exc),
            ), encoding="utf-8")
            print(f"ERROR: at-base cap exceeded — named stop written to {record}",
                  file=sys.stderr)
            return 1
        print(f"ERROR: at-base rerun could not run: {exc}", file=sys.stderr)
        print(f"stub record left at {record}", file=sys.stderr)
        return 1

    # Run HEAD reruns for non-declared failing nodes to classify flaky tests.
    non_declared = [nid for nid, v in at_base.items()
                    if v != "declared-at-base"]
    head_reruns: dict[str, int] = {}
    batch_touched: dict[str, bool] = {}
    flaky_owed: list[str] = []
    if non_declared:
        try:
            head_reruns = run_head_reruns(project, non_declared, invocation)
            batch_touched = batch_touched_files(project, args.base_sha,
                                                non_declared)
            for nid in non_declared:
                cls = classify_flaky(
                    nid, at_base.get(nid, "failed"),
                    head_reruns.get(nid, 0), FLAKY_RERUN_COUNT,
                    batch_touched.get(nid, False))
                if cls == "flaky-owed":
                    flaky_owed.append(nid)
        except (TimeoutError, subprocess.SubprocessError) as exc:
            print(f"WARNING: HEAD reruns could not run: {exc}", file=sys.stderr)
            # Continue without flaky classification — the record is still valid.

    record.write_text(render_record(
        batch=args.batch or record.stem,
        head=head, tree=tree, base_sha=args.base_sha,
        invocation=invocation, scope=scope, results=results,
        at_base=at_base, base_red=base_red, head_red=head_red,
        head_reruns=head_reruns or None,
        batch_touched=batch_touched or None,
        flaky_owed=flaky_owed or None,
    ), encoding="utf-8")

    c = results["counts"]
    print(f"recorded: {c['passed']} passed, {c['failed']} failed, "
          f"{c['errors']} errors of {c['total']} · scope={scope['mode']} · "
          f"at-base rows={len(at_base)} → {record}")
    # Step 0 records; step 1 judges.  A red suite is not this command's failure.
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Write verified_head (and soon suite_scope) to a verification record."
    )
    ap.add_argument(
        "--project", default=".", help="project root whose git HEAD to read"
    )
    ap.add_argument(
        "--record", default=None, help="path to the verification record (.md)"
    )
    ap.add_argument(
        "--batch", default=None, metavar="BATCH_SLUG",
        help="resolve the record as <ext logs>/verification/<BATCH_SLUG>-batch.md",
    )
    ap.add_argument(
        "--compute-scope", action="store_true",
        help="compute suite_scope from base_sha..HEAD and inject it",
    )
    ap.add_argument(
        "--base-sha", default=None, metavar="SHA",
        help="batch base commit for scope computation (required with --compute-scope)",
    )
    ap.add_argument(
        "--run-suite", action="store_true",
        help="run the suite, re-run failures at base, and WRITE the whole record",
    )
    ap.add_argument(
        "--suite-timeout", type=int, default=1800, metavar="SEC",
        help="bound on the suite run (default 1800)",
    )
    ap.add_argument(
        "--scope", default="auto", choices=("full", "auto"),
        metavar="MODE",
        help="override suite scope: 'full' forces the whole suite even when "
             "compute_suite_scope returns scoped; 'auto' (default) uses the "
             "computed scope.",
    )
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()

    if bool(args.record) == bool(args.batch):
        print(
            "ERROR: pass exactly one of --record or --batch",
            file=sys.stderr,
        )
        return 2

    if args.batch:
        if args.run_suite:
            # --run-suite creates the record; it doesn't need to exist yet.
            # Construct the path directly to avoid the FileNotFoundError in
            # resolve_batch_record.
            try:
                vdir = _resolve_project_verification_dir(project)
            except FileNotFoundError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            record = vdir / f"{args.batch}-batch.md"
        else:
            try:
                record = resolve_batch_record(project, args.batch)
            except FileNotFoundError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
    else:
        record = Path(args.record)

    sha = read_head_from_git(project)
    if sha is None:
        print(
            f"ERROR: cannot read HEAD from git in {project}",
            file=sys.stderr,
        )
        return 1

    if args.run_suite:
        return _write_measured_record(project, record, args)

    if not record.is_file():
        print(f"ERROR: record not found: {record}", file=sys.stderr)
        return 1

    text = record.read_text(encoding="utf-8-sig", errors="replace")
    updated = inject_verified_head(text, sha)

    if args.compute_scope:
        if not args.base_sha:
            print(
                "ERROR: --compute-scope requires --base-sha",
                file=sys.stderr,
            )
            return 2
        scope = compute_suite_scope(project, args.base_sha)
        updated = inject_suite_scope(updated, scope["mode"], scope["count"])
        print(f"suite_scope: {scope['mode']} ({scope['count']} files) — {scope['reason']}")

    record.write_text(updated, encoding="utf-8")
    print(f"verified_head: {sha} → {record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
