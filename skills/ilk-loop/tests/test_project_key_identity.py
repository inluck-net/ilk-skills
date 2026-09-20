"""A project key must IDENTIFY a path, not merely describe it.

``ilk_paths.project_key`` lowercases the absolute path, squashes every run of
non-``[a-z0-9]`` to a single ``-``, and appends a 7-char sha1 **only when the
resulting slug exceeds 80 characters**.  Below that cap the transform is
lossy and nothing restores the loss, so distinct paths share a key.
Reproduced 2026-09-08 (C1 of MASTER-2026-09-08b):

    /tmp/ilk-review/app-one   ->  private-tmp-ilk-review-app-one
    /tmp/ilk-review/app_one   ->  private-tmp-ilk-review-app-one
    /tmp/ilk-review/app/one   ->  private-tmp-ilk-review-app-one
    /tmp/ilk-review/APP.ONE   ->  private-tmp-ilk-review-app-one

Four distinct paths, one key — which means one plans directory, one runtime
directory, one log stream and one lock shared between four projects.

Measured severity, so the pin is not oversold: **0 colliding keys among the 55
registered projects** at plan time.  The collision is latent.  What is not
latent is that the 80-char boundary is load-bearing for identity and has
already failed in the *other* direction — ``status_all.py`` reported
``repo_path: None`` for over-length slugs because the transform is one-way.

This file is the RED-FIRST pin for step 0.  The distinctness tests are
expected to FAIL until step 1 lands; the stability and readability tests pass
today and exist to stop step 1 from fixing collisions by throwing away the
human-searchable slug or by making keys non-deterministic.

Sub-plan: a-project-key-that-cannot-collide, step 0.

Note for step 1 — ``test_project_key_contract.py`` pins the CURRENT transform
as a cross-repo contract (gh-resolve ``reconcile.py:41`` mirrors it, and its
AC-2/AC-3 assert that short paths are the *plain* slug).  Making the hash
unconditional necessarily breaks those vectors; they describe the defect, not
a requirement, and must be updated in the same commit as the fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from ilk_paths import project_key  # noqa: E402


# The four paths reproduced on 2026-09-08, verbatim from the sub-plan.
COLLIDING_QUARTET = [
    "/tmp/ilk-review/app-one",
    "/tmp/ilk-review/app_one",
    "/tmp/ilk-review/app/one",
    "/tmp/ilk-review/APP.ONE",
]


def _keys(paths) -> dict:
    return {p: project_key(Path(p)) for p in paths}


# ── AC-1: the reproduction itself ───────────────────────────────────────────

def test_the_reproduced_quartet_gets_four_distinct_keys() -> None:
    """The exact reproduction from the design review, pinned.

    ``Path.resolve()`` rewrites ``/tmp`` to ``/private/tmp`` on macOS and
    leaves it alone on Linux; either way the four inputs stay four distinct
    absolute paths, so the assertion is platform-independent.
    """
    keys = _keys(COLLIDING_QUARTET)
    distinct = set(keys.values())
    assert len(distinct) == 4, (
        "4 distinct paths collapsed to %d key(s): %r — colliding keys mean a "
        "shared plans dir, runtime dir, log stream and lock" % (len(distinct), keys)
    )


# ── AC-2: not special-cased to that quartet ─────────────────────────────────

# Every one of these is a DIFFERENT directory on a case-sensitive filesystem,
# and each pair differs only in a character class the current transform
# destroys: a separator run, a punctuation choice, or letter case.
_SEPARATOR_VARIANTS = [
    "/srv/ilk/proj-alpha",
    "/srv/ilk/proj_alpha",
    "/srv/ilk/proj.alpha",
    "/srv/ilk/proj alpha",
    "/srv/ilk/proj/alpha",
    "/srv/ilk/proj--alpha",
    "/srv/ilk/proj-_alpha",
]

_CASE_VARIANTS = [
    "/srv/ilk/Alpha",
    "/srv/ilk/alpha",
    "/srv/ilk/ALPHA",
]


@pytest.mark.parametrize("variants,label", [
    (_SEPARATOR_VARIANTS, "separator"),
    (_CASE_VARIANTS, "case"),
])
def test_variants_that_differ_only_in_lost_characters_stay_distinct(variants, label) -> None:
    """A fix that only handles ``-`` vs ``_`` is not a fix.

    On a case-insensitive filesystem (macOS APFS by default) the case
    variants name the SAME directory, so distinct keys for them are merely
    redundant, never wrong.  On Linux they are genuinely different
    directories and a shared key is a collision.  Distinctness is the safe
    answer on both.
    """
    keys = _keys(variants)
    distinct = set(keys.values())
    assert len(distinct) == len(variants), (
        "%s variants: %d paths collapsed to %d key(s): %r"
        % (label, len(variants), len(distinct), keys)
    )


def test_every_pair_across_all_pinned_paths_is_distinct() -> None:
    """One assertion over the whole corpus, so a partial fix cannot pass.

    Names the offending pair rather than just a count — a bare
    ``len(set(...))`` failure does not say which two paths merged.
    """
    corpus = COLLIDING_QUARTET + _SEPARATOR_VARIANTS + _CASE_VARIANTS
    keys = _keys(corpus)
    merged = []
    for i, a in enumerate(corpus):
        for b in corpus[i + 1:]:
            if keys[a] == keys[b]:
                merged.append((a, b, keys[a]))
    assert not merged, (
        "%d path pair(s) share a key: %r" % (len(merged), merged)
    )


# ── AC-3: what must NOT change (green today; regression guard for step 1) ───

@pytest.mark.parametrize("path", COLLIDING_QUARTET + _SEPARATOR_VARIANTS + [
    "/Users/chad/Projects/github/inluck-net/ilk-skills",
])
def test_a_key_is_stable_for_the_same_path_across_calls(path: str) -> None:
    """Identity is useless if it is not repeatable.

    Rules out a fix built on ``id()``, ``uuid4()``, a counter, or anything
    seeded per-process — every one of which would make distinctness pass
    while losing every existing record on the next run.
    """
    first = project_key(Path(path))
    assert first == project_key(Path(path))
    assert first == project_key(Path(path))


def test_the_readable_slug_survives() -> None:
    """Keys stay greppable by a human — objective 2 of the sub-plan.

    A bare digest would satisfy AC-1 and AC-2 perfectly and make
    ``ls ~/.ilk-data/projects`` unreadable, which is how an operator finds a
    project's state today.
    """
    key = project_key(Path("/Users/chad/Projects/github/inluck-net/ilk-skills"))
    assert key.startswith("users-chad-projects-github-inluck-net-ilk-skills"), (
        "the slug prefix must survive verbatim so state directories stay "
        "searchable; got %r" % key
    )


def test_a_key_is_filesystem_safe() -> None:
    """Keys are used as directory names, so the character set is a contract."""
    import re
    for path in COLLIDING_QUARTET + _SEPARATOR_VARIANTS + _CASE_VARIANTS:
        key = project_key(Path(path))
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", key), (
            "%r -> %r is not a safe directory name" % (path, key)
        )
