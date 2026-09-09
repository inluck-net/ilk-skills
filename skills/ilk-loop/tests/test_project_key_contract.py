"""The project key is a CROSS-REPO contract, so it gets named vectors.

``ilk_paths.project_key`` decides the directory a run's records are filed
under.  Consumers must find those records again.  gh-resolve's
``reconcile.py:41`` re-implemented the transform rather than shell out to
another repo — a reasonable instinct that produced a silent, one-way
divergence:

    lowercase + [/\\.] -> '-' + lstrip('-')        (the re-implementation)
    lowercase + [^a-z0-9]+ -> '-' + 80-char cap    (this repo)

The two AGREE below 80 characters and DIVERGE above it, because the cap
replaces the tail with a 7-char sha1 and a hash cannot be inverted.  Resolver
worktree paths sit right at the boundary: the live
``…kira-cloudflare-scratch-worktrees-resolver-7359f23`` is 79 characters and
agrees by ONE character.  A slightly longer worktree name does not.

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
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == project_key(Path(path)), (
        "the --project-key CLI and project_key() disagree; every consumer "
        "calling the CLI would file or seek records in the wrong directory"
    )


# ── AC-2: named vectors, including the one that broke ───────────────────────

def test_short_paths_are_the_plain_slug() -> None:
    assert project_key(Path("/Users/chad/Projects/github/inluck-net/ilk-skills")) == (
        "users-chad-projects-github-inluck-net-ilk-skills"
    )


def test_a_path_at_79_chars_is_not_capped() -> None:
    """The live resolver worktree — it agrees with the naive transform by ONE
    character, which is why the divergence went unnoticed."""
    p = "/Users/chad/Projects/keyreply/kira-cloudflare/scratch/worktrees/resolver-7359f23"
    key = project_key(Path(p))
    assert len(key) == 79
    assert key == _naive_key(p), (
        "at 79 chars the two transforms agree — this vector documents the "
        "near-miss that made the bug look absent"
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
    assert key.endswith("-3bd7ec7"), (
        "the tail is the first 7 chars of sha1(lowercased absolute path); a "
        "mirror implementation must reproduce this exact string"
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

def test_agreement_holds_below_the_cap_and_fails_above_it() -> None:
    """One assertion carrying the whole shape of the defect.

    Anyone changing `project_key` should see this fail and understand that a
    consumer in another repo depends on the exact transform.
    """
    below = "/Users/chad/Projects/github/inluck-net/gh-resolve/tests/fixtures/cli-term"
    above = "/Users/chad/Projects/keyreply/kira-cloudflare/scratch/worktrees/resolver-abcdef1234"
    assert project_key(Path(below)) == _naive_key(below)
    assert project_key(Path(above)) != _naive_key(above)
    assert len(_naive_key(above)) > 80 >= len(project_key(Path(above)))
