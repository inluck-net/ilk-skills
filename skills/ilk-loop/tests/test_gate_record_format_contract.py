"""Red-first: a failing gate record written by the writer must read as blocking.

Field record — kira-cloudflare run ``20260828-211346``.  The driver log's two
consecutive lines:

    478:  [local_checks FAIL] issue-sync-schema-widen step 2 -> fail  cmd: bunx vitest run ...
    480: === Loop ended: all-shipped ===

Nothing between them.  The gate ran, printed FAIL on 3 of 3 iterations, and
changed nothing.

Cause — a format contract broken by a refactor:

===============================  ============================================
``emit_jsonl_record.py:110``     ``json.dumps(rec, ensure_ascii=False)`` →
                                 ``{"slug": "…", "outcome": "fail"}``
                                 (a space after every colon)
``run_ilk_loop_claude.sh``       ``grep -qE '"outcome":"(error|fail)"'`` at
``:2091, :2095, :2164, :2175``   — the pattern forbids that space
===============================  ============================================

Reproduced 2026-08-29::

    $ python3 -c "import json; print(json.dumps({'outcome':'fail'}))" > /tmp/lc.jsonl
    $ grep -qE '"outcome":"(error|fail)"' /tmp/lc.jsonl; echo $?
    1

``emit_jsonl_record.py``'s docstring says it "replaces the hand-interpolated
echo in run_ilk_loop_claude.sh:1022".  That echo wrote ``\"outcome\":\"$outcome\"``
— compact, matching ``detached-component-contracts.md:120``.  The refactor
changed the format and left four readers behind.

Why no existing test caught it: every fixture in ``test_ship_audit.py``
(:85, :109, :132, :615) is hand-written *compact* JSON.  The fixtures encode
what the readers want, not what the writer emits.  These two tests close that
gap — the first routes through the real writer, the second pins the compact
form so the fix cannot be "make the writer compact and leave the grep".

AC-1, AC-2 of sub-plan ``a-red-gate-cannot-ship-a-subplan``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
EMIT = SCRIPTS / "emit_jsonl_record.py"
#: The single blocking-detection path the runner delegates to (step 1 adds it).
BLOCKING_CHECKS = SCRIPTS / "blocking_checks.py"


# ── helpers ──────────────────────────────────────────────────────────────────

def _run_local_checks_payload(slug: str, step: int) -> dict:
    """A ``run_local_checks.py``-shaped payload for one FAILING check.

    Field names copied from ``run_local_checks.py:572-582`` and the
    ``CheckResult`` dataclass at ``:257-267`` so the writer sees exactly what
    it sees in production.
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


