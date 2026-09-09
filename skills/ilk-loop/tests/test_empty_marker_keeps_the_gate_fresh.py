"""Red-first: a commit that changes no files must not invalidate the gate.

The batch gate certifies CODE. `validate_record` compares `head_sha` by exact
equality, so any commit invalidates it — including one that changes zero files.

That is not hypothetical: `templates/batch-verification-subplan.md:234-235`
*mandates* an empty marker commit (`git commit --allow-empty`) at step 0, and
another at step 1. Measured on this repo's own 08d batch, both verification
commits carry tree `ea5182415993`, identical to their parent, 0 files changed.
So the verification sub-plan cannot record its own progress without moving HEAD,
and moving HEAD invalidates the proof of every sub-plan verified before it.

On ilk-skills this never bit, because `/ilk-ship` runs the gate LAST. In the
resolver flow the gate runs during the work sub-plan and the markers land after
it, so every resolver batch parks at `shipped-unproven`. Confirmed by the same
failure here on 2026-09-09: cutting v0.9.91, the gate passed at `dd72f31`, one
commit followed, and the record went stale — remedied only by paying a second
full suite run to move a SHA forward over code the suite could not tell had
changed.

**Why a stored field rather than resolving the recorded sha's tree.** Resolving
`<recorded_sha>^{tree}` would rescue records already on disk, which is tempting.
It is also unsafe: a foreign writer exists — a worker session authored a
`verdict: pass` record for #4824 by hand — and resolving any record's tree would
let a hand-authored one validate whenever its tree happened to match. A stored
`tree_sha` is emitted only by `write_record`, so it doubles as a **provenance
marker**: a record without it falls back to strict SHA comparison and a forged
record stays rejected exactly as today.

AC-1  record carries tree_sha, HEAD moved by an empty commit -> fresh
AC-2  record carries tree_sha, tree genuinely differs        -> stale_head
AC-3  record lacks tree_sha (legacy/foreign)                 -> strict SHA, unchanged
AC-4  write_record emits tree_sha and a writer marker
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

INVOCATION = "python3 -m pytest -q"


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _write(tmp_path: Path, **fields) -> Path:
    p = tmp_path / "batch-gate.json"
    p.write_text(json.dumps(fields), encoding="utf-8")
    return p


# ── AC-1 ─────────────────────────────────────────────────────────────────────

def test_empty_marker_commit_keeps_the_record_fresh(tmp_path: Path) -> None:
    """The mandated empty marker must not invalidate a gate it did not change."""
    from batch_gate import validate_record

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")
    gated_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    # The marker the batch-verification template mandates.
    _git(repo, "commit", "-q", "--allow-empty", "-m",
         "test(verify): record full suite result [plan:x#step-0]")
    new_sha = _git(repo, "rev-parse", "HEAD")
    new_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    assert new_sha != gated_sha, "the marker must move HEAD (that is the problem)"
    assert new_tree == gated_tree, "an empty commit must preserve the tree"

    p = _write(tmp_path, verdict="pass", head_sha=gated_sha,
               invocation=INVOCATION, timestamp="2026-09-09T10:00:00+08:00",
               tree_sha=gated_tree)

    result = validate_record(p, new_sha, INVOCATION, expected_tree_sha=new_tree)

    assert result == "fresh", (
        "a commit changing zero files must not invalidate a statement about "
        f"the code; got {result!r}. The gate certifies content, and the "
        "content is byte-identical."
    )


# ── AC-2 ─────────────────────────────────────────────────────────────────────

def test_a_real_change_still_goes_stale(tmp_path: Path) -> None:
    """Tree comparison must not become a way to skip staleness entirely."""
    from batch_gate import validate_record

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")
    gated_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    (repo / "f.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "real change")
    new_sha = _git(repo, "rev-parse", "HEAD")
    new_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    assert new_tree != gated_tree

    p = _write(tmp_path, verdict="pass", head_sha=gated_sha,
               invocation=INVOCATION, timestamp="2026-09-09T10:00:00+08:00",
               tree_sha=gated_tree)

    assert validate_record(
        p, new_sha, INVOCATION, expected_tree_sha=new_tree,
    ) == "stale_head", "a changed tree must still invalidate the record"


# ── AC-3 ─────────────────────────────────────────────────────────────────────

def test_record_without_tree_sha_keeps_strict_sha_comparison(
    tmp_path: Path,
) -> None:
    """No tree_sha ⇒ today's behaviour. This is the provenance property.

    Only `write_record` emits `tree_sha`. A record lacking it is legacy or
    foreign, and a foreign one must stay rejected — the #4824 forgery had a
    genuinely-current head_sha, so anything that loosened comparison for
    unmarked records would have let it validate.
    """
    from batch_gate import validate_record

    repo = _repo(tmp_path)
    gated_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "marker")
    new_sha = _git(repo, "rev-parse", "HEAD")
    new_tree = _git(repo, "rev-parse", "HEAD^{tree}")

    p = _write(tmp_path, verdict="pass", head_sha=gated_sha,
               invocation=INVOCATION, timestamp="2026-09-09T10:00:00+08:00")

    assert validate_record(
        p, new_sha, INVOCATION, expected_tree_sha=new_tree,
    ) == "stale_head", (
        "a record with no tree_sha must fall back to strict SHA comparison — "
        "loosening it for unmarked records would accept hand-authored ones"
    )


# ── AC-4 ─────────────────────────────────────────────────────────────────────

def test_write_record_emits_tree_sha_and_writer(tmp_path: Path) -> None:
    """The real writer must mark its own output, or provenance is unavailable."""
    from batch_gate import BatchGateRecord, write_record

    runtime = tmp_path / "rt"
    rec = BatchGateRecord(
        verdict="pass",
        head_sha="a" * 40,
        invocation=INVOCATION,
        timestamp="2026-09-09T10:00:00+08:00",
        tree_sha="b" * 40,
        writer="batch_gate.py",
    )
    p = write_record(rec, runtime)
    data = json.loads(p.read_text(encoding="utf-8"))

    assert data.get("tree_sha") == "b" * 40, (
        f"write_record must persist tree_sha; got {data!r}"
    )
    assert data.get("writer"), (
        "write_record must mark its output so a reader can tell a real record "
        f"from a hand-authored one; got {data!r}"
    )
