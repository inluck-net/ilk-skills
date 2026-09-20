"""The project key is a CROSS-REPO contract, so it gets named vectors.

``ilk_paths.project_key`` decides the directory a run's records are filed
under.  Consumers must find those records again.  gh-resolve's
``reconcile.py:41`` re-implemented the transform rather than shell out to
another repo — a reasonable instinct that produced a silent, one-way
divergence:

    lowercase + [/\\.] -> '-' + lstrip('-')        (the re-implementation)
    lowercase + [^a-z0-9]+ -> '-' + 80-char cap    (this repo)

Those two AGREED below 80 characters and DIVERGED above it, because the cap
replaced the tail with a 7-char sha1 and a hash cannot be inverted.  Resolver
worktree paths sat right at the boundary: the live
``…kira-cloudflare-scratch-worktrees-resolver-7359f23`` is 79 characters and
agreed by ONE character.  A slightly longer worktree name did not.

**Superseded 2026-09-20 (C1, sub-plan a-project-key-that-cannot-collide).**
That conditional hash was itself the defect: below the cap the lossy slug WAS
the key, so four distinct paths mapped to ``private-tmp-ilk-review-app-one``.
The hash is now unconditional and taken over the case-preserving absolute path,
so the transform is::

    <lowercased-hyphenated-slug, <=72 chars>-<7-char sha1 of the exact path>

The consequence for this file is that the naive mirror now diverges at **every**
length, not just above 80.  That is a widening of an existing break, not a new
one -- the mirror was already unusable for the case that mattered, and this
file's own conclusion was already "call the CLI rather than re-implement".  The
vectors below are kept and updated rather than deleted: they are the record of
what a mirror must now reproduce, and of the boundary that no longer exists.

Measured consequence on rezmac, 2026-09-09: **0 of 5 exit records reachable by
the remedy `doctor` prints, and 0 of 5 computed directories exist**, so
``gh-resolve reconcile <worktree>`` — the command an operator is told to run —
exits 0 and does nothing, forever, on every parked run.  A run that parks and
cannot be reconciled needs a human, which is the outcome the loop exists to
avoid.

So this file does two jobs:

1. Pin the algorithm with vectors a mirror implementation in ANY language can
   be diffed against, including the case that actually broke.
2. Pin the ``--project-key`` CLI as the supported cross-repo entry point, so
   agreement needs no re-implementation at all.

Sub-plan: unassigned — landed directly, 2026-09-09.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from ilk_paths import project_key  # noqa: E402

_CLI = _SCRIPTS / "ilk_paths.py"


def _naive_key(path: str) -> str:
    """gh-resolve reconcile.py:41, reproduced to pin WHERE it diverges.

    Not a criticism of that code — it is here so the boundary is a checked
    fact rather than a claim in a commit message.

    Since C1 it diverges everywhere, so this is no longer a boundary marker but
    a standing demonstration that mirroring cannot work. gh-resolve has not
    been updated by this repo; until it calls `--project-key` it computes the
    old key for every path.
    """
    return re.sub(r"[/\\.]", "-", path.lower()).lstrip("-")


# ── AC-1: the exported entry point cannot drift from the function ───────────

@pytest.mark.parametrize("path", [
    "/Users/chad/Projects/github/inluck-net/ilk-skills",
    "/Users/chad/Projects/keyreply/kira-cloudflare/scratch/worktrees/resolver-abcdef1234",
    "/tmp/x",
])
def test_cli_agrees_with_the_function(path: str) -> None:
    """`--project-key` is what other repos call; it must not drift.

    A CLI that quietly disagreed with the library would be worse than no CLI:
    consumers would adopt it believing they had stopped re-implementing.
    """
    proc = subprocess.run(
        [sys.executable, str(_CLI), "--project-key", "--start", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == project_key(Path(path)), (
        "the --project-key CLI and project_key() disagree; every consumer "
        "calling the CLI would file or seek records in the wrong directory"
    )


# ── AC-2: named vectors, including the one that broke ───────────────────────

def test_short_paths_are_slug_plus_hash_not_the_plain_slug() -> None:
    """The vector that used to assert the defect, inverted.

    Before C1 this asserted the key WAS the bare slug.  That is precisely the
    property that let four paths share it, so the assertion now pins the
    replacement: the readable slug survives verbatim as a prefix, and identity
    is carried by a suffix that is always present.
    """
    key = project_key(Path("/Users/chad/Projects/github/inluck-net/ilk-skills"))
    assert key == "users-chad-projects-github-inluck-net-ilk-skills-604d727"
    assert key.startswith("users-chad-projects-github-inluck-net-ilk-skills-"), (
        "the slug must survive verbatim so state dirs stay greppable"
    )
    assert key != _naive_key("/Users/chad/Projects/github/inluck-net/ilk-skills"), (
        "a short path no longer agrees with the naive mirror either -- this is "
        "the widening gh-resolve must be told about"
    )


def test_the_79_char_near_miss_no_longer_agrees() -> None:
    """The live resolver worktree, kept as the historical near-miss.

    Under the old construction this path agreed with the naive mirror by ONE
    character, which is why the divergence went unnoticed for so long.  There
    is no near-miss any more because there is no boundary any more: the slug is
    truncated at 72 and a hash is always appended, so this key and the naive
    one differ structurally rather than by luck.
    """
    p = "/Users/chad/Projects/keyreply/kira-cloudflare/scratch/worktrees/resolver-7359f23"
    key = project_key(Path(p))
    assert key == "users-chad-projects-keyreply-kira-cloudflare-scratch-worktrees-resolver-047d71e"
    assert len(key) == 79, (
        "71-char slug (the 72-char cut lands on a hyphen and rstrip drops it) "
        "+ 1 separator + 7 hash chars"
    )
    assert key != _naive_key(p), (
        "the one-character agreement that hid the bug is gone"
    )


def test_a_path_over_the_cap_is_hashed_and_diverges() -> None:
    """The case that breaks reconcile, pinned exactly."""
    p = "/Users/chad/Projects/keyreply/kira-cloudflare/scratch/worktrees/resolver-abcdef1234"
    key = project_key(Path(p))
    # AT MOST 80, not exactly 80: the construction is
    # slug[:72].rstrip("-") + "-" + sha1[:7], and the rstrip drops a trailing
    # hyphen when the 72-char cut lands on a separator — as it does here,
    # giving 79. A mirror implementation that pads to exactly 80, or that
    # skips the rstrip, produces a different key for these paths.
    assert len(key) <= 80, "keys are capped at 80 characters"
    assert len(key) == 79, (
        "this vector's 72-char cut lands on a hyphen, so rstrip makes it 79 — "
        "pinned because it is the easiest detail for a mirror to get wrong"
    )
    assert key.endswith("-bc07129"), (
        "the tail is the first 7 chars of sha1 over the EXACT, case-preserving "
        "absolute path. It changed from -3bd7ec7 at C1 because the old hash "
        "input was lowercased, which would have left APP.ONE and app.one "
        "sharing a key. A mirror must hash the un-lowercased path."
    )
    assert key != _naive_key(p), (
        "this is the divergence: 0 of 5 exit records were reachable on rezmac "
        "because the consumer computed the uncapped form"
    )


def test_the_cap_is_not_invertible_so_the_consumer_cannot_recover_it() -> None:
    """Why 'just truncate on the other side too' is not the whole fix.

    Two distinct long paths sharing an 80-char prefix differ only in the sha1
    tail, so a consumer cannot reconstruct the key from a truncated slug — it
    has to run the same hash. That is the argument for calling the CLI rather
    than mirroring by eye.
    """
    a = "/Users/chad/Projects/keyreply/kira-cloudflare/scratch/worktrees/resolver-aaaaaaaaaa"
    b = "/Users/chad/Projects/keyreply/kira-cloudflare/scratch/worktrees/resolver-bbbbbbbbbb"
    ka, kb = project_key(Path(a)), project_key(Path(b))
    assert ka != kb, "distinct paths must not collide after capping"
    assert ka[:72] == kb[:72], (
        "they share everything but the hash tail, so the visible slug alone "
        "does not identify the project"
    )


def test_keys_are_deterministic() -> None:
    p = Path("/Users/chad/Projects/keyreply/kira-cloudflare/scratch/worktrees/resolver-abcdef1234")
    assert project_key(p) == project_key(p)


# ── AC-3: the boundary itself ───────────────────────────────────────────────

def test_agreement_now_fails_at_every_length() -> None:
    """One assertion carrying the whole shape of the change.

    The old version of this test asserted agreement BELOW the cap and
    divergence above it — a boundary that existed only because the hash was
    conditional. C1 removed the condition, so both sides now diverge, and the
    length ceiling is the only thing the two transforms still have in common.

    Anyone changing `project_key` should still see this fail and understand
    that a consumer in another repo depends on the exact transform. The remedy
    has not changed and is now the only remedy: call `--project-key`.
    """
    below = "/Users/chad/Projects/github/inluck-net/gh-resolve/tests/fixtures/cli-term"
    above = "/Users/chad/Projects/keyreply/kira-cloudflare/scratch/worktrees/resolver-abcdef1234"
    assert project_key(Path(below)) != _naive_key(below), (
        "below the old cap the mirror used to agree; it no longer does"
    )
    assert project_key(Path(above)) != _naive_key(above)
    assert len(_naive_key(above)) > 80 >= len(project_key(Path(above)))
    for p in (below, above):
        assert len(project_key(Path(p))) <= 80