def _emit(tmp_path: Path, payload: dict, outcome: str = "fail",
          check_exit: int = 1) -> Path:
    """Run the REAL writer and return the results file it appended to."""
    tmp_out = tmp_path / "run_local_checks.out.json"
    tmp_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    results = tmp_path / "local_checks_results.jsonl"
    proc = subprocess.run(
        [sys.executable, str(EMIT), str(results), str(tmp_out), outcome,
         str(check_exit)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, (
        f"emit_jsonl_record.py exited {proc.returncode}: {proc.stderr}"
    )
    assert results.is_file() and results.stat().st_size > 0, (
        "the writer produced no record at all"
    )
    return results


def _blocking(results: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke the runner's blocking-detection path on a results file."""
    assert BLOCKING_CHECKS.is_file(), (
        f"{BLOCKING_CHECKS.name} does not exist. The runner's blocking "
        "detection is four inline `grep -qE '\"outcome\":\"(error|fail)\"'` "
        "calls (run_ilk_loop_claude.sh:2091,2095,2164,2175) that cannot see a "
        "json.dumps-formatted record. Step 1 replaces them with one JSON "
        "parser exposed here."
    )
    return subprocess.run(
        [sys.executable, str(BLOCKING_CHECKS), str(results), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


# ── AC-1: the writer's own output reads as blocking ─────────────────────────

def test_writer_output_is_recognised_as_blocking(tmp_path: Path) -> None:
    """AC-1 — routed through the real writer, not a hand-made fixture.

    This is the exact production path: run_local_checks.py output →
    emit_jsonl_record.py → the runner's blocking detection.
    """
    results = _emit(tmp_path, _run_local_checks_payload("issue-sync-schema-widen", 2))

    # Bank the format the writer actually produces, so a future refactor that
    # re-breaks it fails HERE with a readable diff rather than in a live run.
    raw = results.read_text(encoding="utf-8").strip()
    rec = json.loads(raw)
    assert rec["outcome"] == "fail", f"writer recorded {rec['outcome']!r}"

    any_proc = _blocking(results, "--any")
    assert any_proc.returncode == 0, (
        "the runner's blocking detection did NOT flag a failing record "
        f"written by its own writer.\n  record: {raw}\n"
        f"  stderr: {any_proc.stderr}"
    )

    targets = _blocking(results, "--targets")
    assert targets.stdout.split() == ["issue-sync-schema-widen", "2"], (
        "--targets must emit `slug step` per blocking record (it feeds the B2 "
        f"confirm re-run at run_ilk_loop_claude.sh:2095); got {targets.stdout!r}"
    )

    describe = _blocking(results, "--describe")
    assert "issue-sync-schema-widen#2" in describe.stdout, (
        "--describe feeds the human-readable failing-check line at :2175; "
        f"got {describe.stdout!r}"
    )


# ── AC-2: separator style and key order are irrelevant ──────────────────────

def test_blocking_detection_parses_json_not_text(tmp_path: Path) -> None:
    """AC-2 — the reader parses JSON, so no serialisation style can hide a red gate.

    ``compact`` pins the documented contract
    (``detached-component-contracts.md:120``) so the fix cannot be "make the
    writer compact and leave the grep in place"; ``spaced`` is what
    ``json.dumps`` emits today; ``reordered`` is what any future writer using a
    different key order would emit.  One test, three styles: the file's
    contract is "format is irrelevant", and that is a single claim.
    """
    styles = {
        "compact": '{"slug":"a-slug","step":2,"outcome":"fail","exit_code":1}',
        "spaced": '{"slug": "a-slug", "step": 2, "outcome": "fail", "exit_code": 1}',
        "reordered": '{"outcome": "error", "exit_code": 124, "step": 2, "slug": "a-slug"}',
    }
    for style, raw in styles.items():
        results = tmp_path / f"{style}.jsonl"
        results.write_text(raw + "\n", encoding="utf-8")

        proc = _blocking(results, "--any")
        assert proc.returncode == 0, (
            f"a {style} blocking record was not recognised — the reader is "
            f"still pattern-matching serialised text.\n  record: {raw}\n"
            f"  stderr: {proc.stderr}"
        )

        slugs = _blocking(results, "--slugs")
        assert slugs.stdout.split() == ["a-slug"], (
            f"[{style}] --slugs feeds auto-quarantine at "
            f"run_ilk_loop_claude.sh:2164; got {slugs.stdout!r}"
        )


# ── AC-6: the slugless path the payload helper structurally could not make ──

def test_slugless_errored_gate_is_reported_unattributable(tmp_path: Path) -> None:
    """AC-6 — every fixture above is built through
    ``_run_local_checks_payload(slug, step)``, which always carries a slug.
    The record that actually fires in the field does not: a gate that errors
    emits no parsable JSON, so the writer's back-compat path produces the
    anonymous record (gh-resolve resolver run, 20260902-183120). The reader
    must REPORT it, and must never turn it into a target or a verdict."""
    tmp_out = tmp_path / "empty.out"
    tmp_out.write_text("", encoding="utf-8")
    results = tmp_path / "local_checks_results.jsonl"
    proc = subprocess.run(
        [sys.executable, str(EMIT), str(results), str(tmp_out), "error", "1"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    rec = json.loads(results.read_text(encoding="utf-8").strip())
    assert rec == {"slug": "", "step": None, "outcome": "error", "exit_code": 1}, (
        f"the anonymous record's shape drifted: {rec}"
    )

    targets = _blocking(results, "--targets")
    assert targets.stdout == "", (
        f"an anonymous record became a B2 target: {targets.stdout!r}"
    )

    describe = _blocking(results, "--describe")
    assert describe.stdout.strip() == "1 unattributable (no slug)", (
        "--describe must reach the operator with the unattributable count "
        f"(the field log showed only the useless '#0'); got {describe.stdout!r}"
    )

    count = _blocking(results, "--unattributable-count")
    assert count.stdout.strip() == "1", (
        f"--unattributable-count (the driver's warning input): {count.stdout!r}"
    )


def test_describe_reports_unattributable_beside_attributable(tmp_path: Path) -> None:
    """AC-6 — on a mixed file the attributable verdict keeps its name and the
    anonymous records are reported separately, not folded into the list."""
    results = tmp_path / "local_checks_results.jsonl"
    payload = _run_local_checks_payload("real-slug", 2)
    tmp_out = tmp_path / "run_local_checks.out.json"
    tmp_out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(EMIT), str(results), str(tmp_out), "fail", "1"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    anon_out = tmp_path / "anon.out"
    anon_out.write_text("", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(EMIT), str(results), str(anon_out), "error", "1"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    describe = _blocking(results, "--describe")
    assert describe.stdout.strip() == (
        "real-slug#2, 1 unattributable (no slug)"
    ), f"mixed-file --describe: {describe.stdout!r}"
