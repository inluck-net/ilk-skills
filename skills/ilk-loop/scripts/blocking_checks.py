#!/usr/bin/env python3
"""The one reader of a local_checks results file. Parses JSON; never greps it.

``run_ilk_loop_claude.sh`` used to ask four separate ``grep -qE
'"outcome":"(error|fail)"'`` calls (``:2091``, ``:2095``, ``:2164``, ``:2175``)
whether a gate had failed.  ``emit_jsonl_record.py`` writes those records with
``json.dumps``, which puts a space after every colon, so the pattern never
matched and the entire B2 block — confirm-re-run, auto-quarantine,
``iter_stop_reason="local_checks_failed"`` — was dead code.  Field record:
kira-cloudflare run ``20260828-211346``, 3 iterations, gate FAIL on all three,
``=== Loop ended: all-shipped ===``.

A pattern over serialised text is the wrong tool for a JSON contract: it
encodes a *formatting* choice as a *semantic* one, and the two drift silently.
This module parses each line and asks about fields, so separator style, key
order and whitespace cannot hide a red gate.

See ``references/detached-component-contracts.md`` — "local_checks results
file".  Record shape is ``emit_jsonl_record.build_record``.

Usage::

    blocking_checks.py <results-file> --any        # exit 0 iff any attributable record blocks
    blocking_checks.py <results-file> --targets    # "<slug> <step>" per line
    blocking_checks.py <results-file> --slugs      # unique slugs, sorted
    blocking_checks.py <results-file> --describe   # "slug#step, slug#step"
    blocking_checks.py <results-file> --unattributable-count
    blocking_checks.py <results-file> --confirm <rerun-file>   # B2: record both attempts

Unattributable records — Contract 2b invariant 6: a record's identity must
come from the invoker, so a blocking record with no ``slug`` names no
sub-plan.  It is not a verdict about anything: it does not block, and no
mode invents a target for it (``--targets`` emitting ``" 0"`` for one is
what turned a gate error into the phantom B2 target ``slug="0"`` —
gh-resolve resolver run, kira-cloudflare launcher ``20260902-183120``).
``--describe`` and ``--unattributable-count`` report them so the harness
defect stays loud.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

#: An outcome that must stop the run.  ``inconclusive`` is deliberately absent:
#: B2's confirm-before-block policy only re-runs what claims to have failed.
BLOCKING_OUTCOMES = ("fail", "error")


def read_records(path: Path) -> list[dict[str, Any]]:
    """Every well-formed JSON object in a local_checks results file.

    A malformed line is skipped rather than fatal: the file is appended to by
    a subprocess per gate, so a truncated tail must not blind the reader to
    the records that did land.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def blocking_records(path: Path) -> list[dict[str, Any]]:
    return [r for r in read_records(path)
            if r.get("outcome") in BLOCKING_OUTCOMES]


def attributable_records(path: Path) -> list[dict[str, Any]]:
    """Blocking records that name a sub-plan — the only ones a verdict can
    be about.  Identity comes from the invoker (Contract 2b invariant 6),
    so a blocking record with an empty ``slug`` is unattributable."""
    return [r for r in blocking_records(path) if r.get("slug")]


def unattributable_count(path: Path) -> int:
    """Blocking records with no slug: not verdicts, reported not enforced."""
    return len(blocking_records(path)) - len(attributable_records(path))


