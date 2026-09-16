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
                           text=True, timeout=30)
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
            cwd=project, capture_output=True, text=True, timeout=30,
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

    # Direct test-file changes.
    test_files = {p for p in changed
                  if "/test" in p.replace("\\", "/")
                  or p.replace("\\", "/").endswith("_test.py")}

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
# See docs/verification-record-design.md.

RECORD_WRITER = "verification_record.py"

_SUMMARY_RE = re.compile(
    r"^=+\s(.*?)\sin\s[\d.]+s.*?=+$", re.MULTILINE)
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")
_NODE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def parse_pytest_output(out: str) -> dict:
    """Extract counts and failing node ids from pytest output.

    Raises ValueError when no summary line is present.  A run whose outcome
    cannot be read has not said the batch is clean — it has said nothing, and
    those are different.  Returning zeros here is the exact substitution this
    module exists to remove.
    """
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
    return {**parse_pytest_output((r.stdout or "") + (r.stderr or "")),
            "exit_code": r.returncode}


AT_BASE_CAP = 50


def run_at_base(project: Path, base_sha: str, node_ids: list[str],
                invocation: str, timeout: int = 600,
                baseline_red: list[dict] | None = None) -> dict:
    """Re-run each failing node id at the batch's base commit.

    This is the step the template describes as a procedure a worker performs.
    Every input is available to a program — the ids come from the suite run,
    the base sha from the master, the runner from config — and "did this node
    id pass at base" is a measurement, not a judgment.  So it is code.

    Returns ``{node_id: "passed" | "failed" | "absent-at-base"}``.
    ``absent-at-base`` means the test did not exist at the base commit, which
    makes a present failure this batch's own damage rather than an exoneration.
    """
    import shutil
    import subprocess
    import tempfile
    if not node_ids:
        return {}

    # A declared baseline_red entry is ALREADY an exoneration — re-measuring it
    # pays a subprocess to rediscover a recorded fact. MEASURED 2026-09-16: 24
    # of 25 at-base rows had verdict "failed", i.e. 24 individual pytest runs
    # confirming known-failing tests.
    #
    # Declared entries are still RECORDED as rows (the table keeps one row per
    # failure), with the verdict taken from the declaration rather than a rerun.
    declared = {n for n in node_ids if _in_baseline_red(n, baseline_red or [])}
    node_ids = [n for n in node_ids if n not in declared]
    verdicts_declared = {n: "failed" for n in declared}
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
            cwd=project, capture_output=True, text=True, timeout=180)
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
                               capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
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
            if r.returncode == 4 or "error: not found:" in blob.lower():
                verdicts[nid] = "absent-at-base"
            elif r.returncode == 0:
                verdicts[nid] = "passed"
            else:
                verdicts[nid] = "failed"
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=project, capture_output=True, text=True)
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


def _in_baseline_red(node_id: str, baseline_red: list[dict]) -> bool:
    """Is this node id covered by a declared entry?

    Substring either way, so a file-level declaration covers its tests:
    `tests/test_x.py` matches `tests/test_x.py::TestA::test_b`.
    """
    for e in baseline_red:
        nid = (e.get("node_id") or "").strip()
        if nid and (nid in node_id or node_id in nid):
            return True
    return False


def render_record(*, batch: str, head: str, tree: str, base_sha: str,
                  invocation: str, scope: dict, results: dict,
                  at_base: dict, baseline_red: list[str]) -> str:
    """Render the complete record.  Measurements only — no verdict column.

    The ``attributed`` column is deliberately absent.  It was the cell that
    carried ``no (fixed)`` on gh-resolve's layer-3 batch, where four failures
    that passed at base were excused by a parenthetical.  With no cell to write
    into, that outcome is not fixed — it is unrepresentable.  The checker
    derives attribution from the two measurements beside each node id.
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
    if not at_base:
        lines += ["_(no failures)_", ""]
    else:
        lines += ["| node id | at base | in baseline_red |",
                  "|---|---|---|"]
        for nid, verdict in at_base.items():
            red = "yes" if _in_baseline_red(nid, baseline_red) else "no"
            lines.append(f"| {nid} | {verdict} | {red} |")
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
        baseline_red = read_baseline_red(project)
        at_base = run_at_base(project, args.base_sha, nodes, invocation,
                              baseline_red=baseline_red)
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: at-base rerun could not run: {exc}", file=sys.stderr)
        print(f"stub record left at {record}", file=sys.stderr)
        return 1

    record.write_text(render_record(
        batch=args.batch or record.stem,
        head=head, tree=tree, base_sha=args.base_sha,
        invocation=invocation, scope=scope, results=results,
        at_base=at_base, baseline_red=baseline_red,
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
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()

    if bool(args.record) == bool(args.batch):
        print(
            "ERROR: pass exactly one of --record or --batch",
            file=sys.stderr,
        )
        return 2

    if args.batch:
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
