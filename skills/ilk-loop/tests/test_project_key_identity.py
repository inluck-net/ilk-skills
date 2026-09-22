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

Second section (added 2026-09-22) — project-ROOT resolution, not the key
transform above.  Sub-plan: pin-a-path-as-its-own-project, step 0.  See the
section header below for why the selfmod worktree invariant is pinned here.

Note for step 1 — ``test_project_key_contract.py`` pins the CURRENT transform
as a cross-repo contract (gh-resolve ``reconcile.py:41`` mirrors it, and its
AC-2/AC-3 assert that short paths are the *plain* slug).  Making the hash
unconditional necessarily breaks those vectors; they describe the defect, not
a requirement, and must be updated in the same commit as the fix.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from ilk_paths import (  # noqa: E402
    external_plans_dir,
    find_project_root,
    git_root,
    project_key,
    resolve_project_key,
)


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


# ── Project-root resolution: the selfmod worktree invariant ──────────────────
#
# Sub-plan: pin-a-path-as-its-own-project, step 0.  A different concern from
# the key transform above; it lives here because this module already owns
# project-key identity and the two meet at `resolve_project_key`.
#
# ``ilk_paths.git_root`` resolves a linked worktree back to its main clone by
# parsing the worktree's ``.git`` file.  That is deliberate and load-bearing:
# selfmod worktrees under ``~/.ilk-data/projects/<key>/runtime/`` must resolve
# to the same project key as the original project, so the loop editing its own
# toolkit does not fork its state.
#
# Step 1 adds a ``.ilk-project-root`` marker that makes pinning possible.  The
# default must not move: a directory without the marker resolves exactly as it
# does today.  AC-3 and AC-4 below are that guarantee, pinned BEFORE the seam
# exists.  Both pass against unmodified ``ilk_paths.py`` — these are pins, not
# red-first.

# Marker name, from the sub-plan's pre-resolved design decisions.  Spelled out
# here rather than imported: step 1 adds ``ilk_paths.PIN_MARKER``, and a pin
# that only passed by importing the implementation it constrains would prove
# less than this one does.
PIN_MARKER = ".ilk-project-root"


def _git(*argv: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *argv], cwd=cwd, check=True, capture_output=True, text=True
    )


def _make_plain_repo(path: Path) -> Path:
    """A minimal git repo with one commit, so HEAD exists for `git worktree add`."""
    path.mkdir(parents=True)
    _git("init", cwd=path)
    (path / "README.md").write_text("repo\n", encoding="utf-8")
    _git("add", "README.md", cwd=path)
    _git("commit", "-m", "initial", cwd=path)
    return path


def _make_clone_with_linked_worktree(tmp_path: Path):
    """A real clone plus a `git worktree add` linked worktree beside it."""
    clone = _make_plain_repo(tmp_path / "clone")
    worktree = tmp_path / "linked"
    _git("worktree", "add", str(worktree), cwd=clone)
    return clone, worktree


def test_a_linked_worktree_without_a_marker_resolves_to_its_clone(
    tmp_path: Path,
) -> None:
    """AC-3 — the selfmod invariant, captured before there is any way to break it.

    A caller holding a worktree path gets the CLONE's root and the CLONE's
    key.  Without this, a caller registering a worktree writes plans under the
    clone's key and neither half can dispatch (measured on rezmac, 2026-09-22),
    and the loop's timeout safety net is sent to the wrong tree.
    """
    clone, worktree = _make_clone_with_linked_worktree(tmp_path)
    assert not (worktree / PIN_MARKER).exists(), (
        "precondition: this pin is the no-marker case; the marker is step 1's seam"
    )

    # The clone's own key is the reference the worktree must agree with — and
    # the value step 2 must prove a pin cannot move.
    assert resolve_project_key(clone) == project_key(clone)

    root, kind = find_project_root(worktree)
    assert root is not None, "a linked worktree must resolve to a project root"
    assert root.resolve() == clone.resolve(), (
        "a worktree without a marker must resolve to its clone, not to itself "
        "— selfmod worktrees share the original project's state directory; "
        "got %r for worktree %r" % (root, worktree)
    )
    assert kind == "single"
    assert resolve_project_key(worktree) == project_key(clone), (
        "the worktree's key must be the clone's key, or its plans/runtime/log "
        "state forks away from the project it is editing"
    )


def test_a_plain_repo_with_no_marker_is_unaffected(tmp_path: Path) -> None:
    """AC-4 — an additive seam must not move a repo that carries no marker.

    No repo carries ``.ilk-project-root`` today, so this is the whole existing
    population.  Passing before step 1 and after it is what makes the seam
    additive rather than a behaviour change.
    """
    repo = _make_plain_repo(tmp_path / "plain")
    assert not (repo / PIN_MARKER).exists(), (
        "precondition: this pin is the no-marker case"
    )

    root, kind = find_project_root(repo)
    assert root is not None and root.resolve() == repo.resolve()
    assert kind == "single"
    assert resolve_project_key(repo) == project_key(repo)
    assert git_root(repo) is not None
    assert git_root(repo).resolve() == repo.resolve()


