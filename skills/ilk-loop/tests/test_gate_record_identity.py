"""Red-first: a gate record carries the identity of the target the runner chose.

Field record — a gh-resolve resolver run (kira-cloudflare launcher
``20260902-183120``) did its work correctly, then stopped itself on a
bookkeeping check.  The gate errored, and the driver printed::

    [local_checks ERR] 0 step  -> error

``0 step`` — a sub-plan named "0" does not exist.  Because the loop stopped,
the batch's second sub-plan never ran, the registry stayed ``pending``, and
gh-resolve's ``reap`` refused the run (``refused:plan-not-shipped``).

Mechanism, reproduced byte-exactly on 2026-09-03 against HEAD ``21e846d``
(sub-plan ``a-gate-result-carries-its-identity``)::

    record:            {"slug":"","step":null,"outcome":"error","exit_code":1}
    --targets output:  " 0"            (leading space; _step_of maps None -> 0)
    read -r slug step: slug="0" step=""
    driver printed:    [local_checks ERR] 0 step  -> error

``invoke_local_checks`` knows the target it is running — ``$slug`` and
``$step`` — but passes neither to the writer
(``run_ilk_loop_claude.sh:1195-1196``), so ``emit_jsonl_record.py:101-102``
takes identity from the *checked process's own stdout*, which is absent
exactly when the gate failed.

These tests pin the fix's contract: identity flows from the invoker (AC-1,
AC-2), the reader never invents a target it cannot name (AC-3), the field
scenario end to end (AC-4), and an unattributable record does not block
(AC-7's ``--any`` half — its loud-warning half is
``test_driver_reports_unattributable_results`` below, landed with Change 4
in step 2: a step-0 test asserting Change-4 output could never have
satisfied step 1's plain-green gate).

AC-1..AC-4, AC-7 of sub-plan ``a-gate-result-carries-its-identity``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Union


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
EMIT = SCRIPTS / "emit_jsonl_record.py"
BLOCKING_CHECKS = SCRIPTS / "blocking_checks.py"
DRIVER = SCRIPTS / "run_ilk_loop_claude.sh"

#: The sub-plan and step the field run's gate was actually judging — the
#: identity the invoker held and the writer dropped.
FIELD_SLUG = "issue-4383-work-2998a2db"
FIELD_STEP = 0

#: The anonymous record from the field log, byte for byte.
ANON_RECORD = '{"slug":"","step":null,"outcome":"error","exit_code":1}'


# ── helpers ──────────────────────────────────────────────────────────────────

def _run_local_checks_payload(slug: str, step: int) -> dict:
    """A ``run_local_checks.py``-shaped payload for one FAILING check.

    Field names copied from ``test_gate_record_format_contract.py`` so both
    identity tests and format tests feed the writer the same shape.
    """
    return {
        "slug": slug,
        "step": step,
        "subplan_path": f"/plans/{slug}.md",
        "run_cwd": "/project",
        "subplan_check_count": 0,
        "step_check_count": 1,
        "all_passed": False,
        "results": [{
            "command": "bunx vitest run --silent",
            "scope": "step",
            "timeout": 900,
            "exit_code": 1,
            "duration_sec": 12.5,
            "passed": False,
            "stdout_tail": "3 failed | 818 passed",
            "stderr_tail": "",
            "error": "",
        }],
    }


def _emit(
    tmp_path: Path,
    helper_out: Union[dict, str, None],
    outcome: str = "error",
    check_exit: int = 1,
    *identity: str,
) -> Path:
    """Run the REAL writer and return the results file it appended to.

    ``helper_out`` is what the checked process left in ``tmp_out``: a dict
    (serialised as its JSON), a raw string (unparsable), or None (empty
    file — the gate emitted nothing).  ``*identity`` is the optional trailing
    ``<slug> <step>`` the fixed driver appends.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_out = tmp_path / "run_local_checks.out.json"
    if helper_out is None:
        tmp_out.write_text("", encoding="utf-8")
    elif isinstance(helper_out, dict):
        tmp_out.write_text(json.dumps(helper_out, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    else:
        tmp_out.write_text(helper_out, encoding="utf-8")
    results = tmp_path / "local_checks_results.jsonl"
    proc = subprocess.run(
        [sys.executable, str(EMIT), str(results), str(tmp_out), outcome,
         str(check_exit), *identity],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"emit_jsonl_record.py exited {proc.returncode}: {proc.stderr}"
    )
    assert results.is_file() and results.stat().st_size > 0, (
        "the writer produced no record at all"
    )
    return results


def _sole_record(results: Path) -> dict:
    lines = [l for l in results.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1, f"expected exactly 1 record, got {len(lines)}: {lines}"
    return json.loads(lines[0])


def _anon_results(tmp_path: Path) -> Path:
    """A results file holding exactly the field's anonymous record, written
    through the real writer's back-compat path (no identity args, empty
    helper output) — so the reader test stays honest after the writer is
    fixed: an anonymous record can still exist, and the reader must still
    refuse to invent a target for it."""
    return _emit(tmp_path, None, outcome="error", check_exit=1)


def _blocking(results: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BLOCKING_CHECKS), str(results), *args],
        capture_output=True, text=True, timeout=60,
    )


# ── AC-1: explicit identity survives a helper that said nothing ─────────────

def test_explicit_identity_survives_empty_helper_output(tmp_path: Path) -> None:
    """AC-1 — the invoker's slug/step land in the record even when the gate
    emitted no parsable JSON (empty file, or a traceback).  This is the
    exact production failure shape: ``outcome:"error"`` correlates with the
    helper's stdout being unusable, which is why identity can never come
    from there."""
    # Empty tmp_out — the gate produced no output at all.
    results = _emit(tmp_path / "empty", None, "error", 1, "slug-a", "3")
    rec = _sole_record(results)
    assert rec["slug"] == "slug-a", (
        f"record lost the invoker's slug: {rec['slug']!r} (want 'slug-a')"
    )
    assert rec["step"] == 3, f"record lost the invoker's step: {rec['step']!r}"
    assert rec["outcome"] == "error" and rec["exit_code"] == 1, rec

    # Unparsable tmp_out — a traceback where JSON was expected.
    traceback = (
        "Traceback (most recent call last):\n"
        "  File \"run_local_checks.py\", line 1, in <module>\n"
        "json.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n"
    )
    results = _emit(tmp_path / "unparsable", traceback, "error", 1, "slug-b", "7")
    rec = _sole_record(results)
    assert rec["slug"] == "slug-b", (
        f"record lost the invoker's slug: {rec['slug']!r} (want 'slug-b')"
    )
    assert rec["step"] == 7, f"record lost the invoker's step: {rec['step']!r}"


# ── AC-2: the invoker is authoritative; a blank only fills a blank ──────────

def test_explicit_identity_overrides_helper_stdout(tmp_path: Path) -> None:
    """AC-2 — an explicit slug/step overrides a *different* slug in the
    helper's stdout; a blank explicit slug falls back to the helper's."""
    # The helper claims another target: the invoker still wins.
    results = _emit(tmp_path / "override", _run_local_checks_payload("helper-slug", 9),
                    "fail", 1, "invoker-slug", "4")
    rec = _sole_record(results)
    assert rec["slug"] == "invoker-slug", (
        f"helper stdout overrode the invoker: {rec['slug']!r} "
        "(identity must be authoritative from the invoker)"
    )
    assert rec["step"] == 4, f"explicit step not honoured: {rec['step']!r}"

    # A blank explicit slug fills from the helper rather than blanking it.
    results = _emit(tmp_path / "fallback", _run_local_checks_payload("helper-slug", 9),
                    "fail", 1, "", "9")
    rec = _sole_record(results)
    assert rec["slug"] == "helper-slug", (
        f"a blank explicit slug blanked the record: {rec['slug']!r} "
        "(blank means fall back, not anonymise)"
    )


# ── AC-3: the reader emits no target it cannot name ─────────────────────────

def test_targets_never_emits_an_anonymous_line(tmp_path: Path) -> None:
    """AC-3 — ``--targets`` (the B2 confirm re-run's input) must never emit
    a line for an anonymous record.  Asserted on the raw bytes: the field
    failure was the line ``" 0"`` — leading space, no slug — which
    ``read -r slug step`` turns into the phantom target ``slug="0"``."""
    # Anonymous-only file: no target lines at all.
    results = _anon_results(tmp_path / "anon-only")
    proc = _blocking(results, "--targets")
    assert proc.returncode == 0, f"--targets errored: {proc.stderr}"
    assert proc.stdout.strip() == "", (
        f"--targets invented a target for an anonymous record: {proc.stdout!r}"
    )
    for line in proc.stdout.splitlines():
        assert not line.startswith(" "), (
            f"--targets emitted a line with an empty slug: {line!r}"
        )
    assert " 0" not in proc.stdout, (
        f"--targets emitted the phantom anonymous target: {proc.stdout!r}"
    )

    # Mixed file: the attributable record survives, the anonymous one is
    # skipped — skipping, not aborting.
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    results = _anon_results(mixed)
    results = _emit(mixed, _run_local_checks_payload("real-slug", 2), "fail", 1)
    proc = _blocking(results, "--targets")
    assert proc.stdout.splitlines() == ["real-slug 2"], (
        f"--targets on a mixed file must emit only attributable records; "
        f"got {proc.stdout!r}"
    )


# ── AC-4: the field scenario, end to end ────────────────────────────────────

def test_field_scenario_end_to_end(tmp_path: Path) -> None:
    """AC-4 — the exact observed chain: a gate that errors with no parsable
    JSON while the invoker holds a known slug/step.  The record must carry
    that identity, ``--describe`` must name the real sub-plan for the
    operator, and ``--slugs`` must yield it so auto-quarantine has a target
    (its emptiness is why the field run stopped instead of quarantining)."""
    traceback = (
        "Traceback (most recent call last):\n"
        "  File \"run_local_checks.py\", line 583, in <module>\n"
        "    main()\n"
        "json.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n"
    )
    results = _emit(tmp_path, traceback, "error", 1, FIELD_SLUG, str(FIELD_STEP))

    rec = _sole_record(results)
    assert rec["slug"] == FIELD_SLUG, (
        f"the field scenario still loses the sub-plan: {rec['slug']!r}"
    )
    assert rec["step"] == FIELD_STEP, f"step lost: {rec['step']!r}"
    assert rec["outcome"] == "error" and rec["exit_code"] == 1, rec

    describe = _blocking(results, "--describe")
    assert f"{FIELD_SLUG}#{FIELD_STEP}" in describe.stdout, (
        "--describe must name the real sub-plan for the operator (the field "
        f"log showed only '#0'); got {describe.stdout!r}"
    )

    slugs = _blocking(results, "--slugs")
    assert slugs.stdout.split() == [FIELD_SLUG], (
        "--slugs must yield the sub-plan so auto-quarantine has a target "
        f"(empty is why the field run stopped instead); got {slugs.stdout!r}"
    )


# ── AC-7 (first half): an anonymous record is not a verdict ─────────────────

def test_anonymous_record_does_not_block(tmp_path: Path) -> None:
    """AC-7 — with an anonymous record present and no attributable one,
    ``--any`` exits non-zero: it is not a verdict about any sub-plan, so it
    must not drive the driver into B2 (whose confirm re-run re-matches the
    anonymous key against itself and can never clear it).

    The second half of AC-7 — the driver's loud unattributable warning — is
    asserted by ``test_driver_reports_unattributable_results`` below: it is
    Change 4's output, and a step-0 test asserting it would have left
    step 1's plain-green gate unsatisfiable."""
    results = _anon_results(tmp_path)
    proc = _blocking(results, "--any")
    assert proc.returncode != 0, (
        "--any treats an anonymous record as blocking: a result with no "
        "sub-plan identity is not a verdict about any sub-plan, and B2's "
        "confirm re-run matches it against itself (deterministically "
        "blocking) instead of quarantining anything"
    )


# ── AC-7 (second half): the driver reports the unattributable, loudly ───────

def _b2_guard_region() -> str:
    """The driver's B2 guard, verbatim: from the
    `local blocking_checks_script=` declaration through the 6-space `fi`
    that closes the results-file `[[ -s ]]` check (deeper closes are
    indented further, so the first exact `      fi` after the declaration
    is the right one)."""
    lines = DRIVER.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, l in enumerate(lines) if "local blocking_checks_script=" in l),
        None,
    )
    assert start is not None, (
        "driver no longer declares blocking_checks_script — update this test"
    )
    region: list[str] = []
    for line in lines[start:]:
        region.append(line)
        if line == "      fi":
            return "\n".join(region)
    raise AssertionError("B2 guard block never closes at '      fi' — update this test")


