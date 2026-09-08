#!/usr/bin/env python3
"""One writer for the ship transition — and one repair for its residue.

Shipping a sub-plan is two writes to two stores:

  1. the sub-plan front-matter ``status: shipped``, which lives **outside**
     the repo under ``~/.ilk-data/projects/<key>/plans/``;
  2. the marker commit ``chore(plans): <slug> shipped [plan:<slug>#ship]``,
     which lives **inside** the repo.

Nothing sequenced them, nothing recorded that one happened without the other,
and nothing repaired the pair afterwards. Measured 2026-09-08 in gh-resolve:
``MASTER-2026-09-07c`` read ``shipped`` while
``2026-09-07c-conflict-batch-verify.md`` read ``in-progress`` at
``current_step: 2``, with the ``#ship`` marker commit present in the repo.
``loop_status`` then selected that older master as next-active and flagged
three of its sub-plans ``(!) unproven``.

The window opens when an iteration is killed between the two writes — which is
why this module ships after ``a-productive-timeout-is-not-a-barren-one``.

judgment call: the **marker commit** is written **before** the front-matter
status because the residue of that order is repairable forward from evidence
that is already durable — a commit is append-only and immutable, so "commit
present, front-matter not yet shipped" is converged by an idempotent
front-matter write, and repeating the whole operation is a no-op. The reverse
order leaves "front-matter shipped, no commit", which can only be converged by
*fabricating* a marker commit for work nothing in the repo attests to; that
forges the audit trail ``ship_audit`` reads, so this module refuses it instead.
Neither order is compelled by evidence — only the recoverability is.
wrong if a repo's history is ever rewritten so a landed marker commit
disappears (force-push, ``reset --hard`` past it): repair would then read the
absence as the un-decidable direction and refuse, which is the safe failure but
means a genuine ship needs a hand-written status.

The intent marker (``.ship-intent.json`` in the plans dir, next to the file
being mutated and outside the repo by the same rule that puts plans there) is
written before the first half and cleared only when both halves are durable.
It is what separates "an iteration died mid-ship" from "someone edited a file
by hand" — the two produce identical store contents otherwise.

Stdlib only, matching the rest of ``skills/ilk-loop/scripts/``.

CLI::

    python3 ship_transition.py --repair --plans-dir DIR --repo DIR [--apply] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

__all__ = [
    "ShipTransitionError",
    "Divergence",
    "RepairAction",
    "ShipResult",
    "MARKER_WITHOUT_STATUS",
    "STATUS_WITHOUT_MARKER",
    "detect",
    "ship",
    "repair",
    "read_intent",
    "write_intent",
    "clear_intent",
]

# The two directions of divergence. Named, never merely "inconsistent": the
# repair for one is evidence-backed and the repair for the other does not
# exist, so a detector that conflates them cannot be acted on.
MARKER_WITHOUT_STATUS = "marker-without-status"
STATUS_WITHOUT_MARKER = "status-without-marker"

INTENT_FILENAME = ".ship-intent.json"

_SHA_LEN = 12
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
_STATUS_LINE_RE = re.compile(r"^(\s*status\s*:).*$", re.MULTILINE)


class ShipTransitionError(RuntimeError):
    """The transition cannot proceed — raised before either half is written."""


@dataclass(frozen=True)
class Divergence:
    """One sub-plan whose two stores disagree."""

    slug: str
    subplan: str
    kind: str
    frontmatter_shipped: bool
    marker_commit: str | None
    interrupted: bool = False


@dataclass
class RepairAction:
    """What repair did, or declined to do, for one diverged pair."""

    slug: str
    subplan: str
    kind: str
    applied: bool = False
    refused: bool = False
    reason: str = ""


@dataclass
class ShipResult:
    slug: str
    subplan: str
    marker_commit: str = ""
    marker_created: bool = False
    status_written: bool = False


# ── git ──────────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str, check: bool = True) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
    )
    if check and cp.returncode != 0:
        raise ShipTransitionError(
            f"git {' '.join(args)} failed in {repo}: {cp.stderr.strip()}"
        )
    return cp.stdout


def marker_trailer(slug: str) -> str:
    """The exact trailer a ship marker commit must carry (Contract 9)."""
    return f"[plan:{slug}#ship]"


def find_marker_commit(repo: Path, slug: str) -> str | None:
    """Return the abbreviated sha of *slug*'s ship marker commit, or None.

    Searches ``--all`` over the full message (subject **and** body), matching
    ``ship_audit.check_step_commits`` so the two readers cannot disagree about
    whether a marker exists.
    """
    out = _git(repo, "log", "--all", "--format=%H%x1f%s%n%b%x1e", check=False)
    needle = marker_trailer(slug)
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, message = record.partition("\x1f")
        if needle in message:
            return sha.strip()[:_SHA_LEN]
    return None


# ── the plans dir ────────────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> dict[str, str]:
    m = _FM_RE.match(text.lstrip("﻿"))
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if line.startswith((" ", "\t", "-")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def _subplan_files(plans_dir: Path) -> list[Path]:
    return sorted(
        p for p in plans_dir.glob("*.md") if not p.name.startswith("MASTER")
    )


def _read_subplan(path: Path) -> tuple[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    return text, _parse_frontmatter(text)


def find_subplan(plans_dir: Path, slug: str) -> Path:
    """Locate the sub-plan file whose ``plan:`` front-matter equals *slug*.

    Matches on the front-matter value, never on the filename: the filename
    carries a date prefix the trailer does not.
    """
    for path in _subplan_files(plans_dir):
        try:
            _, fm = _read_subplan(path)
        except OSError:
            continue
        if fm.get("plan", "") == slug:
            return path
    raise ShipTransitionError(
        f"no sub-plan in {plans_dir} declares plan: {slug!r} "
        f"(candidates: {[p.name for p in _subplan_files(plans_dir)]})"
    )


def _write_status_shipped(path: Path) -> bool:
    """Rewrite only the front-matter ``status:`` line. Idempotent."""
    text, fm = _read_subplan(path)
    if fm.get("status", "").strip().lower() == "shipped":
        return False
    m = _FM_RE.match(text.lstrip("﻿"))
    if not m:
        raise ShipTransitionError(f"{path.name} has no front-matter block")
    head, body = text[: m.end()], text[m.end():]
    new_head, n = _STATUS_LINE_RE.subn(r"\1 shipped", head, count=1)
    if n == 0:
        raise ShipTransitionError(f"{path.name} front-matter has no status: line")
    path.write_text(new_head + body, encoding="utf-8")
    return True


# ── the intent marker ────────────────────────────────────────────────────────

def _intent_path(plans_dir: Path) -> Path:
    return plans_dir / INTENT_FILENAME


def read_intent(plans_dir: Path) -> dict | None:
    path = _intent_path(plans_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_intent(plans_dir: Path, slug: str, repo: Path) -> Path:
    """Record that a transition for *slug* is in flight, before either half."""
    path = _intent_path(plans_dir)
    payload = {
        "slug": slug,
        "repo": str(Path(repo).resolve()),
        "pid": os.getpid(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def clear_intent(plans_dir: Path) -> None:
    """Clear the in-flight marker. Only after both halves are durable."""
    try:
        _intent_path(plans_dir).unlink()
    except FileNotFoundError:
        pass


# ── detection ────────────────────────────────────────────────────────────────

def detect(plans_dir: Path, repo: Path) -> list[Divergence]:
    """Every sub-plan whose two stores disagree, each direction named.

    Consistent states — both halves present, or neither — are not reported;
    work in flight is not a divergence.
    """
    plans_dir, repo = Path(plans_dir), Path(repo)
    intent = read_intent(plans_dir) or {}
    in_flight = intent.get("slug")

    found: list[Divergence] = []
    for path in _subplan_files(plans_dir):
        try:
            _, fm = _read_subplan(path)
        except OSError:
            continue
        slug = fm.get("plan", "")
        if not slug:
            continue
        shipped = fm.get("status", "").strip().lower() == "shipped"
        marker = find_marker_commit(repo, slug)
        if shipped == bool(marker):
            continue
        found.append(
            Divergence(
                slug=slug,
                subplan=path.name,
                kind=MARKER_WITHOUT_STATUS if marker else STATUS_WITHOUT_MARKER,
                frontmatter_shipped=shipped,
                marker_commit=marker,
                interrupted=(slug == in_flight),
            )
        )
    return found


# ── the ordered writer ───────────────────────────────────────────────────────

def ship(
    plans_dir: Path,
    repo: Path,
    slug: str,
    *,
    _write_status: Callable[[Path], bool] | None = None,
) -> ShipResult:
    """Perform the ship transition as one ordered operation.

    Order (see the module docstring's judgment call): intent → marker commit →
    front-matter status → clear intent. Idempotent at every point: a slug whose
    marker already exists is not committed twice, and a front-matter already
    ``shipped`` is not rewritten.

    Raises ``ShipTransitionError`` *before* either half is written when the
    slug names no sub-plan — a marker commit for work that does not exist is
    exactly the forgery ``ship_audit`` would then have to believe.

    ``_write_status`` is a seam for the tests that pin the write order; it is
    not part of the public contract.
    """
    plans_dir, repo = Path(plans_dir), Path(repo)
    subplan = find_subplan(plans_dir, slug)  # refuses before anything is written
    writer = _write_status or _write_status_shipped

    write_intent(plans_dir, slug, repo)

    marker = find_marker_commit(repo, slug)
    created = False
    if marker is None:
        _git(
            repo, "commit", "--allow-empty", "-q",
            "-m", f"chore(plans): {slug} shipped {marker_trailer(slug)}",
        )
        marker = find_marker_commit(repo, slug)
        created = True

    status_written = writer(subplan)

    clear_intent(plans_dir)
    return ShipResult(
        slug=slug,
        subplan=subplan.name,
        marker_commit=marker or "",
        marker_created=created,
        status_written=bool(status_written),
    )


# ── repair ───────────────────────────────────────────────────────────────────

_REFUSAL = (
    "front-matter says shipped but no {trailer} commit exists in this repo. "
    "Refusing: fabricating the marker would forge the trail ship_audit reads, "
    "and reverting the status would destroy a genuine ship whose trailer was "
    "mistyped. Resolve by hand — check `git log --all --grep '#ship'` for a "
    "near-miss slug, or re-run the ship step."
)


def repair(
    plans_dir: Path,
    repo: Path,
    apply: bool = False,
    only_interrupted: bool = False,
) -> list[RepairAction]:
    """Converge diverged pairs. **Dry-run by default**; ``apply=True`` writes.

    ``marker-without-status`` converges forward: the commit is durable proof
    the work completed, so the front-matter is moved to match.

    ``status-without-marker`` is **refused**, not guessed — see ``_REFUSAL``.
    A refusal names the pair and makes the CLI exit non-zero.

    ``only_interrupted`` narrows the scan to pairs the intent marker attests
    to, and it exists for exactly one caller: the loop driver, which converges
    automatically and so must not out-vote a *deliberate* revert. ``test_ship_integrity``
    reverts ``shipped`` → ``in-progress`` when a sub-plan's gate is red, and
    the marker commit stays in history — so an unconditional auto-repair would
    silently re-ship it on the next run. An intent marker is only written by
    ``ship()``, and only cleared once both halves are durable, so its presence
    is positive evidence that *this* pair is a transition that died mid-write
    rather than one a gate deliberately unwound. The operator CLI leaves it off:
    converging an old residue is a judgment a human is making on purpose.
    """
    plans_dir, repo = Path(plans_dir), Path(repo)
    actions: list[RepairAction] = []
    for d in detect(plans_dir, repo):
        if only_interrupted and not d.interrupted:
            continue
        if d.kind == MARKER_WITHOUT_STATUS:
            action = RepairAction(
                slug=d.slug, subplan=d.subplan, kind=d.kind,
                reason=f"marker commit {d.marker_commit} is durable proof; "
                       f"front-matter moves to shipped",
            )
            if apply:
                _write_status_shipped(plans_dir / d.subplan)
                action.applied = True
                intent = read_intent(plans_dir) or {}
                if intent.get("slug") == d.slug:
                    clear_intent(plans_dir)
        else:
            action = RepairAction(
                slug=d.slug, subplan=d.subplan, kind=d.kind,
                refused=True,
                reason=_REFUSAL.format(trailer=marker_trailer(d.slug)),
            )
        actions.append(action)
    return actions


# ── CLI ──────────────────────────────────────────────────────────────────────

def _wrap_slugs(slugs, width: int = 76, indent: str = "    ") -> str:
    """One indented, comma-separated block — every refused pair is still named."""
    out, line = [], indent
    for slug in slugs:
        piece = slug + ", "
        if len(line) + len(piece) > width and line.strip():
            out.append(line.rstrip())
            line = indent
        line += piece
    out.append(line.rstrip().rstrip(","))
    return "\n".join(out)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ship_transition.py",
        description=(
            "One writer for the ship transition (front-matter status + marker "
            "commit), and a repair for pairs already diverged."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Repair is DRY-RUN by default; pass --apply to write.\n"
            "Exits non-zero when a pair is refused (the two stores disagree in "
            "a way that cannot be decided from evidence)."
        ),
    )
    p.add_argument("--repair", action="store_true",
                   help="scan for diverged pairs and converge them")
    p.add_argument("--ship", metavar="SLUG",
                   help="perform the ship transition for SLUG as one operation")
    p.add_argument("--plans-dir", required=True, type=Path,
                   help="the external plans dir (~/.ilk-data/projects/<key>/plans)")
    p.add_argument("--repo", required=True, type=Path,
                   help="the project repo holding the marker commits")
    p.add_argument("--apply", action="store_true",
                   help="with --repair: actually write (default is dry-run)")
    p.add_argument("--only-interrupted", action="store_true",
                   help=("with --repair: consider only pairs an intent marker "
                         "attests to (the loop driver's automatic mode)"))
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.ship:
        try:
            result = ship(args.plans_dir, args.repo, args.ship)
        except ShipTransitionError as exc:
            print(f"ship_transition: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"shipped": result.__dict__}, indent=2))
        else:
            print(f"shipped {result.slug}: marker {result.marker_commit} "
                  f"(created={result.marker_created}), "
                  f"status_written={result.status_written}")
        return 0

    if not args.repair:
        _build_parser().print_help()
        return 2

    actions = repair(args.plans_dir, args.repo, apply=args.apply,
                     only_interrupted=args.only_interrupted)
    refused = [a for a in actions if a.refused]

    if args.json:
        print(json.dumps(
            {"actions": [a.__dict__ for a in actions],
             "applied": args.apply,
             "refused": len(refused)},
            indent=2,
        ))
    elif not actions:
        print(f"ship_transition: 0 diverged pairs in {args.plans_dir}")
    else:
        # Refusals share one reason verbatim, and there can be many of them:
        # gh-resolve on 2026-09-08 had 144 refusals against 1 repairable pair,
        # and printing the paragraph once per slug produced 69KB that buried
        # the single actionable line. Repairable pairs are listed in full; the
        # refused set is one paragraph plus its slugs. --json is unshaped.
        mode = "APPLIED" if args.apply else "dry-run (pass --apply to write)"
        repairable = [a for a in actions if not a.refused]
        print(f"ship_transition: {len(actions)} diverged pair(s) in "
              f"{args.plans_dir} — {mode}")

        if repairable:
            print(f"\n  repairable ({len(repairable)}):")
            for a in repairable:
                verb = "repaired" if a.applied else "would repair"
                print(f"    [{verb}] {a.slug} ({a.kind}) — {a.subplan}")
                print(f"        {a.reason}")

        if refused:
            print(f"\n  REFUSED ({len(refused)}) — front-matter says shipped but no "
                  f"[plan:<slug>#ship] commit exists in this repo.")
            print("      Fabricating the marker would forge the trail ship_audit "
                  "reads, and reverting")
            print("      the status would destroy a genuine ship whose trailer was "
                  "mistyped. Resolve")
            print("      by hand: `git log --all --grep \'#ship\'` for a near-miss "
                  "slug, or re-run ship.")
            print(_wrap_slugs(a.slug for a in refused))

    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
