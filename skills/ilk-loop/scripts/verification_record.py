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
    r"^[-*\s]*\**\s*head[^:\n]*:\**[ \t]*`?[0-9a-fA-F]{7,40}`?\s*$",
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

    if not record.is_file():
        print(f"ERROR: record not found: {record}", file=sys.stderr)
        return 1

    text = record.read_text(encoding="utf-8-sig", errors="replace")
    updated = inject_verified_head(text, sha)
    record.write_text(updated, encoding="utf-8")
    print(f"verified_head: {sha} → {record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