def strike_targets(path: Path) -> list[str]:
    """Only a confirmed red with a measured commit owner may add a strike.

    Missing measurement remains blocking without inventing blame. Each row
    carries its own owner; a different row's owner never redirects this one.
    """
    targets=set()
    for row in attributable_records(path):
        attribution=row.get('attribution')
        if row.get('verdict')!='red' or not isinstance(attribution,dict):continue
        if attribution.get('verdict') not in {'owned','inherited'}:continue
        if attribution.get('batch_base_green') is not True:continue
        if not all(isinstance(attribution.get(key),str) and
                   re.fullmatch(r'[0-9a-f]{40,64}',attribution[key])
                   for key in ('owner_sha','iteration_base','batch_base')):continue
        owner=attribution.get('owner_slug')
        if isinstance(owner,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',owner):
            targets.add(owner)
    return sorted(targets)


def attribute_records(path, *, context=None, budget_s=300):
    """Measure confirmed reds; ownership alone cannot excuse or advance them.

    Use frozen roots and complete captures. The existing 300s legacy cap is
    shared across rows/checks; owned contexts also retain their original cap.
    """
    import subprocess
    from iteration_context import read_context,red_inputs
    import red_owner
    from iteration_clock import monotonic
    rows=[]
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if line.strip():
            row=json.loads(line)
            if not isinstance(row,dict):raise ValueError('invalid check record')
            rows.append(row)
    context=context or read_context()
    if context and context.get('worker_sandbox_adapter'):
        from run_registry import require_driver
        require_driver(context)
        if not context.get('iteration_deadline_monotonic'):raise ValueError('owned attribution deadline absent')
    if context and context.get('iteration_deadline_monotonic') is not None:
        budget_s=min(budget_s,max(0,context['iteration_deadline_monotonic']-monotonic()))
    with red_owner._budget_scope(budget_s):
        for row in rows:
            if row.get('verdict')!='red' or row.get('outcome') not in BLOCKING_OUTCOMES:continue
            attrs=[]
            try:
                if not context:raise ValueError('frozen attribution context unavailable')
                frozen=red_inputs(context,row['slug'],int(row['step']))
                root=Path(frozen['exec_tree'])
                head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,timeout=red_owner._remaining(30)).strip()
                observations=row.get('check_observations') or (row.get('raw') or {}).get('results')
                if not observations:raise ValueError('complete check observations unavailable')
                for check in observations:
                    if check.get('outcome') not in BLOCKING_OUTCOMES:continue
                    if check.get('output_complete') is not True:raise ValueError('check output incomplete')
                    output=[]
                    for channel in ('stdout','stderr'):
                        capture=check.get(channel+'_path')
                        if not capture:raise ValueError('complete capture path unavailable')
                        output.append(Path(capture).read_text(errors='replace'))
                    attr=red_owner.attribute_red(root,frozen['iteration_base'],frozen.get('batch_base'),head,
                        check['command'],'\n'.join(output),red_owner._remaining(budget_s))
                    attrs.append(dict(attr,command=check['command'],scope=check.get('scope')))
                if not attrs:raise ValueError('confirmed failing check unavailable')
                identities={(a.get('verdict'),a.get('owner_sha'),a.get('owner_slug'),a.get('batch_base_green')) for a in attrs}
                if len(identities)!=1:raise ValueError('check owners differ; no row-wide strike')
                row['attribution']=dict(attrs[0],checks=attrs)
            except Exception as error:
                row['attribution']=dict(verdict='unmeasured',reason=type(error).__name__+': '+str(error),checks=attrs)
    import tempfile,os
    with tempfile.NamedTemporaryFile(mode='w',dir=Path(path).parent,delete=False,encoding='utf-8') as stream:
        temporary=Path(stream.name)
        for row in rows:stream.write(json.dumps(row)+'\n')
        stream.flush();os.fsync(stream.fileno())
    try:temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
    return dict(measured=sum(row.get('attribution',{}).get('verdict') in {'owned','inherited','pre-existing'} for row in rows),
                diagnostic_only=True)


