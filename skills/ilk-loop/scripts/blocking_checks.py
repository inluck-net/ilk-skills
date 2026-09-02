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


def _step_of(rec: dict[str, Any]) -> int:
    step = rec.get("step")
    return step if isinstance(step, int) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results_file", type=Path,
                    help="the local_checks JSONL written by emit_jsonl_record.py")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--any", action="store_true",
                      help="exit 0 if any attributable record is fail/error, else 1 "
                           "(an anonymous record is not a verdict about any sub-plan)")
    mode.add_argument("--targets", action="store_true",
                      help="'<slug> <step>' per attributable blocking record "
                           "(B2 re-run input; anonymous records are never emitted)")
    mode.add_argument("--slugs", action="store_true",
                      help="unique attributable blocking slugs, sorted (auto-quarantine input)")
    mode.add_argument("--describe", action="store_true",
                      help="'slug#step, slug#step' for the human-readable line, plus an "
                           "'N unattributable (no slug)' segment when anonymous records exist")
    mode.add_argument("--unattributable-count", action="store_true",
                      help="print the number of blocking records with no slug")
    args = ap.parse_args(argv)

    if args.any:
        return 0 if attributable_records(args.results_file) else 1

    if args.targets:
        for rec in attributable_records(args.results_file):
            print(f"{rec['slug']} {_step_of(rec)}")
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

    # --describe
    parts = [f"{r['slug']}#{_step_of(r)}" for r in attributable_records(args.results_file)]
    unattr = unattributable_count(args.results_file)
    if unattr:
        parts.append(f"{unattr} unattributable (no slug)")
    print(", ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
