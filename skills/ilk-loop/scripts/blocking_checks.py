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

    blocking_checks.py <results-file> --any        # exit 0 iff any record blocks
    blocking_checks.py <results-file> --targets    # "<slug> <step>" per line
    blocking_checks.py <results-file> --slugs      # unique slugs, sorted
    blocking_checks.py <results-file> --describe   # "slug#step, slug#step"
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


def _step_of(rec: dict[str, Any]) -> int:
    step = rec.get("step")
    return step if isinstance(step, int) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results_file", type=Path,
                    help="the local_checks JSONL written by emit_jsonl_record.py")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--any", action="store_true",
                      help="exit 0 if any record is fail/error, else 1")
    mode.add_argument("--targets", action="store_true",
                      help="'<slug> <step>' per blocking record (B2 re-run input)")
    mode.add_argument("--slugs", action="store_true",
                      help="unique blocking slugs, sorted (auto-quarantine input)")
    mode.add_argument("--describe", action="store_true",
                      help="'slug#step, slug#step' for the human-readable line")
    args = ap.parse_args(argv)

    blocking = blocking_records(args.results_file)

    if args.any:
        return 0 if blocking else 1

    if args.targets:
        for rec in blocking:
            print(f"{rec.get('slug', '')} {_step_of(rec)}")
        return 0

    if args.slugs:
        for slug in sorted({r.get("slug", "") for r in blocking} - {""}):
            print(slug)
        return 0

    # --describe
    print(", ".join(f"{r.get('slug', '')}#{_step_of(r)}" for r in blocking))
    return 0


if __name__ == "__main__":
    sys.exit(main())