# ── The seam itself (step 2): a pin makes a path its own project ─────────────
#
# Sub-plan: pin-a-path-as-its-own-project, step 2.  AC-1, AC-2 and AC-5, plus
# the property step 0 recorded as its reference — a pin in the worktree must
# not move the clone.  Each test below reuses `_make_clone_with_linked_worktree`
# and differs from the no-marker pins above by exactly one file, which is the
# whole seam.


def test_a_linked_worktree_with_the_marker_resolves_to_itself(tmp_path: Path) -> None:
    """AC-1 — the marker makes the worktree its own project root.

    Paired with `test_a_linked_worktree_without_a_marker_resolves_to_its_clone`:
    same fixture, one file different.  With the marker the worktree resolves to
    itself and reports kind "single"; without it, to its clone.
    """
    clone, worktree = _make_clone_with_linked_worktree(tmp_path)
    (worktree / PIN_MARKER).touch()

    root, kind = find_project_root(worktree)
    assert root is not None and root.resolve() == worktree.resolve(), (
        "a worktree carrying %r must resolve to itself, not to its clone; "
        "got %r for worktree %r" % (PIN_MARKER, root, worktree)
    )
    assert kind == "single"


def test_a_pin_gives_the_worktree_its_own_key_and_plans_dir(tmp_path: Path) -> None:
    """AC-2 — everything keyed off `project_root` follows from the pin.

    A shared key would mean a shared plans dir, runtime dir, log stream and
    lock — the collision this seam exists to let a caller avoid.  `external_plans_dir`
    is asserted on the key's position in the path, not merely on inequality,
    so a fix that keys the plans dir off something other than the pinned root
    cannot pass.
    """
    clone, worktree = _make_clone_with_linked_worktree(tmp_path)
    (worktree / PIN_MARKER).touch()

    clone_key = project_key(clone)
    wt_key = resolve_project_key(worktree)
    assert wt_key is not None and wt_key != clone_key, (
        "a pinned worktree must not share its clone's key; both resolved to %r"
        % (wt_key,)
    )

    wt_plans = external_plans_dir(wt_key)
    clone_plans = external_plans_dir(clone_key)
    assert wt_plans != clone_plans, (
        "the pinned worktree and its clone must not share a plans dir: %r"
        % (wt_plans,)
    )
    assert wt_key in wt_plans.parts, (
        "external_plans_dir must be keyed by the pinned root's key %r; got %r"
        % (wt_key, wt_plans)
    )
    assert clone_key not in wt_plans.parts, (
        "the pinned worktree's plans dir must not be keyed by the clone's key "
        "%r; got %r" % (clone_key, wt_plans)
    )


def test_a_pin_in_the_worktree_does_not_move_the_clone(tmp_path: Path) -> None:
    """The property step 0 recorded as its reference, re-asserted after the pin.

    Pinning is opt-in for the caller holding the worktree path.  It must not
    retroactively re-key the clone the worktree was carved from — the clone's
    state directory is where all its history lives.
    """
    clone, worktree = _make_clone_with_linked_worktree(tmp_path)
    assert resolve_project_key(clone) == project_key(clone), (
        "precondition: step 0's reference property"
    )

    (worktree / PIN_MARKER).touch()

    assert resolve_project_key(clone) == project_key(clone), (
        "adding a pin inside the worktree moved the clone's own key"
    )
    root, _kind = find_project_root(clone)
    assert root is not None and root.resolve() == clone.resolve(), (
        "adding a pin inside the worktree moved the clone's own root: %r" % (root,)
    )


def test_the_cli_reports_the_pinned_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-5 — `ilk_paths.py --start <pinned>` answers about the pin.

    Runs the real CLI entrypoint rather than the functions, because the JSON
    shape is what cross-repo consumers read (Contract 6b), and a library that
    pins while the CLI still reports the clone would be the defect in a
    different costume.  A planted MASTER file makes `find_plans_dir` resolve
    to the external dir, so `resolved_plans_dir` is asserted rather than
    merely present.

    `ILK_DATA_HOME` points at tmp_path so nothing is written under the real
    `~/.ilk-data` — `conftest.py`'s DATA-ROOT LEAK detector is watching for
    exactly that.
    """
    clone, worktree = _make_clone_with_linked_worktree(tmp_path)
    (worktree / PIN_MARKER).touch()

    data_home = tmp_path / "ilk-data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)

    wt_key = project_key(worktree)
    plans = data_home / "projects" / wt_key / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-01-01-execution-plan.md").write_text(
        "master\n", encoding="utf-8"
    )

    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "ilk_paths.py"), "--start", str(worktree)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["project_root"] == str(worktree.resolve())
    assert payload["project_kind"] == "single"
    assert payload["project_key"] == wt_key
    assert payload["project_key"] != project_key(clone), (
        "the CLI must answer about the pin, not the clone"
    )
    assert payload["resolved_plans_dir"] == str(plans), (
        "resolved_plans_dir must follow the pinned root's key; got %r want %r"
        % (payload["resolved_plans_dir"], str(plans))
    )
    assert payload["resolved_source"] == "external"