def confirm_rerun(first_path: Path, rerun_path: Path) -> dict[str, Any]:
    """B2 confirm-before-block, recorded in the first-pass file itself.

    A blocking check whose re-run is explicitly pass was transient. Its
    record is rewritten with the re-run's outcome plus ``attempts`` (both
    outcomes) and ``flaky: true``, so every reader of the results file --
    ship_integrity (``outcome == "pass"``), quarantine, the JSONL record --
    agrees with the decision B2 made.  Before 2026-09-28 the re-run file
    was deleted and the first-pass red stayed in the results file:
    ship_integrity then reverted correct work that B2 had already cleared
    (ilk-skills run 20260928-202541; design D5,
    docs/architecture/loop-state-and-ownership-design.md section 6).

    A blocking check with an unmeasured retry is inconclusive, not cleared.
    An explicitly deterministic check retains its first measured red.

    Returns ``{"blocked": bool, "transient_cleared": [...]}`` -- the shape the
    runner parsed from its old inline block.
    """
    first = read_records(first_path)
    rerun = {}
    for row in read_records(rerun_path):
        if row.get('slug'):
            rerun.setdefault((row['slug'], _step_of(row)), []).append(row)
    confirmed: list[str] = []
    inconclusive: list[str] = []
    transient: list[str] = []
    rewritten: list[dict[str, Any]] = []
    for rec in first:
        if rec.get("outcome") in BLOCKING_OUTCOMES and rec.get("slug"):
            key = (rec["slug"], _step_of(rec))
            retries = rerun.get(key, [])
            first_outcome = rec.get("outcome")
            history = list(rec.get('attempt_records') or [dict(rec)])
            history.extend(dict(row) for row in retries)
            outcomes = list(rec.get('attempts') or [first_outcome])
            outcomes.extend([row.get('outcome') for row in retries] or ['not-rerun'])
            measured = bool(retries) and all(_same_checks(rec, row) and
                row.get('outcome') in (*BLOCKING_OUTCOMES, 'pass') for row in retries)
            if not _needs_rerun(rec)[0] or (measured and 'pass' not in outcomes):
                confirmed.append(f"{key[0]}#{key[1]}")
                rec = {**rec, "attempts": outcomes, "attempt_records": history, 'verdict': 'red'}
            elif measured and 'pass' in outcomes:
                transient.append(str(key))
                rec = {**rec, "outcome": 'pass',
                       "attempts": outcomes, "attempt_records": history,
                       "flaky": True, 'verdict': 'flaky'}
            else:
                inconclusive.append(f"{key[0]}#{key[1]}")
                rec = {**rec, 'outcome': 'inconclusive', 'verdict': 'inconclusive',
                       'attempts': outcomes, 'attempt_records': history}
        rewritten.append(rec)
    tmp = first_path.with_suffix(first_path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rewritten), encoding="utf-8")
    tmp.replace(first_path)
    return {"blocked": bool(confirmed or inconclusive), "transient_cleared": transient,
            "inconclusive": bool(inconclusive), "inconclusive_checks": inconclusive,
            "confirmed": confirmed}


def _same_checks(first, retry):
    # The failing display label can differ from a green gate's first label.
    # Compare the complete measured check set when present, for both callers.
    def identity(row):
        checks = row.get('check_observations') or (row.get('raw') or {}).get('results')
        if checks:
            fields = ('command', 'scope', 'files', 'partition_index', 'partition_count', 'tree_sha')
            return sorted(json.dumps({key: check[key] for key in fields if key in check},
                                     sort_keys=True) for check in checks)
        return row.get('command')
    return identity(first) == identity(retry)


def _step_of(rec: dict[str, Any]) -> int:
    step = rec.get("step")
    return step if isinstance(step, int) else 0


