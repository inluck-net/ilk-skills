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
import fnmatch
import hashlib
import json
import re
import os
import time
import re
import subprocess
import sys
from pathlib import Path

# Sibling module — bounded subprocess execution with process-group cleanup.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bounded_run import run as _bounded_run  # noqa: E402


# ── Contention measurement ────────────────────────────────────────────────

def _ps_axo() -> str | None:
    """Run ``ps -axo pid,ppid,command`` and return stdout, or None on failure."""
    try:
        r = subprocess.run(
            ["ps", "-axo", "pid,ppid,command"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _own_pids() -> set[int]:
    """Return the set of PIDs in this process's tree (self + ancestors + descendants)."""
    own: set[int] = set()
    my_pid = os.getpid()
    own.add(my_pid)
    # Walk ancestors (stop before PID 1 — that's init, not our tree).
    try:
        r = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(my_pid)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=5,
        )
        if r.returncode == 0:
            ppid = int(r.stdout.strip())
            while ppid > 1:
                own.add(ppid)
                r2 = subprocess.run(
                    ["ps", "-o", "ppid=", "-p", str(ppid)],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=5,
                )
                if r2.returncode != 0:
                    break
                ppid = int(r2.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    # Walk descendants.
    try:
        r = subprocess.run(
            ["pgrep", "-P", str(my_pid)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=5,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                try:
                    own.add(int(line.strip()))
                except ValueError:
                    pass
    except (OSError, subprocess.SubprocessError):
        pass
    return own


def measure_contention() -> dict:
    """Measure current contention: other pytest suites, load, and CPUs.

    Returns ``{other_suites, load1, cpus}``.  A ``ps`` failure records
    ``other_suites: "unmeasured"``, never 0.
    """
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = 0.0
    cpus = os.cpu_count()

    ps_out = _ps_axo()
    if ps_out is None:
        return {"other_suites": "unmeasured", "load1": load1, "cpus": cpus}

    own = _own_pids()
    other = 0
    for line in ps_out.splitlines()[1:]:  # skip header
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[2]
        if " -m pytest" in cmd and pid not in own:
            other += 1
    return {"other_suites": other, "load1": load1, "cpus": cpus}


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


def _test_infra_change(path: str) -> bool:
    """True if *path* is test infrastructure (conftest, pytest config, or tests/_*.py).

    A ``.py`` file whose basename starts with ``_`` and whose parent directory
    is named ``tests`` or ``test`` counts as test infrastructure, including
    ``__init__.py``.  Config files (conftest.py, pytest.ini, etc.) are always
    infrastructure.
    """
    norm = path.replace("\\", "/")
    basename = Path(norm).name
    # Config files that already live in _GLOBAL_PATTERNS are also infra.
    if basename in ("conftest.py", "pytest.ini", "setup.cfg",
                    "pyproject.toml", "tox.ini"):
        return True
    # tests/_*.py or tests/__init__.py
    if basename.endswith(".py") and basename.startswith("_"):
        parent = Path(norm).parent.name
        if parent in ("tests", "test"):
            return True
    return False


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

    # Test infrastructure changes (tests/_*.py, conftest.py, config files)
    # force full scope — a helper breakage can INTERNALERROR every test.
    infra_hits = [p for p in changed if _test_infra_change(p)]
    if infra_hits:
        reason = f"test infrastructure changed: {infra_hits[0]}"
        if len(infra_hits) > 1:
            reason += f" (+{len(infra_hits) - 1} more)"
        return {"mode": "full", "count": 0, "reason": reason}

    has_global = any(_is_global_change(p) for p in changed)

    if has_global:
        first = next(p for p in changed if _is_global_change(p))
        return {"mode": "full", "count": 0,
                "reason": f"diff touches conftest/fixtures/build config: {first}"}

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
    # Candidates are git-TRACKED test files only. MEASURED 2026-10-06: an
    # rglob walked into a git-excluded worktree at .claude/worktrees/ and
    # selected its older test copies (28 -> 53 files); their stale modules
    # broke the real tests and 33 failures were attributed to a clean batch.
    try:
        ls = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.py"],
            cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return {"mode": "full", "count": 0,
                "reason": "git ls-files failed — tracked test set uncomputable"}
    if ls.returncode != 0:
        return {"mode": "full", "count": 0,
                "reason": f"git ls-files exited {ls.returncode}"}
    tracked_tests = sorted(p for p in ls.stdout.split("\0")
                           if Path(p).name.startswith("test_"))

    # Three ways a module maps, cheapest first. A module that matches none of
    # them contributes nothing and is RECORDED; it does not veto the rest.
    mapped: set[str] = set()
    unmapped: list[str] = []
    for py_path in non_test_py:
        stem = Path(py_path).stem
        hits: set[str] = set()

        # 1. exact conventional name, e.g. foo.py -> test_foo.py
        exact = _module_test_name(stem)
        hits.update(p for p in tracked_tests if Path(p).name == exact)

        # 2. prefixed variants, e.g. foo.py -> test_foo_emission.py
        if not hits:
            hits.update(p for p in tracked_tests
                        if fnmatch.fnmatch(Path(p).name, f"test_{stem}*.py"))

        # 3. importers — the actual contract the docstring promises. A test
        #    that imports the module covers it whatever the file is called.
        if not hits:
            try:
                from test_importers import importer_tests  # noqa: E402
                hits.update(importer_tests(project, [py_path]))
            except ImportError:
                pass

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
    r"^=*\s*(\d.*?)\sin\s[\d.]+s.*?=*$", re.MULTILINE)
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")
_NODE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Patterns for parse_collect_count — reads every form pytest prints with
# --collect-only -q (and the legacy header without -q).
_COLLECT_SUMMARY_RE = re.compile(r"(\d+) tests? collected")
_COLLECT_HEADER_RE = re.compile(r"collected\s+(\d+)\s+items?")

# Output-only flags that do not change collection or selection in pytest 8.x.
# These are stripped during invocation normalisation so that an operator who
# adds ``-q`` or ``--color=no`` does not trigger a mismatch.
_OUTPUT_ONLY_FLAGS = re.compile(
    r"(?:^|\s)"
    r"(?:-q\b|-qq\b|-v\b|-vv\b|-r[A-Z]*\b|--color=\S+|-p\s+no:\S+)"
)

# Selection-changing tokens that MUST refuse even if also configured.
# Note: -m is excluded because Python's `-m pytest` and pytest's `-m "marker"`
# are indistinguishable in a single command string.  The configured invocation
# always uses `-m pytest`, so matching `-m` would false-positive on every run.
_SELECTION_FLAGS = re.compile(
    r"(?:^|\s)"
    r"(?:-k\s|--lf\b|--deselect\b|-x\b|--maxfail\b)"
)

# Leading env prefix: one or more VAR=value pairs before the command.
_ENV_PREFIX_RE = re.compile(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+")

# Git committer time of HEAD (seconds since epoch).


def _head_committer_time(project: Path) -> int | None:
    """Return HEAD's committer timestamp as seconds since epoch, or None."""
    raw = _git(project, "log", "-1", "--format=%ct", "HEAD")
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _strip_env_prefix(invocation: str) -> str:
    """Strip a leading ``VAR=value`` env prefix from *invocation*."""
    return _ENV_PREFIX_RE.sub("", invocation, count=1)


def _strip_output_only_flags(invocation: str) -> str:
    """Strip output-only flags (``-q``, ``--color=auto``, etc.) from *invocation*."""
    result = invocation
    # Repeatedly strip until no more matches — flags may be adjacent.
    prev = None
    while prev != result:
        prev = result
        result = _OUTPUT_ONLY_FLAGS.sub(" ", result)
    return " ".join(result.split())


def _normalise_invocation(invocation: str) -> str:
    """Normalise an invocation for comparison: strip env prefix + output-only flags."""
    return _strip_output_only_flags(_strip_env_prefix(invocation))


def _check_selection_flags(invocation: str) -> str | None:
    """Return the first selection-changing flag found, or None."""
    m = _SELECTION_FLAGS.search(invocation)
    return m.group(0).strip() if m else None


def _extract_short_reason(out: str, node_id: str) -> str | None:
    """Return the short reason after `` - `` on the FAILED line, or None."""
    escaped = re.escape(node_id)
    m = re.search(rf"^FAILED\s+{escaped}\s*-\s*(.+)$", out, re.MULTILINE)
    return m.group(1).strip() if m else None


def _extract_failure_excerpts(out: str, node_ids: list[str],
                              max_chars: int = 4000) -> dict[str, str]:
    """Return ``{node_id: excerpt}`` for each failing node id.

    Each excerpt is the short-summary reason (text after `` - `` on the
    ``FAILED`` line), or the last 20 lines of the failure block when no
    short reason is available.  Excerpts longer than *max_chars* are
    truncated with a ``… [truncated]`` marker.
    """
    out = _ANSI_RE.sub("", out)
    excerpts: dict[str, str] = {}
    for nid in node_ids:
        short = _extract_short_reason(out, nid)
        if short:
            excerpts[nid] = (short[:max_chars] + "… [truncated]"
                             if len(short) > max_chars else short)
            continue
        block = _failure_block_for_node(out, nid)
        if block:
            lines = block.splitlines()
            tail = "\n".join(lines[-20:])
            excerpts[nid] = (tail[:max_chars] + "… [truncated]"
                             if len(tail) > max_chars else tail)
    return excerpts


def _failure_block_for_node(out: str, node_id: str) -> str | None:
    """Return the ``_ ... _`` failure block following *node_id*, or None."""
    escaped = re.escape(node_id)
    m = re.search(rf"(?:FAILED|ERROR)\s+{escaped}\b", out)
    if not m:
        return None
    after = out[m.end():]
    # The separator is a line of underscores with spaces, e.g. ``_ _ _ _``.
    # Use ^ with MULTILINE so we only match at line starts, not newlines
    # within the content.
    sep_re = re.compile(r"^_+(?: _+)*[ ]*\n", re.MULTILINE)
    sep_m = sep_re.search(after)
    if not sep_m:
        return None
    content_start = sep_m.end()
    rest = after[content_start:]
    # The block ends at the next separator or the summary line.
    next_sep = sep_re.search(rest)
    summary = _SUMMARY_RE.search(rest)
    end = len(rest)
    if next_sep:
        end = min(end, next_sep.start())
    if summary:
        end = min(end, summary.start())
    return rest[:end] if rest[:end].strip() else None


def parse_collect_count(out: str) -> int | None:
    """Parse the test count from ``--collect-only -q`` output.

    Reads every form pytest prints:

    * ``4586 tests collected in 1.11s`` (the ``-q`` summary)
    * ``1 test collected in 0.00s`` (singular)
    * ``4 tests collected, 1 error in 0.05s`` (with error suffix)
    * ``no tests collected in 0.00s`` → 0
    * ``collected 4 items`` (the legacy header without ``-q``)
    * ``1/4 tests collected (3 deselected)`` → 1 (selected count)

    Returns :data:`None` for empty text, the ``-qq`` per-file form
    (``test_one.py: 1``), error messages, and anything else unparseable.
    """
    text = (out or "").strip()
    if not text:
        return None

    # Selection form: "1/4 tests collected (3 deselected)" — first number is
    # the selected count, which is what the run will execute.
    m_sel = re.search(r"(\d+)/\d+\s+tests?\s+collected", text)
    if m_sel:
        return int(m_sel.group(1))

    # Summary line: "N tests collected" (covers ", N error" suffix).
    m_sum = _COLLECT_SUMMARY_RE.search(text)
    if m_sum:
        return int(m_sum.group(1))

    # Legacy header: "collected N items" (no -q).
    m_hdr = _COLLECT_HEADER_RE.search(text)
    if m_hdr:
        return int(m_hdr.group(1))

    # "no tests collected" → 0.
    if re.search(r"no\s+tests?\s+collected", text):
        return 0

    return None


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
    import time
    cmd = invocation
    if selection:
        cmd = f"{invocation} {' '.join(selection)}"
    t0 = time.monotonic()
    rc, stdout, stderr, timed_out = _bounded_run(
        cmd, shell=True, cwd=str(project), timeout=timeout,
    )
    if timed_out:
        raise TimeoutError(
            f"suite exceeded {timeout}s; the record keeps `suite_failed: "
            f"unmeasured` rather than a count nothing measured"
        )
    elapsed = round(time.monotonic() - t0)
    raw_output = (stdout or "") + (stderr or "")
    return {**_parse_for(invocation)(raw_output),
            "exit_code": rc,
            "suite_duration_sec": elapsed,
            "suite_output_text": raw_output}


AT_BASE_CAP = 50

# Judgment call: FAILURE_SURGE_THRESHOLD = 20.  Basis: on ilk-skills,
# canonical full suites on 2026-10-05/06 recorded 7-16 failures before
# declared reds were removed (v0.9.149: 16 of 5134; Batch W/X: 8; Batch Y:
# 7), and clean scoped verifies record 0, while the measured environmental
# surge was 53.  Wrong if a genuine batch regression of more than 20
# non-declared failures is refused as an environment fault and then
# reproduces at base-clean HEAD — in that case the stop still fails closed,
# but its message misleads, and the threshold should rise.
FAILURE_SURGE_THRESHOLD = 20

# Judgment call: K = 3.  Basis: keeps the worst case at 3 small pytest
# processes over the failing set only.  Wrong if a 1-in-4 flake routinely
# reads 3/3; then raise K, which is a single module constant.
FLAKY_RERUN_COUNT = 3

# Judgment call: total head-rerun budget 600 s.  Basis: a whole loaded
# -n 8 suite took 485 s; a rerun of ≤ 50 ids under the same flags
# should take a small fraction.  Wrong if real verifies hit the bound;
# the record says so, and the fix is to raise it with the measured pass times.
HEAD_RERUN_BUDGET_S = 600

# 900 s.  Basis: 2026-10-07 unblocked verifies took ~11-12 min of step 0 on a
# loaded host (07j: suite 474 s); R2b's took 52 min.  Wrong if healthy verifies
# routinely exceed it (then raise it with the measured phase_seconds, never to
# hide a phase that grew).
VERIFY_SLO_S = 900


def _check_slo_breach(batch: str, head: str, record: Path,
                      phase_seconds: dict[str, int]) -> None:
    """File to improvement backlog when total > VERIFY_SLO_S.

    Wrapped in try/except: a backlog failure prints one stderr line and
    NEVER changes the verify's exit code or record verdict.
    """
    total = phase_seconds.get("total", 0)
    if total <= VERIFY_SLO_S:
        return
    # Find the largest phase (excluding total).
    phases = {k: v for k, v in phase_seconds.items() if k != "total"}
    largest_phase = max(phases, key=phases.get)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                               / "ilk-feedback" / "scripts"))
        import improvement_backlog
        improvement_backlog.add_candidate(
            kind="toolkit",
            source="supervisor",
            title="batch verify exceeded 15 min",
            gap=f"{largest_phase}={phase_seconds[largest_phase]} s",
            evidence={
                "batch": batch,
                "head": head,
                "phase_seconds": phase_seconds,
                "record": str(record),
            },
            severity="high",
            leverage="high",
            proposed_fix=(
                "find the phase that grew; the rule is one suite per "
                "batch and no waits"
            ),
        )
        print(f"[verify-slo] {batch} took {total} s (> {VERIFY_SLO_S}): "
              f"{largest_phase}={phase_seconds[largest_phase]} s; "
              f"filed to the improvement backlog",
              file=sys.stderr)
    except Exception as exc:
        print(f"[verify-slo] backlog filing failed: {exc}", file=sys.stderr)

# ── Env pinning for at-base / adding-commit reruns ──────────────────────────

_ENV_MARKER = "base-pinned-v1"


def _base_env(worktree: Path) -> dict:
    """Build a subprocess environment pinned to *worktree*'s skills.

    The at-base and adding-commit reruns must execute the BASE tree's code,
    not the live clone's.  ``ILK_SKILL_HOME`` is set to ``<worktree>/skills``
    so that any script resolved through it lands in the base copy.  The
    runner's hook env vars are removed so the base test does not inherit
    dispatch markers from the live driver.
    """
    env = os.environ.copy()
    env["ILK_SKILL_HOME"] = str(worktree / "skills")
    env.pop("ILK_SHIPPED_MARKER", None)
    env.pop("ILK_ITERATION_SUBPLAN", None)
    return env


def _classify_single_at_base_verdict(
    rc: int, stdout: str, stderr: str, timed_out: bool,
    runner: str, wt: Path, nid: str,
) -> str:
    """Classify a single node id from one pytest run's output.

    Returns ``"passed"``, ``"failed"``, or ``"absent-at-base"``.
    This is the per-id verdict logic extracted from the at-base loop so it
    can be reused for both the batched path and the per-id fallback.
    """
    if timed_out:
        return "unmeasured-at-base"
    blob = (stdout or "") + (stderr or "")
    if "Timeout (>" in blob and "from pytest-timeout" in blob:
        return "unmeasured-at-base"
    if _is_vitest(runner):
        if rc == 0:
            return "passed"
        elif not (wt / nid).exists():
            return "absent-at-base"
        else:
            return "failed"
    elif rc == 4 or "error: not found:" in blob.lower():
        return "absent-at-base"
    elif rc == 0:
        return "passed"
    else:
        return "failed"


def run_at_base(project: Path, base_sha: str, node_ids: list[str],
                invocation: str, timeout: int = 600,
                baseline_red: list[dict] | None = None,
                registry_slugs: set[str] | None = None) -> dict:
    """Re-run each failing node id at the batch's base commit.

    This is the step the template describes as a procedure a worker performs.
    Every input is available to a program — the ids come from the suite run,
    the base sha from the master, the runner from config — and "did this node
    id pass at base" is a measurement, not a judgment.  So it is code.

    Returns ``{node_id: "passed" | "failed" | "absent-at-base" | "declared-at-base" | "born-red-at:<sha>"}``.
    ``absent-at-base`` means the test did not exist at the base commit, which
    makes a present failure this batch's own damage rather than an exoneration.
    ``declared-at-base`` means the test was in the base commit's ``baseline_red``
    and is already exonerated — no subprocess is spawned.
    ``born-red-at:<sha>`` means the test was already failing at the commit that
    added it (another plan's commit) — not attributed to this batch.
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
    # At-base cache: read cached verdicts for this base sha + invocation.
    cache_verdicts: dict[str, str] = {}
    try:
        vdir = _resolve_project_verification_dir(project)
        cache_path = vdir / f"at-base-{base_sha}.json"
        if cache_path.is_file():
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            stripped_inv = re.sub(r"\s-n\s+\S+|\s--dist\s+\S+", "", invocation)
            if (cache_data.get("invocation") == stripped_inv
                    and cache_data.get("env") == _ENV_MARKER):
                cache_verdicts = cache_data.get("verdicts", {})
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        cache_verdicts = {}

    # Filter out already-cached ids (only cacheable verdicts).
    _CACHEABLE = {"passed", "failed", "absent-at-base"}
    cached_ids = {nid for nid in node_ids
                  if nid in cache_verdicts and cache_verdicts[nid] in _CACHEABLE}
    verdicts: dict[str, str] = {nid: cache_verdicts[nid] for nid in cached_ids}
    uncached_ids = [nid for nid in node_ids if nid not in cached_ids]
    if not uncached_ids:
        verdicts.update(verdicts_declared)
        return verdicts

    node_ids = uncached_ids

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
        env = _base_env(wt)

        # ── batched at-base run: one pytest process for all ids ───────────
        # Vitest keeps the per-id path (absent-vs-failed needs per-file
        # existence checks that the batched output cannot provide).
        if _is_vitest(runner):
            for nid in node_ids:
                rc, stdout, stderr, timed_out = _bounded_run(
                    f"{runner} {nid}", shell=True, cwd=str(wt),
                    timeout=timeout, env=env,
                )
                verdicts[nid] = _classify_single_at_base_verdict(
                    rc, stdout, stderr, timed_out, runner, wt, nid,
                )
        else:
            # Run all ids in one process.
            rc, stdout, stderr, timed_out = _bounded_run(
                f"{runner} {' '.join(node_ids)}", shell=True, cwd=str(wt),
                timeout=timeout, env=env,
            )
            blob = (stdout or "") + (stderr or "")

            # Timeout of the batched process: all ids are unclassifiable.
            if timed_out:
                fallback_ids = list(node_ids)
            else:
                # Check for collection errors — if any file had a
                # collection error, pytest's output uses file paths in the
                # summary (e.g. "ERROR tests/test_broken.py") while node
                # ids carry the test name ("tests/test_broken.py::test_foo").
                # _NODE_RE matches the file path, not the node id, so
                # classification is unreliable.  Fall back to per-id for
                # ALL ids when collection errors are present.
                has_collection_errors = (
                    rc == 4 and "error: not found:" not in blob.lower()
                )
                if has_collection_errors:
                    fallback_ids = list(node_ids)
                else:
                    # Classify from the batched output.
                    failed_ids = set(_NODE_RE.findall(blob))
                    # Only ids explicitly named in "ERROR: not found:" are
                    # definitively absent.  Mark absent ONLY from the
                    # explicit message; everything else unclassified goes
                    # to fallback.
                    absent_from_output: set[str] = set()
                    for m in re.finditer(
                        r"ERROR:\s*not found:\s*(\S+)", blob, re.IGNORECASE,
                    ):
                        absent_from_output.add(m.group(1))

                    for nid in node_ids:
                        if nid in failed_ids:
                            verdicts[nid] = "failed"
                        elif nid in absent_from_output:
                            verdicts[nid] = "absent-at-base"
                        else:
                            # Not in the failed set and not explicitly
                            # absent.  If rc == 0, all passed.  If rc == 1
                            # (some failed), the non-failed ids passed.
                            # Ambiguous cases (collection errors, timeouts)
                            # are already routed to fallback above.
                            verdicts[nid] = "passed"

                    # Any id not classified above is ambiguous (pytest
                    # aborted before reaching it, or output is garbled).
                    # Fall back to per-id for exactly those.
                    fallback_ids = [nid for nid in node_ids
                                    if nid not in verdicts]

            # Per-id fallback for ambiguous ids.
            for nid in fallback_ids:
                rc, stdout, stderr, timed_out = _bounded_run(
                    f"{runner} {nid}", shell=True, cwd=str(wt),
                    timeout=timeout, env=env,
                )
                verdicts[nid] = _classify_single_at_base_verdict(
                    rc, stdout, stderr, timed_out, runner, wt, nid,
                )
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=project, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
        shutil.rmtree(tmp, ignore_errors=True)
    verdicts.update(verdicts_declared)

    # Write at-base cache: store only cacheable verdicts for this base sha.
    try:
        vdir = _resolve_project_verification_dir(project)
        cache_path = vdir / f"at-base-{base_sha}.json"
        stripped_inv = re.sub(r"\s-n\s+\S+|\s--dist\s+\S+", "", invocation)
        # Merge with any existing cache (may have verdicts from other runs).
        existing: dict[str, str] = {}
        if cache_path.is_file():
            try:
                ed = json.loads(cache_path.read_text(encoding="utf-8"))
                if (ed.get("invocation") == stripped_inv
                        and ed.get("env") == _ENV_MARKER):
                    existing = ed.get("verdicts", {})
            except (OSError, json.JSONDecodeError):
                pass
        for nid, v in verdicts.items():
            if v in _CACHEABLE:
                existing[nid] = v
        cache_path.write_text(
            json.dumps({"invocation": stripped_inv, "env": _ENV_MARKER,
                        "verdicts": existing},
                       sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
    except (FileNotFoundError, OSError):
        pass  # Non-fatal: caching is an optimisation.

    # Adding-commit rerun: for absent-at-base ids, re-run at the commit that
    # added their test file.  born-red-at:* = not attributed (the test was
    # already red when it was born).
    if registry_slugs:
        absent_ids = [nid for nid, v in verdicts.items()
                      if v == "absent-at-base"]
        if absent_ids:
            adding_verdicts, _adding_slugs = run_at_adding_commit(
                project, base_sha, absent_ids, invocation, registry_slugs,
                timeout=timeout)
            verdicts.update(adding_verdicts)

    return verdicts


def _find_adding_commits(project: Path, base_sha: str,
                         node_ids: list[str]) -> dict[str, str | None]:
    """Find the commit that added each node id's test file.

    Returns ``{node_id: commit_sha_or_None}``.  ``None`` means the file
    existed at base (shouldn't happen for absent-at-base ids, but defensive).
    Uses ``git log --diff-filter=A`` (oldest match) so renamed files resolve
    to the commit that first created them.
    """
    import subprocess
    # Map file paths to node ids (multiple ids can share a file).
    file_to_nids: dict[str, list[str]] = {}
    for nid in node_ids:
        fp = nid.split("::")[0]
        file_to_nids.setdefault(fp, []).append(nid)
    result: dict[str, str | None] = {}
    for fp in file_to_nids:
        r = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%H",
             f"{base_sha}..HEAD", "--", fp],
            cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30)
        shas = (r.stdout or "").strip().splitlines()
        if shas and shas[0]:
            # oldest = last in log output
            adding = shas[-1].strip()
        else:
            adding = None
        for nid in file_to_nids[fp]:
            result[nid] = adding
    return result


def ledger_at_base(project: Path, base_tree: str, node_ids: list[str],
                   base_entry: dict, baseline_red: list[dict] | None = None,
                   ) -> dict[str, str] | None:
    """Derive at-base verdicts from a ledger entry instead of rerunning.

    Returns ``{node_id: verdict}`` if the entry covers the base tree, or
    None if the entry is missing or invalid.  Verdicts:

    - ``declared-at-base`` — the id is in the base's baseline_red.
    - ``failed`` — the id is in the entry's failing set.
    - ``absent-at-base`` — the id's test file does not exist at the base.
    - ``passed`` — the id is not in the failing set and its file exists.

    This replaces the worktree + subprocess-per-id path in ``run_at_base``
    when a ledger entry is available.
    """
    if not node_ids:
        return {}

    # The entry must be for the base tree.
    if base_entry.get("tree") != base_tree:
        return None

    base_failing = set(base_entry.get("failing_nodes", []))
    declared = {n for n in node_ids
                if _in_baseline_red(n, baseline_red or [])}

    verdicts: dict[str, str] = {}
    for nid in node_ids:
        if nid in declared:
            verdicts[nid] = "declared-at-base"
        elif nid in base_failing:
            verdicts[nid] = "failed"
        else:
            # Check if the test file exists at the base tree.
            parts = nid.split("::", 1)
            file_path = parts[0]
            try:
                # git ls-tree returns empty for missing paths.
                out = subprocess.run(
                    ["git", "ls-tree", base_tree, file_path],
                    cwd=project, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=10,
                )
                if (out.stdout or "").strip():
                    verdicts[nid] = "passed"
                else:
                    verdicts[nid] = "absent-at-base"
            except (OSError, subprocess.SubprocessError):
                verdicts[nid] = "absent-at-base"
    return verdicts


def _extract_plan_slug(commit_msg: str) -> str | None:
    """Extract the plan slug from a ``[plan:<slug>#…]`` trailer, or None."""
    m = re.search(r"\[plan:([^#\]]+)#", commit_msg)
    return m.group(1) if m else None


def run_at_adding_commit(
    project: Path, base_sha: str,
    absent_ids: list[str], invocation: str,
    registry_slugs: set[str],
    timeout: int = 600,
) -> tuple[dict[str, str], dict[str, str | None]]:
    """Re-run ``absent-at-base`` ids at the commit that added their test file.

    Returns ``(verdicts, adding_slugs)`` where:

    - ``verdicts`` is ``{node_id: verdict}`` with verdict one of:
      ``absent-at-base`` (this batch's slug, or passed/absent at adding commit),
      ``born-red-at:<sha>`` (failed at the adding commit, not attributed).
    - ``adding_slugs`` is ``{node_id: slug_or_None}`` — the plan slug from the
      adding commit's message, for rendering in the record.
    """
    import shutil
    import subprocess
    import tempfile
    if not absent_ids:
        return {}, {}

    adding = _find_adding_commits(project, base_sha, absent_ids)

    # Group by adding commit (batch: one worktree per commit, not per id).
    by_commit: dict[str, list[str]] = {}
    for nid in absent_ids:
        sha = adding.get(nid)
        if sha is None:
            by_commit.setdefault("__none__", []).append(nid)
        else:
            by_commit.setdefault(sha, []).append(nid)

    verdicts: dict[str, str] = {}
    adding_slugs: dict[str, str | None] = {}

    # Ids with no adding commit (file existed at base — defensive).
    for nid in by_commit.get("__none__", []):
        verdicts[nid] = "absent-at-base"
        adding_slugs[nid] = None

    runner = re.sub(r"\s-n\s+\S+|\s--dist\s+\S+", "", invocation)

    for commit_sha, nids in by_commit.items():
        if commit_sha == "__none__":
            continue

        # Read the commit message to extract the plan slug.
        msg = _git(project, "log", "-1", "--format=%B", commit_sha) or ""
        slug = _extract_plan_slug(msg)
        for nid in nids:
            adding_slugs[nid] = slug

        # AC-1: this batch's slug ⇒ no rerun, stays absent-at-base.
        # An UNTRAILERED adding commit is treated the same way.  It lies in
        # base..HEAD by construction (see _find_adding_commits), and the
        # usual one is the runner's own `WIP: preserve timed-out iteration
        # changes` commit.  Reading "no slug" as "another plan" let gh-resolve
        # G4's verify excuse its own red-first pin as born-red (b15797e7,
        # 2026-10-03).  Only a trailer that NAMES another plan earns a rerun;
        # an unknown owner stays attributed.
        if slug is None or slug in registry_slugs:
            for nid in nids:
                verdicts[nid] = "absent-at-base"
            continue

        # Re-run at the adding commit in a detached worktree.
        tmp = Path(tempfile.mkdtemp(prefix="ilk-adding-"))
        wt = tmp / "adding-wt"
        try:
            add = subprocess.run(
                ["git", "worktree", "add", "--detach", str(wt), commit_sha],
                cwd=project, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180)
            if add.returncode != 0:
                # Can't check out — treat as absent (defensive).
                for nid in nids:
                    verdicts[nid] = "absent-at-base"
                continue
            env = _base_env(wt)
            for nid in nids:
                r = subprocess.run(
                    f"{runner} {nid}", shell=True, cwd=wt,
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                    env=env)
                blob = (r.stdout or "") + (r.stderr or "")
                if _is_vitest(runner):
                    if r.returncode == 0:
                        verdicts[nid] = "absent-at-base"
                    elif not (wt / nid).exists():
                        verdicts[nid] = "absent-at-base"
                    else:
                        verdicts[nid] = f"born-red-at:{commit_sha[:12]}"
                elif r.returncode == 4 or "error: not found:" in blob.lower():
                    verdicts[nid] = "absent-at-base"
                elif r.returncode == 0:
                    verdicts[nid] = "absent-at-base"
                else:
                    verdicts[nid] = f"born-red-at:{commit_sha[:12]}"
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                           cwd=project, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
            shutil.rmtree(tmp, ignore_errors=True)

    return verdicts, adding_slugs


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
                    timeout: int = 600,
                    budget_s: int = HEAD_RERUN_BUDGET_S
                    ) -> tuple[dict[str, tuple[int, int]], bool]:
    """Run failing node ids at HEAD, dropping settled ids after each pass.

    Each rerun is ONE pytest process over the remaining set, using the suite's
    own invocation and flags (xdist included).  An id that does NOT fail in a
    pass leaves the set; subsequent passes skip it.  Stop after K passes or
    when the set is empty.

    The total wall clock is bounded by *budget_s*.  Each pass's subprocess
    timeout is the remaining budget.  If a pass times out or the budget is
    spent, stop rerunning; ids still in the set keep the red count of the
    passes they completed.

    Returns ``(results, bound_hit)`` where *results* is
    ``{node_id: (red_count, runs)}`` and *bound_hit* is ``True`` when the
    budget was exhausted before all passes completed.
    """
    if not node_ids:
        return {}, False
    # Keep xdist — the suite's own flags.
    remaining = list(node_ids)
    red_counts: dict[str, int] = {nid: 0 for nid in node_ids}
    runs: dict[str, int] = {nid: 0 for nid in node_ids}
    bound_hit = False
    import time as _time
    deadline = _time.monotonic() + budget_s
    for _pass in range(K):
        if not remaining:
            break
        remaining_budget = deadline - _time.monotonic()
        if remaining_budget <= 0:
            bound_hit = True
            break
        pass_timeout = max(1, int(remaining_budget))
        try:
            r = subprocess.run(
                f"{invocation} {' '.join(remaining)}", shell=True,
                cwd=project, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=pass_timeout)
        except subprocess.TimeoutExpired:
            bound_hit = True
            break
        blob = (r.stdout or "") + (r.stderr or "")
        failed_this_run = set(_NODE_RE.findall(blob))
        still_remaining: list[str] = []
        for nid in remaining:
            runs[nid] += 1
            if nid in failed_this_run:
                red_counts[nid] += 1
                still_remaining.append(nid)
            # else: id passed — settled, drop from set.
        remaining = still_remaining
    # If we exhausted all K passes with remaining ids, that's not a bound hit.
    results = {nid: (red_counts[nid], runs[nid]) for nid in node_ids}
    return results, bound_hit


def run_head_alone(project: Path, node_ids: list[str], invocation: str,
                   timeout: int = 600) -> dict[str, str]:
    """Run each failing node id alone at HEAD and return {node_id: result}.

    Each id is run in its own pytest process with xdist flags stripped (as in
    ``run_at_base``).  The result is ``"passed"``, ``"failed"``, or
    ``"skipped-cap"`` when more than ``AT_BASE_CAP`` ids are given.

    For vitest the id is a file path, so run that file alone.
    """
    if not node_ids:
        return {}
    if len(node_ids) > AT_BASE_CAP:
        return {nid: "skipped-cap" for nid in node_ids}
    runner = re.sub(r"\s-n\s+\S+|\s--dist\s+\S+", "", invocation)
    results: dict[str, str] = {}
    for nid in node_ids:
        r = subprocess.run(f"{runner} {nid}", shell=True,
                           cwd=project, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        blob = (r.stdout or "") + (r.stderr or "")
        failed = bool(_NODE_RE.findall(blob))
        results[nid] = "failed" if failed else "passed"
    return results


def _batch_commit_files(project: Path, base_sha: str,
                        batch_slugs: set[str]) -> set[str] | None:
    """Files changed in ``base_sha..HEAD`` by commits that may be the batch's.

    A commit is excluded only when it carries a ``[plan:<slug>#…]`` trailer
    and none of them is one of the batch's slugs, i.e. it provably belongs
    to another batch.  A trailer-less commit (a hand fix, an operator edit)
    still counts: dropping it would under-attribute, which lets a ship
    through.  ``None`` when no commit in range carries a batch trailer
    (e.g. a shared remote stripped the trailers)."""
    log = _git(project, "log", "--format=%x1e%H%n%B", f"{base_sha}..HEAD") or ""
    ours: list[str] = []
    saw_batch_trailer = False
    for entry in log.split("\x1e"):
        sha, _, body = entry.strip().partition("\n")
        if not sha:
            continue
        slugs_here = set(re.findall(r"\[plan:([^#\]\s]+)#", body))
        if slugs_here & batch_slugs:
            saw_batch_trailer = True
            ours.append(sha)
        elif not slugs_here:
            ours.append(sha)
    if not saw_batch_trailer:
        return None
    files: set[str] = set()
    for sha in ours:
        out = _git(project, "diff-tree", "--no-commit-id", "--name-only",
                   "-r", "--root", sha) or ""
        files.update(out.splitlines())
    return files


def batch_touched_files(project: Path, base_sha: str,
                        node_ids: list[str],
                        batch_slugs: set[str] | None = None) -> dict[str, bool]:
    """For each node id, check whether the batch touched its test file.

    With ``batch_slugs``, the changed set is the files of the batch's OWN
    commits (those carrying a ``[plan:<slug>#…]`` trailer for one of its
    slugs).  On a shared main ``base_sha..HEAD`` also holds other batches'
    commits, and a file only they changed must not read as touched.

    Without slugs, or when no commit in range carries one, falls back to
    ``git diff --name-only <base_sha> HEAD``: over-attribution blocks a ship,
    it never lets one through.

    Returns ``{node_id: True/False}``.
    """
    if not node_ids:
        return {}
    changed_set = (_batch_commit_files(project, base_sha, batch_slugs)
                   if batch_slugs else None)
    if changed_set is None:
        if batch_slugs:
            print("verification_record: no commit in range carries a batch "
                  "trailer; 'batch touched file' uses the whole "
                  f"{base_sha[:8]}..HEAD diff", file=sys.stderr)
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
                  head_reruns: dict[str, int | tuple[int, int]] | None = None,
                  batch_touched: dict[str, bool] | None = None,
                  alone: dict[str, str] | None = None,
                  flaky_owed: list[str] | None = None,
                  adding_slugs: dict[str, str | None] | None = None,
                  failed_at_base: list[str] | None = None,
                  suite_duration_sec: int | None = None,
                  suite_budget: tuple[int, str] | None = None,
                  suite_output_path: str | None = None,
                  suite_output_text: str | None = None,
                  suite_source: str | None = None,
                  suite_source_sha256: str | None = None,
                  ledger_mode: str | None = None,
                  head_source: str | None = None,
                  base_source: str | None = None,
                  ledger_wait_sec: int | None = None,
                  record_elapsed_sec: int | None = None,
                  phase_seconds: dict[str, int] | None = None,
                  contention: dict[str, dict] | None = None,
                  owners: dict[str, dict] | None = None,
                  head_rerun_bound_hit: bool = False,
                  carried_from: str | None = None,
                  rerun_selection: int | None = None) -> str:
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
        f"suite_scope_reason: {scope.get('reason', '').replace(chr(10), '; ')}",
        f"selection_size: {scope['count']}",
        f"suite_total: {c['total']}",
        f"suite_passed: {c['passed']}",
        f"suite_failed: {c['failed'] + c['errors']}",
        f"suite_errors: {c['errors']}",
        f"suite_skipped: {c['skipped']}",
    ]
    if suite_duration_sec is not None:
        lines.append(f"suite_duration_sec: {suite_duration_sec}")
    if suite_budget is not None:
        budget_val, budget_src = suite_budget
        lines.append(f"suite_budget: {budget_val} ({budget_src})")
    if suite_output_path is not None:
        lines.append(f"suite_output: {suite_output_path}")
    if suite_source is not None:
        lines.append(f"suite_source: {suite_source}")
    if suite_source_sha256 is not None:
        lines.append(f"suite_source_sha256: {suite_source_sha256}")
    # Ledger citation fields.
    if ledger_mode is not None:
        lines.append(f"ledger_mode: {ledger_mode}")
    if head_source is not None:
        lines.append(f"head_source: {head_source}")
    if base_source is not None:
        lines.append(f"base_source: {base_source}")
    if ledger_wait_sec is not None:
        lines.append(f"ledger_wait_sec: {ledger_wait_sec}")
    if record_elapsed_sec is not None:
        lines.append(f"record_elapsed_sec: {record_elapsed_sec}")
    if phase_seconds is not None:
        parts = [f"{k}={v}" for k, v in phase_seconds.items()]
        lines.append(f"phase_seconds: {' '.join(parts)}")
    if contention is not None:
        def _fmt_side(side: dict) -> str:
            os_val = side.get("other_suites", "unmeasured")
            load = side.get("load1", 0.0)
            cpus = side.get("cpus", "?")
            return f"{os_val} suites load {load}/{cpus}"
        lines.append(
            f"contention: start {_fmt_side(contention['start'])}; "
            f"end {_fmt_side(contention['end'])}")
    if carried_from is not None:
        lines.append(f"carried_from: {carried_from}")
    if rerun_selection is not None:
        lines.append(f"rerun_selection: {rerun_selection} files")
    lines += [
        "",
        "## At-base rerun",
        "",
    ]
    if at_base_error and "exceeds the" in at_base_error and "cap" in at_base_error:
        # Designed human-escalation: name the stop, not the symptom.
        uncovered = c['failed'] + c['errors']
        if "surge cap" in at_base_error:
            # Failure surge: environment fault, not a batch issue.
            # Extract first failing ids from the error message if present.
            first_ids_part = ""
            if "First failing ids:" in at_base_error:
                first_ids_part = ". " + at_base_error.split("First failing ids:")[1].split(". Suite")[0]
            # The measured reproduce count ("N of M also fail at base") is
            # the evidence for the verdict, so the record carries it.
            measured = re.search(r"\d+ of \d+ also fail at base", at_base_error)
            measured_part = f" and {measured.group(0)}" if measured else ""
            lines += [
                f"at_base_cap_exceeded: {uncovered} non-declared failures "
                f"exceeds the {FAILURE_SURGE_THRESHOLD} surge cap"
                f"{measured_part}; "
                f"environment fault, not a batch issue"
                f"{first_ids_part}",
                "",
            ]
        else:
            lines += [
                f"at_base_cap_exceeded: {uncovered} uncovered (>50 cap) — complete "
                f"ship.baseline_red coverage for the pre-existing families and re-run",
                "",
            ]
    elif not at_base:
        lines += ["_(no failures)_", ""]
    else:
        has_reruns = head_reruns is not None and batch_touched is not None
        has_alone = alone is not None
        if has_reruns and has_alone:
            lines += ["| node id | at base | in baseline_red | head reruns | batch touched file | alone |",
                      "|---|---|---|---|---|---|"]
        elif has_reruns:
            lines += ["| node id | at base | in baseline_red | head reruns | batch touched file |",
                      "|---|---|---|---|---|"]
        else:
            lines += ["| node id | at base | in baseline_red |",
                      "|---|---|---|"]
        order_dependent: list[str] = []
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
                rerun_val = head_reruns.get(nid, (0, 0))
                touched_val = batch_touched.get(nid, False)
                if rerun_val == "—":
                    rerun_str = "—"
                elif isinstance(rerun_val, tuple):
                    red_count, runs = rerun_val
                    rerun_str = f"{red_count}/{runs}"
                else:
                    # Legacy int format (red_count only, denominator = K).
                    rerun_str = f"{rerun_val}/{FLAKY_RERUN_COUNT}"
                if touched_val == "—":
                    touched_str = "—"
                else:
                    touched_str = "yes" if touched_val else "no"
                if has_alone and alone is not None:
                    alone_val = alone.get(nid, "—")
                    lines.append(f"| {nid} | {verdict} | {red} | {rerun_str} | {touched_str} | {alone_val} |")
                    # Track order-dependent: passes alone but red in batch.
                    rerun_red = rerun_val[0] if isinstance(rerun_val, tuple) else rerun_val
                    if alone_val == "passed" and isinstance(rerun_red, int) and rerun_red >= 1:
                        order_dependent.append(nid)
                else:
                    lines.append(f"| {nid} | {verdict} | {red} | {rerun_str} | {touched_str} |")
            else:
                lines.append(f"| {nid} | {verdict} | {red} |")
        lines.append("")
        if head_rerun_bound_hit:
            lines.append(f"head reruns: stopped at the {HEAD_RERUN_BUDGET_S} s bound; unsettled ids count as red")
            lines.append("")
        if order_dependent:
            lines += ["## Order-dependent", ""]
            for nid in order_dependent:
                rv = head_reruns.get(nid, (0, 0)) if head_reruns else (0, 0)
                if isinstance(rv, tuple):
                    rc, rn = rv
                else:
                    rc, rn = rv, FLAKY_RERUN_COUNT
                lines.append(f"order-dependent: {nid} — passes alone, red {rc}/{rn} with the failing set")
            lines.append("")
    if adding_slugs:
        born_red = {nid: v for nid, v in at_base.items()
                    if v.startswith("born-red-at:")}
        if born_red:
            lines += ["## Adding-commit metadata", ""]
            for nid, verdict in born_red.items():
                slug = adding_slugs.get(nid)
                slug_str = slug if slug else "(untrailered)"
                sha = verdict.split(":", 1)[1] if ":" in verdict else "?"
                lines.append(f"born-red-at: {nid} — adding commit {sha} ({slug_str})")
            lines.append("")
    if flaky_owed:
        lines += ["## Flaky (owed)", ""]
        for nid in flaky_owed:
            lines.append(f"- {nid}")
        lines.append("")
    if owners:
        lines += ["## Owners", ""]
        lines += ["| node id | owner | commit | how |",
                  "|---|---|---|---|"]
        for nid, info in owners.items():
            slug = info.get("slug", "—") or "—"
            sha = info.get("sha", "—") or "—"
            how = info.get("how", "unknown") or "unknown"
            sha_short = sha[:12] if sha and sha != "—" else "—"
            lines.append(f"| {nid} | {slug} | {sha_short} | {how} |")
        lines.append("")
    if failed_at_base:
        lines += ["## Failed at base, not in baseline_red", ""]
        for nid in failed_at_base:
            lines.append(f"suggested baseline_red: {nid} — "
                         f"failed at base {base_sha[:7]} (recorded "
                         f"{__import__('datetime').date.today().isoformat()})")
        lines.append("")
    # Failure excerpts: one ### heading per failing node id with its short
    # reason or the last 20 lines of its failure block.
    if suite_output_text is not None:
        node_ids = results.get("failing_nodes", [])
        if node_ids:
            excerpts = _extract_failure_excerpts(suite_output_text, node_ids)
            if excerpts:
                lines += ["## Failure excerpts", ""]
                for nid in node_ids:
                    if nid not in excerpts:
                        continue
                    lines.append(f"### {nid}")
                    lines.append("")
                    lines.append("```")
                    lines.append(excerpts[nid])
                    lines.append("```")
                    lines.append("")
    lines += [
        "## Findings",
        "",
        "_(the worker writes narrative here; no parser reads this section)_",
        "",
    ]
    return "\n".join(lines)


def _compute_record_digest(text: str) -> str:
    """SHA-256 of the machine-readable surface (everything above ``## Findings``).

    The gate recomputes this and refuses on mismatch, so any edit to a row or
    header after recording is caught (R4).
    """
    surface = text.split("## Findings")[0] if "## Findings" in text else text
    return hashlib.sha256(surface.encode("utf-8")).hexdigest()


def _history_path(record: Path) -> Path:
    """Return the history file path for a record."""
    return record.with_suffix(".history.jsonl")


def _append_history_entry(record: Path, attempt: int, digest: str,
                          failing_nodes: list[str],
                          suite_duration_sec: int | None = None,
                          head: str | None = None,
                          tree: str | None = None,
                          contention: dict[str, dict] | None = None) -> None:
    """Append one attempt's metadata to the history file (R3).

    The history is append-only; each line is a JSON object with the attempt
    number, the record's digest, and the failing node ids.
    """
    import os
    hist = _history_path(record)
    entry = {"attempt": attempt, "digest": digest,
             "failing_nodes": sorted(failing_nodes)}
    if suite_duration_sec is not None:
        entry["suite_duration_sec"] = suite_duration_sec
    # The gate scopes carry-forward to attempts at the same CODE, which it can
    # only decide if each attempt names its commit (the spec's history shape).
    if head:
        entry["head"] = head
    if tree:
        entry["tree"] = tree
    if contention is not None:
        entry["contention"] = contention
    with open(hist, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def _read_history(record: Path) -> list[dict]:
    """Read the history file for a record.  Returns [] if missing."""
    hist = _history_path(record)
    if not hist.is_file():
        return []
    entries: list[dict] = []
    for line in hist.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


# ── Measured suite budget ──────────────────────────────────────────────────

# Judgment call: 2×, [600, 3600].  Basis: ilk-skills' suite measured ~9-12 min
# and gh-resolve's ~4-5 min on this host, and 2× absorbs load without letting a
# hang run for an hour.  Wrong if a project's suite varies more than 2× run to
# run; then its `ship.suite.timeout` should be passed explicitly.
_SUITE_BUDGET_MULTIPLIER = 2
_SUITE_BUDGET_MIN = 600
_SUITE_BUDGET_MAX = 3600
_SUITE_BUDGET_DEFAULT = 1800
_SUITE_BUDGET_HISTORY_COUNT = 5


def _read_historical_suite_durations(project: Path) -> list[int]:
    """Read suite_duration_sec from the last N history entries across all
    batches in this project's verification directory.

    Returns a list of durations (may be empty).
    """
    try:
        vdir = _resolve_project_verification_dir(project)
    except FileNotFoundError:
        return []
    durations: list[int] = []
    # Scan all batch records' history files, newest first.
    for hist_file in sorted(vdir.glob("*-batch.history.jsonl"), reverse=True):
        for line in hist_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            dur = entry.get("suite_duration_sec")
            if isinstance(dur, int) and dur > 0:
                durations.append(dur)
                if len(durations) >= _SUITE_BUDGET_HISTORY_COUNT:
                    return durations
    return durations


def compute_suite_budget(project: Path, explicit_timeout: int | None) -> tuple[int, str]:
    """Compute the suite budget.

    Returns ``(budget_sec, source)`` where source is ``"explicit"``,
    ``"measured"``, or ``"default"``.

    When ``explicit_timeout`` is not None, it is used as-is.  Otherwise the
    budget is ``clamp(2 × max(last 5 durations), 600, 3600)``, falling back
    to 1800 when there is no history.
    """
    if explicit_timeout is not None:
        return explicit_timeout, "explicit"
    durations = _read_historical_suite_durations(project)
    if not durations:
        return _SUITE_BUDGET_DEFAULT, "default"
    measured = max(durations)
    budget = measured * _SUITE_BUDGET_MULTIPLIER
    budget = max(_SUITE_BUDGET_MIN, min(budget, _SUITE_BUDGET_MAX))
    return budget, "measured"


def _existing_record_is_measured(record: Path, head: str) -> bool:
    """Return True when *record* exists, names *head*, and carries a numeric
    ``suite_failed`` — i.e. the suite was actually measured, not a stub.

    A measured record is never replaced by a stub or a weaker outcome.
    """
    if not record.is_file():
        return False
    try:
        text = record.read_text(encoding="utf-8")
    except OSError:
        return False
    # Extract verified_head.
    m = re.search(r"^verified_head:\s*(\S+)", text, re.MULTILINE)
    if not m or m.group(1) != head:
        return False
    # Extract suite_failed — must be numeric (an int), not "unmeasured".
    m = re.search(r"^suite_failed:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return False
    try:
        int(m.group(1).strip())
        return True
    except ValueError:
        return False


def _atomic_write(path: Path, text: str) -> None:
    """Write *text* to *path* atomically via ``<path>.tmp`` + ``os.replace``."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _try_remeasure(
    project: Path,
    record: Path,
    head: str,
    tree: str,
    base_sha: str,
    invocation: str,
    scope: dict,
    args,
) -> dict | None:
    """Attempt the incremental re-measure path.

    Returns a dict with the merged results and metadata if the re-measure
    path triggers, or None if a full re-measure is needed.

    The re-measure path triggers when ALL hold:
    1. History has a prior measured attempt with head H0.
    2. H0 is an ancestor of HEAD.
    3. suite_ledger.lookup(tree(H0), invocation) returns an entry whose
       digest validates.
    4. No path in ``git diff --name-only H0..HEAD`` is test infrastructure.
    """
    import suite_ledger

    # 1. Read history to find the most recent measured attempt.
    history = _read_history(record)
    if not history:
        return None

    # Find the most recent entry with a head field.
    prior = None
    for entry in reversed(history):
        if "head" in entry and "tree" in entry:
            prior = entry
            break
    if prior is None:
        return None

    h0_sha = prior["head"]
    h0_tree = prior["tree"]

    # 2. Check if H0 is an ancestor of HEAD.
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", h0_sha, head],
            cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None

    # 3. Look up the ledger entry for H0's tree.
    ledger_entry = suite_ledger.lookup(project, h0_tree, invocation)
    if ledger_entry is None:
        return None

    # Validate the digest.
    expected_digest = ledger_entry.get("digest", "")
    computed = suite_ledger._compute_digest({
        k: v for k, v in ledger_entry.items() if k != "digest"
    })
    if expected_digest != computed:
        return None

    # 4. Check if no test infrastructure files changed between H0 and HEAD.
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", h0_sha, head],
            cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if diff_result.returncode != 0:
            return None
        changed_files = [f for f in diff_result.stdout.splitlines() if f.strip()]
    except (OSError, subprocess.SubprocessError):
        return None

    # Check for test infrastructure changes.
    for f in changed_files:
        if _test_infra_change(f):
            return None

    # All conditions hold — compute the selection.
    # Selection = changed test files ∪ importer tests ∪ prior failing ids.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_importers import _is_test_file, importer_tests

    changed_test_files = [f for f in changed_files if _is_test_file(f)]

    # Importer tests of changed .py modules.
    importer_files = importer_tests(project, changed_files)

    # Prior failing ids.
    prior_failing = prior.get("failing_nodes", [])

    # Combine and deduplicate the selection.
    selection_set: set[str] = set()
    selection_set.update(changed_test_files)
    selection_set.update(importer_files)
    # For prior failing ids, we need to include their test files.
    for nid in prior_failing:
        # Node id format: tests/test_suite.py::test_name
        test_file = nid.split("::")[0]
        selection_set.add(test_file)
    selection = sorted(selection_set)

    if not selection:
        # No selection — fall back to full re-measure.
        return None

    # Run only the selection.
    suite_budget, suite_budget_source = compute_suite_budget(
        project, args.suite_timeout)
    suite_start = time.monotonic()
    try:
        results = run_suite(project, invocation, suite_budget,
                            selection=selection)
    except (TimeoutError, ValueError):
        return None
    suite_elapsed = round(time.monotonic() - suite_start)

    # Merge results: prior entry's counts with the selection's results
    # substituted.
    prior_counts = ledger_entry["counts"]
    selection_counts = results["counts"]

    # Compute the carried test count (tests not in the selection).
    prior_total = prior_counts["total"]
    selection_total = selection_counts["total"]
    carried_count = prior_total - selection_total

    # Merge counts: for each category, use the selection's count for tests
    # in the selection, and the prior count for carried tests.
    # This is a simplification: we assume the selection covers all failing
    # tests and the carried tests are all passing.
    merged_counts = {
        "passed": prior_counts["passed"] + selection_counts["passed"],
        "failed": selection_counts["failed"],  # Only selection failures matter
        "errors": selection_counts["errors"],
        "skipped": prior_counts["skipped"] + selection_counts["skipped"],
        "xfailed": prior_counts["xfailed"] + selection_counts["xfailed"],
        "xpassed": prior_counts["xpassed"] + selection_counts["xpassed"],
    }
    merged_counts["total"] = sum(v for k, v in merged_counts.items()
                                  if k != "total")

    # Build the carried_from string: <tree> <digest16>.
    carried_from = f"{h0_tree} {ledger_entry['digest'][:16]}"

    # Build phase_seconds with reused.
    phase_seconds = {
        "suite": suite_elapsed,
        "at_base": 0,
        "head_reruns": 0,
        "total": suite_elapsed,
        "reused": carried_count,
    }

    # Build the merged results.
    merged_results = {
        "counts": merged_counts,
        "failing_nodes": results["failing_nodes"],
        "suite_duration_sec": results.get("suite_duration_sec"),
    }

    return {
        "results": merged_results,
        "carried_from": carried_from,
        "rerun_selection": len(selection),
        "phase_seconds": phase_seconds,
        "suite_elapsed": suite_elapsed,
    }


def _write_measured_record(project: Path, record: Path, args,
                          registry_slugs: set[str] | None = None) -> int:
    """Own the whole machine-read surface: measure it, then write it.

    Ordering is deliberate.  The record is written TWICE — a signed stub with
    ``suite_failed: unmeasured`` before the long run, then the real thing after.
    A suite that exceeds its bound is killed with the gate, and without the stub
    the step leaves nothing at all: no record, and a next iteration that fails
    "record missing" for a reason that has nothing to do with the code.  The
    stub is refused by the checker (``unmeasured`` is not a count), which is the
    correct outcome — loudly incomplete beats silently absent.

    The stub only fills a vacuum: it is written only when no record exists, or
    the existing record names a different ``verified_head``.  A measured record
    (numeric ``suite_failed``) for the same HEAD is never replaced by a stub
    or a weaker outcome.  All writes are atomic (``<record>.tmp`` +
    ``os.replace``).  Every non-measured exit prints ``ILK-CHECK: unmeasured``
    on stderr so the runner can classify the exit without reading the record.
    """
    if not args.base_sha:
        print("ERROR: --run-suite requires --base-sha", file=sys.stderr)
        print("ILK-CHECK: unmeasured no base-sha", file=sys.stderr)
        return 2

    # Guard: refuse in a worker session.  The suite runs in the driver, not
    # in a worker.  A worker that needs a fresh record should commit its fix
    # and end its turn — the next iteration's gate-first re-measures in the
    # driver.
    if os.environ.get("ILK_WORKER_SESSION") == "1":
        print("ILK-CHECK: unmeasured refused in a worker session",
              file=sys.stderr)
        print("verification_record: the suite runs in the driver — commit "
              "your fix and end your turn; the next iteration re-measures",
              file=sys.stderr)
        return 1

    # Guard: refuse a dirty tracked tree.  A record must name the tree it
    # measured; a dirty tree means the record's head does not match the
    # working copy's tests.  Untracked files do not change what HEAD's tests
    # import.
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        dirty_lines = [l for l in (status.stdout or "").splitlines()
                       if l.strip()]
        if dirty_lines:
            print(f"ILK-CHECK: unmeasured dirty tree "
                  f"({len(dirty_lines)} tracked files modified)",
                  file=sys.stderr)
            return 1
    except (OSError, subprocess.SubprocessError):
        pass  # if git status fails, proceed — the guard is best-effort

    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        from ship_audit import _resolve_expected_invocation
    except ImportError as exc:
        print(f"ERROR: cannot import ship_audit: {exc}", file=sys.stderr)
        print("ILK-CHECK: unmeasured import error", file=sys.stderr)
        return 1

    invocation = (_resolve_expected_invocation(project) or "").strip()
    if not invocation:
        print("ERROR: ship.suite is not configured; refusing to invent a suite "
              "command. A record naming no invocation cannot be verified.",
              file=sys.stderr)
        print("ILK-CHECK: unmeasured not configured", file=sys.stderr)
        return 1

    head = read_head_from_git(project)
    tree = _git(project, "rev-parse", "HEAD^{tree}")
    if not head or not tree:
        print(f"ERROR: cannot read HEAD/tree from git in {project}", file=sys.stderr)
        print("ILK-CHECK: unmeasured git error", file=sys.stderr)
        return 1
    if head.startswith(args.base_sha) or args.base_sha.startswith(head):
        print(f"ERROR: base_sha equals HEAD ({head[:12]}); the comparison would "
              f"be HEAD against itself and could not detect a regression.",
              file=sys.stderr)
        print("ILK-CHECK: unmeasured base equals head", file=sys.stderr)
        return 1

    scope = compute_suite_scope(project, args.base_sha)
    # --scope full overrides the computed scope.  When the computed mode was
    # already full, preserve the computed reason so the record says *why*.
    if getattr(args, "scope", "auto") == "full":
        if scope["mode"] == "full":
            scope["reason"] = f"override: --scope full; computed: {scope['reason']}"
        else:
            scope["reason"] = "override: --scope full"
        scope["mode"] = "full"
    record.parent.mkdir(parents=True, exist_ok=True)

    stub = (f"# Batch verification record — {args.batch or record.stem}\n\n"
            f"record_writer: {RECORD_WRITER}\n"
            f"verified_head: {head}\n"
            f"verified_tree: {tree}\n"
            f"base_sha: {args.base_sha}\n"
            f"suite_invocation: {invocation}\n"
            f"suite_scope: {scope['mode']}\n"
            f"suite_scope_reason: {scope.get('reason', '').replace(chr(10), '; ')}\n"
            f"suite_failed: unmeasured\n\n"
            f"## At-base rerun\n\n_(suite did not finish)_\n")
    # The stub only fills a vacuum: write it only when there is no record,
    # or the existing record names a different HEAD.  A measured record
    # (numeric suite_failed) for the same HEAD is never replaced by a stub.
    if not _existing_record_is_measured(record, head):
        _atomic_write(record, stub)

    # ── Re-measure path ─────────────────────────────────────────────────
    # Try the incremental re-measure path first.  If it triggers, we skip
    # the full suite run and use the carried-forward results.
    remeasure = _try_remeasure(project, record, head, tree, args.base_sha,
                               invocation, scope, args)
    if remeasure is not None:
        # Re-measure path triggered — write the record with carried results.
        results = remeasure["results"]
        carried_from = remeasure["carried_from"]
        rerun_selection = remeasure["rerun_selection"]
        phase_seconds = remeasure["phase_seconds"]

        # Read history for attempt number.
        history = _read_history(record)
        attempt = len(history) + 1

        record_text = render_record(
            batch=args.batch or record.stem,
            head=head, tree=tree, base_sha=args.base_sha,
            invocation=invocation, scope=scope, results=results,
            at_base={}, base_red=[], head_red=[],
            suite_duration_sec=results.get("suite_duration_sec"),
            suite_source="tool",
            phase_seconds=phase_seconds,
            carried_from=carried_from,
            rerun_selection=rerun_selection,
        )
        # Inject attempt header after the batch line.
        record_text = record_text.replace(
            "record_writer:", f"attempt: {attempt}\nrecord_writer:", 1)
        _atomic_write(record, record_text)

        # SLO breach check.
        _check_slo_breach(args.batch or record.stem, head, record,
                          phase_seconds)

        # Append to history with carried_from.
        digest = _compute_record_digest(record_text)
        hist_entry = {
            "attempt": attempt, "digest": digest,
            "failing_nodes": sorted(results["failing_nodes"]),
            "carried_from": carried_from,
        }
        if results.get("suite_duration_sec") is not None:
            hist_entry["suite_duration_sec"] = results["suite_duration_sec"]
        hist_entry["head"] = head
        hist_entry["tree"] = tree
        hist_path = _history_path(record)
        with open(hist_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(hist_entry, sort_keys=True) + "\n")

        c = results["counts"]
        print(f"recorded (re-measure): {c['passed']} passed, {c['failed']} failed, "
              f"{c['errors']} errors of {c['total']} · scope={scope['mode']} · "
              f"carried_from={carried_from} · rerun_selection={rerun_selection} → {record}")
        return 0

    # ── Ledger mode ──────────────────────────────────────────────────────
    ledger_mode = getattr(args, "ledger", "off")
    ledger_entry: dict | None = None
    base_ledger: dict | None = None
    at_base_from_ledger = False
    ledger_wait_sec = 0
    record_start = time.monotonic()
    suite_start = time.monotonic()
    contention_start = measure_contention()
    suite_budget: int = 0
    suite_budget_source: str = "default"

    if ledger_mode != "off":
        import suite_ledger
        base_tree = _git(project, "rev-parse", f"{args.base_sha}^{{tree}}")
        ledger_entry = suite_ledger.lookup(project, tree, invocation)
        if ledger_entry:
            # AC-4: a full-suite request must not reuse a scoped ledger
            # entry.  The scoped result satisfies a different cost
            # boundary and cannot substitute for a full-suite measurement.
            entry_scope_mode = ledger_entry.get("scope_mode", "full")
            if (getattr(args, "scope", "auto") == "full"
                    and entry_scope_mode == "scoped"):
                print("INFO: refusing scoped ledger entry for --scope full "
                      "request; will re-measure", file=sys.stderr)
                ledger_entry = None

        if ledger_entry:
            # Build results from the ledger entry.
            results = {
                "counts": ledger_entry["counts"],
                "failing_nodes": ledger_entry["failing_nodes"],
            }
            # Read suite output from the ledger's output file.
            ld = suite_ledger.ledger_dir(project)
            output_sha = ledger_entry.get("output_sha256")
            output_path = ld / f"{tree}.output.txt"
            if output_path.is_file():
                suite_output_text = output_path.read_text(encoding="utf-8")
                if output_sha:
                    actual = hashlib.sha256(
                        suite_output_text.encode()).hexdigest()
                    if actual != output_sha:
                        print(f"WARNING: ledger output hash mismatch for "
                              f"{tree[:12]}", file=sys.stderr)
            else:
                suite_output_text = ""
            # Preserve the entry's recorded scope.  Legacy entries lack
            # scope_mode and are treated as full-suite runs.
            entry_scope = ledger_entry.get("scope_mode", "full")
            scope["mode"] = entry_scope
            if entry_scope == "scoped":
                selected = ledger_entry.get("selected_files", [])
                scope["reason"] = (f"ledger entry is a scoped run "
                                   f"({len(selected)} file(s))")
                scope["count"] = len(selected)
                scope["selection"] = selected
            else:
                scope["reason"] = "ledger entry is a full-suite run"
                scope["count"] = ledger_entry["counts"]["total"]
            results["suite_duration_sec"] = ledger_entry.get(
                "suite_duration_sec")
            results["suite_budget"] = (0, "ledger")
        elif ledger_mode == "require":
            # No entry found — measure in-process, then use the result.
            # Apply the computed scope selection so the run matches the
            # record's suite_scope claim.  Without this the require path
            # ran the full suite while recording suite_scope: scoped.
            # MEASURED 2026-10-06 (Batch Z): 5,142 tests ran, record
            # claimed selection_size: 8.
            selection = (scope.get("selection")
                         if scope.get("mode") == "scoped" else None)
            try:
                suite_budget, suite_budget_source = compute_suite_budget(
                    project, args.suite_timeout)
                results = run_suite(project, invocation, suite_budget,
                                    selection=selection)
                suite_output_text = results.get("suite_output_text", "")
            except (TimeoutError, ValueError) as exc:
                print(f"ERROR: ledger require — no entry and suite failed: "
                      f"{exc}", file=sys.stderr)
                print("ILK-CHECK: unmeasured ledger require failed",
                      file=sys.stderr)
                return 1
            # Write the entry to the ledger for future lookups.
            # Include scope metadata so readers can distinguish scoped
            # from full measurements and refuse unsafe reuse.
            try:
                import suite_ledger
                measure_entry = {
                    "tree": tree,
                    "invocation": invocation,
                    "counts": results["counts"],
                    "failing_nodes": sorted(results["failing_nodes"]),
                    "suite_duration_sec": results.get("suite_duration_sec", 0),
                    "scope_mode": scope["mode"],
                    "selected_files": (sorted(selection)
                                       if selection else []),
                    "digest": "",
                }
                measure_entry["digest"] = suite_ledger._compute_digest(
                    measure_entry)
                ld = suite_ledger.ledger_dir(project)
                ld.mkdir(parents=True, exist_ok=True)
                entry_path = ld / f"{tree}.json"
                entry_path.write_text(
                    json.dumps(measure_entry, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                ledger_entry = measure_entry
                # Write output text.
                output_path = ld / f"{tree}.output.txt"
                output_path.write_text(suite_output_text, encoding="utf-8")
            except (OSError, PermissionError) as exc:
                print(f"WARNING: could not write ledger entry: {exc}",
                      file=sys.stderr)
        else:
            # prefer mode, no entry — fall through to run_suite.
            pass

    if ledger_entry is None:
        try:
            # Apply the scope, do not merely record it.
            selection = scope.get("selection") if scope.get("mode") == "scoped" else None
            # Compute the suite budget: explicit if --suite-timeout was passed,
            # measured from history otherwise.
            suite_budget, suite_budget_source = compute_suite_budget(
                project, args.suite_timeout)
            results = run_suite(project, invocation, suite_budget,
                                selection=selection)
            # Save raw suite output beside the record for failure diagnostics.
            suite_output_text = results.get("suite_output_text", "")
        except (TimeoutError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            reason = "timeout" if isinstance(exc, TimeoutError) else "no summary line"
            print(f"ILK-CHECK: unmeasured {reason}", file=sys.stderr)
            print(f"stub record left at {record}", file=sys.stderr)
            return 1

    suite_elapsed = round(time.monotonic() - suite_start)
    contention_end = measure_contention()
    at_base_start = time.monotonic()
    nodes = results["failing_nodes"]
    try:
        base_red = read_baseline_red_at(project, args.base_sha)
        head_red = read_baseline_red(project)
        # A failure surge is MEASURED at base, not presumed environmental.
        # run_at_base reruns every id in one pytest process, so the
        # measurement costs one rerun (66 s for 34 ids on gh-resolve,
        # 2026-10-06, where 30 of 34 passed at base: the batch's own
        # regressions, refused here before this change as "environment
        # fault").  Ids over AT_BASE_CAP still stop inside run_at_base.
        non_declared = [n for n in nodes
                        if not _in_baseline_red(n, base_red)]
        surge = len(non_declared) > FAILURE_SURGE_THRESHOLD
        # Try ledger at-base first when we have a ledger entry for HEAD.
        if ledger_entry is not None and ledger_mode != "off":
            import suite_ledger
            base_tree_sha = _git(project, "rev-parse",
                                 f"{args.base_sha}^{{tree}}")
            base_ledger = suite_ledger.lookup(
                project, base_tree_sha, invocation)
            if base_ledger:
                ledger_verdicts = ledger_at_base(
                    project, base_tree_sha, nodes, base_ledger,
                    baseline_red=base_red)
                if ledger_verdicts is not None:
                    at_base = ledger_verdicts
                    at_base_from_ledger = True
        if not at_base_from_ledger:
            at_base = run_at_base(project, args.base_sha, nodes, invocation,
                                  baseline_red=base_red)
        # Judgment call: a surge is an environment fault when at least half
        # of its non-declared ids also fail at base.  Basis: a broken
        # environment fails both trees, a regression fails only HEAD
        # (2026-10-06: 30 of 34 passed at base).  Wrong if a real surge
        # splits near half; then the message misleads but still fails closed.
        if surge:
            reproduced = [n for n in non_declared if at_base.get(n) == "failed"]
            if 2 * len(reproduced) >= len(non_declared):
                suite_output_path = record.parent / f"{record.stem}.output.txt"
                error_msg = (
                    f"{len(non_declared)} non-declared failures exceeds the "
                    f"{FAILURE_SURGE_THRESHOLD} surge cap and "
                    f"{len(reproduced)} of {len(non_declared)} also fail at "
                    f"base; environment fault, not a batch issue. "
                    f"First failing ids: {', '.join(reproduced[:5])}. "
                    f"Suite output: {suite_output_path}"
                )
                _atomic_write(record, render_record(
                    batch=args.batch or record.stem,
                    head=head, tree=tree, base_sha=args.base_sha,
                    invocation=invocation, scope=scope, results=results,
                    at_base={}, base_red=base_red, head_red=head_red,
                    at_base_error=error_msg,
                    suite_source="tool",
                ))
                print(f"ERROR: {error_msg}", file=sys.stderr)
                print("ILK-CHECK: unmeasured failure surge", file=sys.stderr)
                return 1
    except (ValueError, RuntimeError) as exc:
        if "exceeds the" in str(exc) and "cap" in str(exc):
            # Designed human-escalation: write the named stop, not the stub.
            _atomic_write(record, render_record(
                batch=args.batch or record.stem,
                head=head, tree=tree, base_sha=args.base_sha,
                invocation=invocation, scope=scope, results=results,
                at_base={}, base_red=base_red, head_red=head_red,
                at_base_error=str(exc),
                suite_source="tool",
            ))
            print(f"ERROR: at-base cap exceeded — named stop written to {record}",
                  file=sys.stderr)
            print("ILK-CHECK: unmeasured at-base cap exceeded", file=sys.stderr)
            return 1
        print(f"ERROR: at-base rerun could not run: {exc}", file=sys.stderr)
        print("ILK-CHECK: unmeasured at-base rerun failed", file=sys.stderr)
        print(f"stub record left at {record}", file=sys.stderr)
        return 1

    # Adding-commit rerun: for absent-at-base ids, re-run at the commit that
    # added their test file.  born-red-at:* = not attributed (the test was
    # already red when it was born).  This must run before HEAD reruns so that
    # born-red-at ids are excluded from the flaky classification path.
    adding_slugs: dict[str, str | None] = {}
    if registry_slugs:
        absent_ids = [nid for nid, v in at_base.items()
                      if v == "absent-at-base"]
        if absent_ids:
            try:
                adding_verdicts, adding_slugs = run_at_adding_commit(
                    project, args.base_sha, absent_ids, invocation,
                    registry_slugs)
                at_base.update(adding_verdicts)
            except (TimeoutError, subprocess.SubprocessError) as exc:
                print(f"WARNING: adding-commit rerun failed: {exc}",
                      file=sys.stderr)
                # Continue without adding-commit classification — the record
                # is still valid with plain absent-at-base verdicts.

    # Owner resolution: for ids whose at-base verdict is passed or
    # absent-at-base, determine which sub-plan owns the red.  A foreign
    # owner (not in registry_slugs) gets an owned-by: cell and is excluded
    # from head reruns.
    owners: dict[str, dict] = {}
    owned_by_ids: list[str] = []
    if registry_slugs:
        import suite_ledger
        owner_candidates = [nid for nid, v in at_base.items()
                            if v in ("passed", "absent-at-base")]
        for nid in owner_candidates:
            try:
                result = suite_ledger.owner_of(
                    project, nid,
                    base_sha=args.base_sha, head_sha=head,
                )
            except Exception as exc:
                print(f"WARNING: owner_of failed for {nid}: {exc}",
                      file=sys.stderr)
                result = {"slug": None, "sha": None, "how": "unknown"}
            owners[nid] = result
            slug = result.get("slug")
            sha = result.get("sha")
            how = result.get("how", "unknown")
            if slug and slug not in registry_slugs and sha:
                # Foreign owner — mark as owned-by and exclude from reruns.
                at_base[nid] = f"owned-by:{slug}@{sha[:12]}"
                owned_by_ids.append(nid)

    # Run HEAD reruns for non-declared failing nodes to classify flaky tests.
    # Declared-at-base rows (already in baseline_red) get — in head reruns
    # and batch touched file — no subprocess is spawned for them.
    # Red-at-base rows also get — ; their verdict is already pre-existing
    # and reruns cannot change it (classify_flaky returns pre-existing
    # for at_base == "failed" before reading rerun counts).
    at_base_elapsed = round(time.monotonic() - at_base_start)
    head_reruns_start = time.monotonic()
    non_declared = [nid for nid, v in at_base.items()
                    if v not in ("declared-at-base", "failed")
                    and nid not in owned_by_ids]
    declared = [nid for nid, v in at_base.items()
                if v == "declared-at-base"]
    failed_at_base_ids = [nid for nid, v in at_base.items()
                          if v == "failed"]
    head_reruns: dict[str, tuple[int, int]] = {}
    batch_touched: dict[str, bool] = {}
    flaky_owed: list[str] = []
    head_rerun_bound_hit = False
    # Mark declared, red-at-base, and owned-by rows with — (no rerun).
    declared_reruns: dict[str, str] = {nid: "—" for nid in declared}
    declared_touched: dict[str, str] = {nid: "—" for nid in declared}
    failed_reruns: dict[str, str] = {nid: "—" for nid in failed_at_base_ids}
    failed_touched: dict[str, str] = {nid: "—" for nid in failed_at_base_ids}
    owned_reruns: dict[str, str] = {nid: "—" for nid in owned_by_ids}
    owned_touched: dict[str, str] = {nid: "—" for nid in owned_by_ids}
    if non_declared:
        try:
            # Compute batch_touched BEFORE reruns so touched ids are excluded.
            batch_touched = batch_touched_files(project, args.base_sha,
                                                non_declared,
                                                batch_slugs=registry_slugs)
            untouched = [nid for nid in non_declared
                         if not batch_touched.get(nid, False)]
            head_reruns_raw, head_rerun_bound_hit = run_head_reruns(
                project, untouched, invocation)
            # Merge untouched results; touched ids get (0, 0).
            for nid in non_declared:
                if nid in head_reruns_raw:
                    head_reruns[nid] = head_reruns_raw[nid]
                else:
                    head_reruns[nid] = (0, 0)
            for nid in non_declared:
                red_count, runs = head_reruns.get(nid, (0, 0))
                cls = classify_flaky(
                    nid, at_base.get(nid, "failed"),
                    red_count, runs,
                    batch_touched.get(nid, False))
                if cls == "flaky-owed":
                    flaky_owed.append(nid)
        except (TimeoutError, subprocess.SubprocessError) as exc:
            print(f"WARNING: HEAD reruns could not run: {exc}", file=sys.stderr)
            # Continue without flaky classification — the record is still valid.

    # R3: determine attempt number and write history.
    history = _read_history(record)
    attempt = len(history) + 1

    # Save raw suite output beside the record for failure diagnostics.
    suite_output_file = record.with_suffix(".suite-output.txt")
    _atomic_write(suite_output_file, suite_output_text)

    # Merge declared-row markers (—) into the reruns/touched dicts so
    # render_record shows — for already-classified rows.
    all_reruns: dict = {**head_reruns, **declared_reruns, **failed_reruns,
                        **owned_reruns}
    all_touched: dict = {**batch_touched, **declared_touched, **failed_touched,
                         **owned_touched}

    # Suggest baseline_red entries for ids that failed at base but are not
    # already in baseline_red.  The script never edits .ilk-launch.json.
    failed_at_base_suggestions = [
        nid for nid in failed_at_base_ids
        if not _in_baseline_red(nid, base_red or [])
    ]

    # Compute ledger citation fields.
    if ledger_entry is not None:
        head_source = (f"ledger {ledger_entry['tree']} "
                       f"{ledger_entry['digest'][:16]}")
    else:
        head_source = "run"
    if at_base_from_ledger and base_ledger is not None:
        base_source = (f"ledger {base_ledger['tree']} "
                       f"{base_ledger['digest'][:16]}")
    else:
        base_source = "rerun"
    head_reruns_elapsed = round(time.monotonic() - head_reruns_start)
    record_elapsed_sec = round(time.monotonic() - record_start)
    phase_seconds = {
        "suite": suite_elapsed,
        "at_base": at_base_elapsed,
        "head_reruns": head_reruns_elapsed,
        "total": record_elapsed_sec,
    }
    contention = {"start": contention_start, "end": contention_end}

    record_text = render_record(
        batch=args.batch or record.stem,
        head=head, tree=tree, base_sha=args.base_sha,
        invocation=invocation, scope=scope, results=results,
        at_base=at_base, base_red=base_red, head_red=head_red,
        head_reruns=all_reruns or None,
        batch_touched=all_touched or None,
        flaky_owed=flaky_owed or None,
        adding_slugs=adding_slugs or None,
        failed_at_base=failed_at_base_suggestions or None,
        suite_duration_sec=results.get("suite_duration_sec"),
        suite_budget=(suite_budget, suite_budget_source),
        suite_output_path=str(suite_output_file),
        suite_output_text=suite_output_text,
        suite_source="tool",
        ledger_mode=ledger_mode,
        head_source=head_source,
        base_source=base_source,
        ledger_wait_sec=ledger_wait_sec if ledger_wait_sec else None,
        record_elapsed_sec=record_elapsed_sec,
        phase_seconds=phase_seconds,
        contention=contention,
        owners=owners or None,
        head_rerun_bound_hit=head_rerun_bound_hit,
    )
    # Inject attempt header after the batch line.
    record_text = record_text.replace(
        "record_writer:", f"attempt: {attempt}\nrecord_writer:", 1)
    _atomic_write(record, record_text)

    # SLO breach check: when total > VERIFY_SLO_S, file to improvement backlog.
    _check_slo_breach(args.batch or record.stem, head, record, phase_seconds)

    # R4: compute digest and append to history.
    # Use the raw suite failures (nodes), not the at-base classification.
    # A test that fails at HEAD but passes at base is an attributed regression
    # that must be carried forward if the next attempt "fixes" it.
    digest = _compute_record_digest(record_text)
    _append_history_entry(record, attempt, digest, nodes,
                          suite_duration_sec=results.get("suite_duration_sec"),
                          head=head, tree=tree, contention=contention)

    c = results["counts"]
    print(f"recorded: {c['passed']} passed, {c['failed']} failed, "
          f"{c['errors']} errors of {c['total']} · scope={scope['mode']} · "
          f"at-base rows={len(at_base)} → {record}")
    # Step 0 records; step 1 judges.  A red suite is not this command's failure.
    return 0


def _write_record_from_output(project: Path, record: Path, args,
                               registry_slugs: set[str] | None = None) -> int:
    """Write a record from a pre-existing suite output file (operator ingest).

    AC-1: reads the file through the same parser as --run-suite and writes
    the whole record, including at-base reruns.
    AC-2: all bindings refuse with a named reason.
    AC-3: invocation must match after normalisation.
    AC-4: provenance: suite_source: operator:<path> + sha256 digest.
    """
    suite_output_path = Path(args.from_suite_output).resolve()
    if not suite_output_path.is_file():
        print(f"ERROR: suite output file not found: {suite_output_path}",
              file=sys.stderr)
        return 1

    # ── AC-2a: --suite-head must equal HEAD ────────────────────────────────
    head = read_head_from_git(project)
    if head is None:
        print(f"ERROR: cannot read HEAD from git in {project}",
              file=sys.stderr)
        return 1
    if args.suite_head != head:
        print(f"ERROR: --suite-head {args.suite_head[:12]} does not match "
              f"HEAD {head[:12]}; the output must name the tree it measured",
              file=sys.stderr)
        return 1

    # ── AC-2b: worktree must be clean (tracked files) ─────────────────────
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=project, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        dirty_lines = [l for l in (status.stdout or "").splitlines()
                       if l.strip()]
        if dirty_lines:
            print(f"ERROR: worktree is dirty ({len(dirty_lines)} tracked "
                  f"files modified); the record must name a clean tree",
                  file=sys.stderr)
            return 1
    except (OSError, subprocess.SubprocessError):
        pass  # best-effort

    # ── AC-2c: file mtime >= HEAD's committer time ────────────────────────
    head_time = _head_committer_time(project)
    if head_time is not None:
        file_mtime = int(suite_output_path.stat().st_mtime)
        if file_mtime < head_time:
            print(f"ERROR: suite output file mtime ({file_mtime}) is before "
                  f"HEAD's committer time ({head_time}); the output must be "
                  f"from after the commit",
                  file=sys.stderr)
            return 1

    # ── AC-2d + AC-2e: parse the file ─────────────────────────────────────
    raw_output = suite_output_path.read_text(encoding="utf-8",
                                              errors="replace")
    # Strip ANSI escapes before checking for banners.
    clean_output = _ANSI_RE.sub("", raw_output)

    # AC-2e: "PARTIAL RUN" banner refuses.
    if "PARTIAL RUN" in clean_output:
        print("ERROR: suite output contains 'PARTIAL RUN' banner; "
              "refusing incomplete run",
              file=sys.stderr)
        return 1

    # AC-3: invocation must match after normalisation.
    configured_invocation = (args.suite_invocation or "").strip()
    if not configured_invocation:
        print("ERROR: --suite-invocation is required with --from-suite-output",
              file=sys.stderr)
        return 1

    # Check for selection-changing flags in the configured invocation.
    bad_flag = _check_selection_flags(configured_invocation)
    if bad_flag:
        print(f"ERROR: configured invocation contains selection-changing "
              f"flag {bad_flag!r}; refusing",
              file=sys.stderr)
        return 1

    # AC-2f: collected count below --collect-only count refuses.
    # Run --collect-only on the configured invocation to get the expected count.
    # Strip output-only flags so the command carries exactly one -q.
    stripped = _strip_output_only_flags(configured_invocation)
    collect_cmd = f"{stripped} --collect-only -q"
    collect_result = None
    try:
        collect_result = subprocess.run(
            collect_cmd, shell=True, cwd=project,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        collect_output = _ANSI_RE.sub("", collect_result.stdout or "")
        expected_count = parse_collect_count(collect_output)
    except (OSError, subprocess.SubprocessError):
        expected_count = None

    # None is a refusal: the collected-count floor cannot be checked.
    if expected_count is None:
        if collect_result is not None:
            raw = (collect_result.stdout or "") + (collect_result.stderr or "")
            snippet = _ANSI_RE.sub("", raw)[:300]
            rc = collect_result.returncode
        else:
            snippet = "(subprocess raised before producing output)"
            rc = "?"
        print(f"ERROR: cannot read the --collect-only count from "
              f"{collect_cmd!r} (exit {rc}): {snippet}; refusing, the "
              f"collected-count floor cannot be checked",
              file=sys.stderr)
        return 1

    # Parse the file through the same parser as --run-suite.
    parser = _parse_for(configured_invocation)
    try:
        parsed = parser(clean_output)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    actual_count = parsed["counts"]["total"]
    if expected_count is not None and actual_count < expected_count:
        print(f"ERROR: collected count {actual_count} is below the configured "
              f"suite's --collect-only count {expected_count}; refusing "
              f"incomplete run",
              file=sys.stderr)
        return 1

    # Build the results dict matching run_suite's shape.
    results = {
        **parsed,
        "exit_code": 0,  # not meaningful for file ingest
        "suite_duration_sec": None,
        "suite_output_text": raw_output,
    }

    tree = _git(project, "rev-parse", "HEAD^{tree}")
    if not tree:
        print(f"ERROR: cannot read tree from git in {project}", file=sys.stderr)
        return 1

    if head.startswith(args.base_sha) or args.base_sha.startswith(head):
        print(f"ERROR: base_sha equals HEAD ({head[:12]}); the comparison would "
              f"be HEAD against itself",
              file=sys.stderr)
        return 1

    scope = compute_suite_scope(project, args.base_sha)
    if getattr(args, "scope", "auto") == "full":
        if scope["mode"] == "full":
            scope["reason"] = f"override: --scope full; computed: {scope['reason']}"
        else:
            scope["reason"] = "override: --scope full"
        scope["mode"] = "full"
    record.parent.mkdir(parents=True, exist_ok=True)

    # At-base reruns.
    nodes = results["failing_nodes"]
    try:
        base_red = read_baseline_red_at(project, args.base_sha)
        head_red = read_baseline_red(project)
        # Failure surge stop: if too many non-declared failures, refuse
        # before any rerun — this is an environment fault, not a batch issue.
        non_declared = [n for n in nodes
                        if not _in_baseline_red(n, base_red)]
        if len(non_declared) > FAILURE_SURGE_THRESHOLD:
            first_ids = non_declared[:5]
            error_msg = (
                f"{len(non_declared)} non-declared failures exceeds the "
                f"{FAILURE_SURGE_THRESHOLD} surge cap; "
                f"environment fault, not a batch issue. "
                f"First failing ids: {', '.join(first_ids)}. "
                f"Suite output: {suite_output_path}"
            )
            _atomic_write(record, render_record(
                batch=args.batch or record.stem,
                head=head, tree=tree, base_sha=args.base_sha,
                invocation=configured_invocation, scope=scope,
                results=results,
                at_base={}, base_red=base_red, head_red=head_red,
                at_base_error=error_msg,
                suite_source=f"operator:{suite_output_path}",
                suite_source_sha256=hashlib.sha256(
                    suite_output_path.read_bytes()
                ).hexdigest(),
            ))
            print(f"ERROR: {error_msg}", file=sys.stderr)
            return 1
        at_base = run_at_base(project, args.base_sha, nodes,
                              configured_invocation,
                              baseline_red=base_red)
    except (ValueError, RuntimeError) as exc:
        if "exceeds the" in str(exc) and "cap" in str(exc):
            _atomic_write(record, render_record(
                batch=args.batch or record.stem,
                head=head, tree=tree, base_sha=args.base_sha,
                invocation=configured_invocation, scope=scope,
                results=results,
                at_base={}, base_red=[], head_red=[],
                at_base_error=str(exc),
                suite_source=f"operator:{suite_output_path}",
                suite_source_sha256=hashlib.sha256(
                    suite_output_path.read_bytes()
                ).hexdigest(),
            ))
            print(f"ERROR: at-base cap exceeded — named stop written to {record}",
                  file=sys.stderr)
            return 1
        print(f"ERROR: at-base rerun could not run: {exc}", file=sys.stderr)
        return 1

    # Adding-commit rerun.
    adding_slugs_map: dict[str, str | None] = {}
    if registry_slugs:
        absent_ids = [nid for nid, v in at_base.items()
                      if v == "absent-at-base"]
        if absent_ids:
            try:
                adding_verdicts, adding_slugs_map = run_at_adding_commit(
                    project, args.base_sha, absent_ids,
                    configured_invocation, registry_slugs)
                at_base.update(adding_verdicts)
            except (TimeoutError, subprocess.SubprocessError) as exc:
                print(f"WARNING: adding-commit rerun failed: {exc}",
                      file=sys.stderr)

    # HEAD reruns for non-declared failing nodes.
    non_declared = [nid for nid, v in at_base.items()
                    if v not in ("declared-at-base", "failed")]
    declared = [nid for nid, v in at_base.items()
                if v == "declared-at-base"]
    failed_at_base_ids = [nid for nid, v in at_base.items()
                          if v == "failed"]
    head_reruns: dict[str, tuple[int, int]] = {}
    batch_touched: dict[str, bool] = {}
    flaky_owed: list[str] = []
    head_rerun_bound_hit = False
    declared_reruns: dict[str, str] = {nid: "—" for nid in declared}
    declared_touched: dict[str, str] = {nid: "—" for nid in declared}
    failed_reruns: dict[str, str] = {nid: "—" for nid in failed_at_base_ids}
    failed_touched: dict[str, str] = {nid: "—" for nid in failed_at_base_ids}
    if non_declared:
        try:
            batch_touched = batch_touched_files(project, args.base_sha,
                                                non_declared,
                                                batch_slugs=registry_slugs)
            untouched = [nid for nid in non_declared
                         if not batch_touched.get(nid, False)]
            head_reruns_raw, head_rerun_bound_hit = run_head_reruns(
                project, untouched, configured_invocation)
            for nid in non_declared:
                if nid in head_reruns_raw:
                    head_reruns[nid] = head_reruns_raw[nid]
                else:
                    head_reruns[nid] = (0, 0)
            for nid in non_declared:
                red_count, runs = head_reruns.get(nid, (0, 0))
                cls = classify_flaky(
                    nid, at_base.get(nid, "failed"),
                    red_count, runs,
                    batch_touched.get(nid, False))
                if cls == "flaky-owed":
                    flaky_owed.append(nid)
        except (TimeoutError, subprocess.SubprocessError) as exc:
            print(f"WARNING: HEAD reruns could not run: {exc}", file=sys.stderr)

    # History.
    history = _read_history(record)
    attempt = len(history) + 1

    # Save raw suite output beside the record.
    suite_output_file = record.with_suffix(".suite-output.txt")
    _atomic_write(suite_output_file, raw_output)

    all_reruns: dict = {**head_reruns, **declared_reruns, **failed_reruns}
    all_touched: dict = {**batch_touched, **declared_touched, **failed_touched}

    failed_at_base_suggestions = [
        nid for nid in failed_at_base_ids
        if not _in_baseline_red(nid, base_red or [])
    ]

    # AC-4: provenance.
    suite_source = f"operator:{suite_output_path}"
    suite_source_sha256 = hashlib.sha256(
        suite_output_path.read_bytes()
    ).hexdigest()

    record_text = render_record(
        batch=args.batch or record.stem,
        head=head, tree=tree, base_sha=args.base_sha,
        invocation=configured_invocation, scope=scope, results=results,
        at_base=at_base, base_red=base_red, head_red=head_red,
        head_reruns=all_reruns or None,
        batch_touched=all_touched or None,
        flaky_owed=flaky_owed or None,
        adding_slugs=adding_slugs_map or None,
        failed_at_base=failed_at_base_suggestions or None,
        suite_output_path=str(suite_output_file),
        suite_output_text=raw_output,
        suite_source=suite_source,
        suite_source_sha256=suite_source_sha256,
        head_rerun_bound_hit=head_rerun_bound_hit,
    )
    record_text = record_text.replace(
        "record_writer:", f"attempt: {attempt}\nrecord_writer:", 1)
    _atomic_write(record, record_text)

    digest = _compute_record_digest(record_text)
    _append_history_entry(record, attempt, digest, nodes,
                          head=head, tree=tree)

    c = results["counts"]
    print(f"recorded: {c['passed']} passed, {c['failed']} failed, "
          f"{c['errors']} errors of {c['total']} · scope={scope['mode']} · "
          f"at-base rows={len(at_base)} · source=operator:{suite_output_path} "
          f"→ {record}")
    return 0


def _extract_registry_slugs(master_path: Path) -> list[str]:
    """Extract plan slugs from the MASTER's sub-plan registry table.

    Returns a list of slugs (e.g. ``["alpha", "beta"]``) extracted from
    sub-plan filenames like ``2026-09-29-alpha.md``.
    """
    master_text = master_path.read_text(encoding="utf-8-sig", errors="replace")
    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from plan_preflight import _extract_table_filenames
    filenames = _extract_table_filenames(master_text)
    slug_re = re.compile(r"^\d{4}-\d{2}-\d{2}[a-z]?-(.+?)\.md$")
    slugs: list[str] = []
    for fn in filenames:
        m = slug_re.match(fn)
        if m:
            slugs.append(m.group(1))
        else:
            slugs.append(fn.removesuffix(".md"))
    return slugs


def resolve_batch_base_sha(
    project: Path,
    master_path: Path,
    *,
    explicit_sha: str | None = None,
) -> tuple[str, str]:
    """Derive the batch base from the MASTER's registry slugs.

    Returns ``(base_sha, provenance)`` where *provenance* is a human-readable
    string like ``base_sha: abc1234 (derived from def5678 [plan:alpha#…])``.

    When *explicit_sha* is not ``None`` and not ``"auto"``, it is returned
    unchanged (pass-through for explicit ``--base-sha <sha>``).

    Raises ``SystemExit`` with a non-zero code when:
    * ``explicit_sha == "auto"`` but no ``--master`` was supplied.
    * No commit matches any registry slug (the operator must pass an
      explicit sha).
    """
    if explicit_sha is not None and explicit_sha != "auto":
        return explicit_sha, f"base_sha: {explicit_sha} (explicit)"

    slugs = _extract_registry_slugs(master_path)
    if not slugs:
        print(f"ERROR: no sub-plan filenames found in {master_path.name}",
              file=sys.stderr)
        raise SystemExit(1)

    # Build a regex alternation for git log --grep.
    escaped = [re.escape(s) for s in slugs]
    grep_pattern = r"\[plan:(" + "|".join(escaped) + r")#"

    # Find the earliest commit whose message matches any slug.
    try:
        log_out = subprocess.run(
            ["git", "log", "--reverse", "--format=%H", "-E",
             f"--grep={grep_pattern}", "HEAD"],
            cwd=project,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: git log failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    first_line = (log_out.stdout or "").strip().splitlines()
    if not first_line:
        searched = ", ".join(slugs)
        print(f"ERROR: no commit matches any registry slug [{searched}]. "
              f"Pass an explicit --base-sha.", file=sys.stderr)
        raise SystemExit(1)

    first_sha = first_line[0]

    # The base is the parent of that commit.
    try:
        parent = subprocess.run(
            ["git", "rev-parse", f"{first_sha}^"],
            cwd=project,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
        base_sha = parent.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: git rev-parse failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if not base_sha:
        print(f"ERROR: could not resolve parent of {first_sha[:12]}",
              file=sys.stderr)
        raise SystemExit(1)

    provenance = (f"base_sha: {base_sha} "
                  f"(derived from {first_sha[:8]} [plan:{slugs[0]}#…])")
    return base_sha, provenance


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
        "--suite-timeout", type=int, default=None, metavar="SEC",
        help="bound on the suite run (default: measured from history, 1800 if no history)",
    )
    ap.add_argument(
        "--scope", default="auto", choices=("full", "auto"),
        metavar="MODE",
        help="override suite scope: 'full' forces the whole suite even when "
             "compute_suite_scope returns scoped; 'auto' (default) uses the "
             "computed scope.",
    )
    ap.add_argument(
        "--master", default=None, metavar="PATH",
        help="path to the MASTER plan (.md); required when --base-sha is 'auto'",
    )
    ap.add_argument(
        "--ledger", default="prefer", choices=("prefer", "require", "off"),
        metavar="MODE",
        help="control ledger lookups for head and base: 'prefer' uses the "
             "ledger when an entry exists, 'require' measures in-process if "
             "no entry is found (and refuses head_source: run), 'off' runs "
             "the suite directly (today's behaviour).",
    )
    ap.add_argument(
        "--from-suite-output", default=None, metavar="FILE",
        help="write the record from a pre-existing suite output file "
             "(mutually exclusive with --run-suite)",
    )
    ap.add_argument(
        "--suite-invocation", default=None, metavar="CMD",
        help="the configured suite invocation (required with --from-suite-output)",
    )
    ap.add_argument(
        "--suite-head", default=None, metavar="SHA",
        help="the HEAD sha the suite ran on (must match current HEAD; "
             "required with --from-suite-output)",
    )
    args = ap.parse_args(argv)

    # Mutual exclusivity: --run-suite and --from-suite-output cannot coexist.
    if args.run_suite and args.from_suite_output:
        print("ERROR: --run-suite and --from-suite-output are mutually exclusive",
              file=sys.stderr)
        return 2

    # Resolve --base-sha auto before any code path reads args.base_sha.
    if args.base_sha == "auto":
        if not args.master:
            print("ERROR: --base-sha auto requires --master",
                  file=sys.stderr)
            return 2
        master_path = Path(args.master).resolve()
        if not master_path.is_file():
            print(f"ERROR: master plan not found: {master_path}",
                  file=sys.stderr)
            return 1
        project_for_resolve = Path(args.project).resolve()
        try:
            resolved_sha, provenance = resolve_batch_base_sha(
                project_for_resolve, master_path,
            )
        except SystemExit as exc:
            return exc.code
        args.base_sha = resolved_sha
        print(provenance)

    # Extract registry slugs from --master for the adding-commit rerun.
    # Available when --master is provided (required for --base-sha auto,
    # optional otherwise).
    registry_slugs: set[str] = set()
    if args.master:
        master_for_slugs = Path(args.master).resolve()
        if master_for_slugs.is_file():
            registry_slugs = set(_extract_registry_slugs(master_for_slugs))

    project = Path(args.project).resolve()

    if bool(args.record) == bool(args.batch):
        print(
            "ERROR: pass exactly one of --record or --batch",
            file=sys.stderr,
        )
        return 2

    if args.batch:
        if args.run_suite or args.from_suite_output:
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
        return _write_measured_record(project, record, args,
                                      registry_slugs=registry_slugs)

    if args.from_suite_output:
        return _write_record_from_output(project, record, args,
                                         registry_slugs=registry_slugs)

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