def test_driver_reports_unattributable_results(tmp_path: Path) -> None:
    """AC-7 — with an anonymous record present and no attributable one, the
    driver's B2 guard takes the `--any`-false path, prints the
    unattributable warning to stderr, and does NOT stop the loop. The guard
    region is extracted verbatim and executed against an anonymous-only
    results file (the same dot-source pattern test_driver_wiring.py uses):
    at HEAD this printed nothing while stopping the batch — the loud report
    is the operator-visible half of the downgrade."""
    results = _anon_results(tmp_path)
    script = "\n".join([
        f'source "{DRIVER}"',
        "set +e",
        "probe() {",
        f'  local local_checks_results="{results}"',
        _b2_guard_region(),
        "}",
        "probe",
        'printf "STOP_REASON=[%s]\\n" "${iter_stop_reason:-}"',
    ])
    env = {
        **os.environ,
        "ILK_DOTSOURCE_ONLY": "1",
        "ILK_SKILL_HOME": str(SCRIPTS.parent.parent),
    }
    proc = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"probe rc={proc.returncode}; stderr: {proc.stderr.strip()}"
    )
    assert "carried no sub-plan identity" in proc.stderr, (
        "the driver stayed silent about an unattributable gate result — the "
        "downgrade to non-blocking is only safe while the harness defect is "
        f"loud. stderr: {proc.stderr!r}"
    )
    assert "1 gate result(s)" in proc.stderr, proc.stderr
    assert "local_checks_failed" not in proc.stderr, (
        "the unattributable record still stopped the loop"
    )
    assert "STOP_REASON=[]" in proc.stdout, (
        f"iter_stop_reason was set on an unattributable-only file: {proc.stdout!r}"
    )