def _needs_rerun(rec: dict[str, Any]) -> tuple[bool, str | None]:
    """A blocking check that should be re-run in B2.

    Deterministic checks and environment reds skip the re-run: their
    verdict cannot change, so a transient pass is impossible.

    Returns (True, None) if the check needs re-run, (False, reason) if skipped.
    """
    # Deterministic: retry: false
    if rec.get("retry") is False:
        return False, "retry: false"

    # Deterministic: verify_attribution.py without --remeasure-if-stale
    cmd = rec.get("command", "")
    if "verify_attribution" in cmd and "--remeasure-if-stale" not in cmd:
        return False, "verify_attribution without --remeasure-if-stale"

    # Environment red: error with reason starting with "environment:"
    if rec.get("outcome") == "error":
        reason = rec.get("reason") or ""
        if reason.startswith("environment:"):
            return False, reason

    return True, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results_file", type=Path,
                    help="the local_checks JSONL written by emit_jsonl_record.py")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--attribute', action='store_true', help='measure confirmed reds from frozen context; never waive a gate')
    mode.add_argument("--any", action="store_true",
                      help="exit 0 if any attributable record is fail/error, else 1 "
                           "(an anonymous record is not a verdict about any sub-plan)")
    mode.add_argument('--inconclusive', action='store_true',
                      help='exit 0 if an attributable gate was unmeasured; no strike is authorized')
    mode.add_argument("--targets", action="store_true",
                      help="'<slug> <step>' per attributable blocking record "
                           "(B2 re-run input; anonymous records are never emitted)")
    mode.add_argument("--slugs", action="store_true",
                      help="unique attributable blocking slugs, sorted (auto-quarantine input)")
    mode.add_argument('--strike-targets', action='store_true',
                      help='only confirmed red rows with a measured batch commit owner')
    mode.add_argument("--describe", action="store_true",
                      help="'slug#step, slug#step' for the human-readable line, plus an "
                           "'N unattributable (no slug)' segment when anonymous records exist")
    mode.add_argument("--unattributable-count", action="store_true",
                      help="print the number of blocking records with no slug")
    mode.add_argument("--confirm", metavar="RERUN_FILE", type=Path,
                      help="B2: compare with the re-run, rewrite cleared records with both "
                           "attempts and flaky:true, print {blocked, transient_cleared}")
    mode.add_argument("--outcome-for-slug", metavar="SLUG",
                      help="print the outcome for a specific slug (fail or error)")
    mode.add_argument("--rerun-targets", action="store_true",
                      help="like --targets but excludes deterministic and environment-red "
                           "checks (those whose verdict cannot change)")
    mode.add_argument("--json-lines", action="store_true",
                      help="print each attributable blocking record as a JSON object, "
                           "one per line (for red-owner attribution)")
    args = ap.parse_args(argv)

    if args.attribute:
        print(json.dumps(attribute_records(args.results_file)))
        return 0

    if args.any:
        return 0 if attributable_records(args.results_file) else 1
    if args.strike_targets:
        for owner in strike_targets(args.results_file):print(owner)
        return 0
    if args.inconclusive:
        return 0 if any(r.get('slug') and r.get('outcome') in ('inconclusive', 'unknown', None)
                        for r in read_records(args.results_file)) else 1

    if args.confirm:
        print(json.dumps(confirm_rerun(args.results_file, args.confirm)))
        return 0

    if args.targets:
        for rec in attributable_records(args.results_file):
            print(f"{rec['slug']} {_step_of(rec)}")
        return 0

    if args.rerun_targets:
        for rec in attributable_records(args.results_file):
            needs, _reason = _needs_rerun(rec)
            if needs:
                print(f"{rec['slug']} {_step_of(rec)}")
        return 0

    if args.json_lines:
        for rec in attributable_records(args.results_file):
            print(json.dumps(rec, ensure_ascii=False))
        return 0

    if args.slugs:
        # The same explicit rule as --targets (drop the unattributable), not
        # the old set-difference accident.
        for slug in sorted({r["slug"] for r in attributable_records(args.results_file)}):
            print(slug)
        return 0

    if args.unattributable_count:
        print(unattributable_count(args.results_file))
        return 0

    if args.outcome_for_slug:
        target_slug = args.outcome_for_slug
        for rec in attributable_records(args.results_file):
            if rec.get("slug") == target_slug:
                print(rec.get("outcome", "fail"))
                return 0
        # Slug not found in blocking records — default to "fail" for back-compat
        print("fail")
        return 0

    # --describe
    parts = [f"{r['slug']}#{_step_of(r)}" for r in attributable_records(args.results_file)]
    unattr = unattributable_count(args.results_file)
    if unattr:
        parts.append(f"{unattr} unattributable (no slug)")
    print(", ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
