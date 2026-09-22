# Retro 2026-09-22 — a worktree that could never land again

The 12-sub-plan `provider-switching-tier12` batch did all of its work, shipped
all twelve sub-plans, and then could not be released — because the step that
lands a self-modifying batch back into the clone had latched into a state where
it could never succeed again, and nothing in the system could tell the
difference between "refused once" and "refused forever".

Sibling retro, same family, previous day:
[`retro-2026-09-21-the-gate-cannot-pass-from-the-worktree.md`](retro-2026-09-21-the-gate-cannot-pass-from-the-worktree.md)
— there a guard failed a session on its own cache. Here a guard failed every
future run on its own marker file. Both convict correct work.

## What happened

| Time | Event |
|---|---|
| 2026-09-17 | `90153a3` lands merge-back at the ship transition |
| 2026-09-17 14:04 | run `20260917-140430` — the first merge-back ever attempted — refuses, exit 3 |
| 2026-09-17 → 09-20 | six fixes to `selfmod_worktree.py` in four days |
| 2026-09-22 07:47 | worktree created, marker records clone HEAD `4e84abe` |
| 2026-09-22 ~08:0x | fixture fixes committed **into the clone**, moving it to `b7b8a37` |
| 2026-09-22 08:39 | merge refuses, exit 3; batch stranded; 12/12 shipped, 0 released |

The refusal text, from `logs/launcher/…-20260922-082734.log:215-217`:

```
[selfmod] MERGE REFUSED: clone HEAD moved since worktree creation.
ERROR: Branch 'selfmod-batch' moved since worktree was created
       (4e84abea→b7b8a37e). This is a condition to report, not to overwrite.
[selfmod] merge exited 3 — batch did not land.
```

## The numbers

Measured across this project's launcher logs and run-exit records:

| Metric | Value |
|---|---|
| Run exits ending `selfmod_merge_failed` | **49 of 77** |
| Longest consecutive streak | **22 runs** |
| Run exits ever ending `all-shipped` | **1 of 77** |
| Failed merge attempts | 57 (43 × exit 3, 8 × exit 2, 6 × exit 5) |
| Successful merge attempts | 27 |

## Root cause: a guard stronger than the operation it guards

`_do_merge` lands the batch with `git merge --ff-only`. That operation has
exactly one precondition: **the clone's HEAD must be reachable from the
worktree's HEAD.** The guard, however, asked a different and strictly stronger
question — SHA equality against a value recorded at worktree *creation*:

```python
# selfmod_worktree.py:629-637 (before)
head_at_creation = self._head_at_creation
if head_at_creation is None:
    head_at_creation = self._read_saved_head()
if check_branch and head_at_creation is not None:
    current_sha = _resolve_head_sha(self.repo_path)
    if current_sha != head_at_creation:
        raise BranchMovedError(...)
```

On its own that is merely conservative. It became a latch because of a second,
separately reasonable decision — `create()` is idempotent, and on reuse it
re-reads the persisted marker rather than refreshing it:

```python
# selfmod_worktree.py:491-494
if self._is_valid_worktree():
    logger.info("Reusing existing worktree at %s", self.worktree_path)
    self._head_at_creation = self._read_saved_head()   # never refreshed
    return
```

Compose the two and the failure is permanent by construction:

1. A merge succeeds and **fast-forwards the clone to the worktree tip**.
2. The marker still names the *original* SHA.
3. The next run reuses the worktree, reads the stale marker, and refuses —
   even though the clone is now a strict ancestor and the fast-forward is
   clean.
4. Every subsequent run repeats step 3. Nothing short of deleting the
   worktree clears it, and the merge CLI has no force flag by design.

The live evidence at the time of diagnosis:

```
selfmod-batch.head-at-creation  = 4e84abe   (written 07:47, at creation)
clone HEAD                      = b7b8a37   (moved since)
```

### Why it was hard to see

The latch is invisible from any single run. Each failure is individually
*correct* — the clone really had moved, and refusing to overwrite really is
the safe default. Only the denominator shows it: 49 of 77 exits, one streak of
22. A prior session on 2026-09-21 had even written down the mechanism —
*"Until landed, every new run reuses this unmerged worktree"* — and it was
still read as a condition to resolve by hand rather than a defect to fix.

## The damage

Because the merge never landed, the batch's verification ran against the
worktree tree while the fixes lived in the clone. The batch-gate record was
written with `head_sha: bd266b6` and `tree_sha: f76bd0ab` — a tree byte-identical
to the pre-fix merge base, with **0 files differing**. Four separate "green"
results were produced about a tree that did not contain the corrections they
were meant to confirm.

The recovery attempts left their own debris: an `archive/…-use-switches-orphans`
branch, five `backup/*` refs, and a `main` that had five commits replayed onto
it under different SHAs — so `origin/main` was no longer an ancestor of the
batch line, and landing it needed a history rewrite rather than a fast-forward.
None of that is a second defect. It is all one defect's wake.

## The fix

Ask the question the operation actually needs, and keep no state to go stale:

```python
# selfmod_worktree.py (after)
if check_branch:
    current_sha  = _resolve_head_sha(self.repo_path)
    worktree_sha = _resolve_head_sha(self.worktree_path)
    diverged = (not _is_ancestor(self.repo_path, current_sha, worktree_sha)
                and not _is_ancestor(self.repo_path, worktree_sha, current_sha))
    if diverged:
        raise BranchMovedError(...)
```

- clone HEAD reachable from worktree HEAD → fast-forward is safe
- worktree HEAD reachable from clone HEAD → already landed, nothing to do
- neither → the genuine divergence the guard exists for

Both pre-existing divergence tests still refuse, unchanged. The new regression
test reproduces the latch directly: merge once, reuse the worktree, commit
again, merge again. Without the fix the second merge raises
`BranchMovedError`; with it, the work lands.

`_is_ancestor` treats any `git merge-base --is-ancestor` exit other than 0 or 1
as a broken probe and raises, so "not an ancestor" and "could not tell" are
never byte-identical — the same fail-closed invariant the liveness probe
already follows.

## What to carry forward

- **A guard should ask exactly the question its operation needs.** Anything
  stronger is not "extra safety"; it is a distinct predicate with its own
  failure modes, and here it was one that could not be satisfied again.
- **Persisted state in a guard is a latch waiting to happen.** The marker file
  existed only to survive across CLI invocations. Ancestry needed no marker at
  all.
- **A repeated terminal state is a design defect, not an incident.** The
  second identical failure was the signal; the twenty-second was not more
  informative, only more expensive.
