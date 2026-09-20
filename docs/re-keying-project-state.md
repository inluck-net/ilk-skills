# Re-keying project state — an operator step on a quiet box

`ilk_paths.project_key` changed in MASTER-2026-09-08b (C1). Every project's
on-disk state lives at `ilk_data_root()/projects/<key>/`, so a changed key
points every tool at a new, empty directory until the state is moved.

**Running the migration is an operator step. It is not part of any loop, and it
is unsafe while a loop is live.**

## What changed

The old key appended a 7-char sha1 only when the lowercased path slug exceeded
80 characters. Below that cap the lossy slug *was* the key, so distinct paths
collided — `app-one`, `app_one`, `app/one` and `APP.ONE` under one parent all
produced `…-app-one`. The new key appends the hash unconditionally and takes it
over the exact, **case-preserving** absolute path.

Which keys actually change, measured 2026-09-20 over the 55 registered projects:

| case | changes? |
|---|---|
| slug under the cap | **always** — it gains a suffix it never had |
| over the cap, path contains an uppercase character | **yes** — the hash input stopped being lowercased |
| over the cap, path is all lowercase | **no** — reported `unchanged-key`, nothing is renamed |

All 11 projects that had state on disk changed, because every real path on this
host begins `/Users/…`. On Linux an over-cap `/home/...` project may keep its
key; the migration handles that case rather than renaming a directory onto
itself.

## Running it

```bash
python3 skills/ilk-loop/scripts/migrate_project_keys.py            # dry run
python3 skills/ilk-loop/scripts/migrate_project_keys.py --json     # dry run, machine-readable
python3 skills/ilk-loop/scripts/migrate_project_keys.py --apply    # perform the renames
```

Exit codes: `0` clean, `1` at least one project refused, `2` unsafe to apply
(a loop is live, liveness unknown, or the registry is unreadable).

### Quiet box, and what "quiet" means

`--apply` refuses while any ilk runner process is live, because a loop resolves
its paths through `project_key` *mid-run*: renaming under it strands the run
with a half-open state directory. Liveness comes from
`selfmod_worktree._find_live_ilk_pids` — `pgrep` exit 1 means none, exit >1
raises, and a raised probe refuses rather than reading as zero.

Before applying:

1. `/ilk-stop` for every project with a live run, **and** stop the scheduler —
   it is launchd `KeepAlive`-supervised on this Mac, so `launchctl bootout` then
   `bootstrap`, not `kill`. De-queue any master first: `stop.sh` killing the
   supervised scheduler triggers a re-dispatch.
2. Re-run the dry run and confirm `0 refused`.
3. Apply, then repair git worktrees (below), then restart the scheduler.

There is a second host. `rezmac` has its own independent ilk install with its
own `~/.ilk-data`; the migration must be run there separately, on its own quiet
box. The registries are not shared.

### It cannot be run from inside a directory it moves

Two of the 11 moves hold live git worktrees under
`runtime/launcher/worktrees/`. A self-modifying batch executes *from inside*
`ilk-skills`'s own worktrees directory — that session cannot apply the
migration to itself. Run it from an ordinary shell whose cwd is outside
`~/.ilk-data`.

### Git worktree registrations need repair afterwards

Git stores absolute paths on both sides of a worktree registration (the
worktree's `.git` file, and `.git/worktrees/<name>/gitdir` in the main repo), so
the rename invalidates them. The dry run flags every source that holds
worktrees. After applying:

```bash
git -C <repo> worktree repair    # for each flagged repo
git -C <repo> worktree prune     # for worktrees that are genuinely gone
```

## What it refuses, and why refusal is the right answer

| refusal | meaning |
|---|---|
| `shared-legacy-dir` | two registered projects collided under the old key and share one state directory. It holds two histories interleaved and no rule can split them. **Both sides are refused by name**; split by hand. |
| `destination-exists` | source and destination both present. Moving would merge two state trees. |
| `duplicate-destination` | two projects computed the same new key. Defensive — the new construction makes this impossible; if it ever fires, the key function is broken, not the tree. |

One refusal blocks the whole `--apply`. A partly-migrated tree leaves no single
command that describes its state, which is worse than not starting.

The migration is **registry-driven, never pattern-driven**. `projects/` also
holds hundreds of pytest `tmp_path` debris directories; a pattern sweep would
move those too. Anything not named in the launcher's `projects.json` is counted
and left alone.

## Before the migration runs, the tree is slow, not merely stale

This is the part that matters for scheduling the operator step, and it is not
obvious from the key change alone.

`plan_lint.py:2747-2764` scopes its `gate_cost` scan to the project key, but
only `if key and (ilk_data_root()/"projects"/key).is_dir()`. Between deploying
the new `project_key` and running the migration that directory does not exist,
so **every** `plan_lint` invocation silently falls back to the unscoped scan —
which its own comment calls "the slowest path of all". Measured 2026-09-20 at
this commit: one CLI invocation **16.5 s** (resolution itself is 0.001 s; the
cost is entirely the unscoped `gate_cost`). `plan_lint` runs once per gated
sub-plan in the loop and once per assertion across ~25 test files, so
`skills/ilk-loop/tests/test_plan_lint_one_branch.py` went from **10 passed in
2.6 s** (key reverted) to **4 failed, 6 passed in 264 s** at this commit, the
failures being 30 s and 120 s subprocess timeouts rather than wrong answers.

Two consequences:

* **The batch cannot self-verify before the migration runs.** A full suite in
  this window measures timeouts, not defects.
* **A read-side fallback in `find_plans_dir` alone would not fix this.** That
  path does not call `find_plans_dir`; it calls `project_key` and stats the
  directory itself. Any legacy-key compatibility shim has to cover both.

## Consumers outside this repo

`project_key` is mirrored, not imported, by gh-resolve `reconcile.py:41`. That
mirror still computes the old key, so it now diverges at **every** path length
rather than only above 80 characters — a widening of a break that already
existed. `reconcile.py`'s own docstring records 0-of-5 unreachable exit records
on rezmac from the last time the mirror drifted.

The fix is not to re-mirror the new construction. `plan_lint`'s `--project-key`
CLI resolves the key from the one implementation that owns it; porting
gh-resolve onto it removes the mirror, and the duplicated transform, for good.

`skills/ilk-runner/scripts/preflight.ps1:108,223` uses `Split-Path -Leaf` on the
project root as a "key". That was never the real key and is unaffected —
pre-existing and out of scope here, noted so a reader does not mistake it for a
fourth mirror. Otherwise there are **0** shell or PowerShell reimplementations
of the transform: all 12 non-Python files that reference the key shell out to
the Python CLI/JSON or derive it from an already-resolved path.

## The 80-char boundary, and a bug it already caused

The total key length is still 80 (`_KEY_SLUG_MAX = 80 - 1 - 7`) — carried over,
not re-chosen, so consumers sized around it keep working.

What has changed is that the boundary is no longer load-bearing for *identity*.
It now only decides how much human-readable prefix survives; the hash carries
identity at every length. This matters for the truncation bug recorded against
`status_all.py`, which reported `repo_path: None` for over-length slugs because
the old transform was one-way and nothing could invert it. That is still true —
the slug remains lossy and uninvertible — so the fix there is still to resolve
the path from the registry rather than to parse it out of the key. The
difference is that the failure is no longer confined to keys over 80 characters:
**every** key now carries a suffix, so any code that tried to treat a short key
as a recoverable path was already wrong and now fails uniformly instead of
intermittently.

## Test files that hard-code the old key

These read `~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills`
as a literal and will stop finding it once the migration moves it. They are in
no step's gate and are not fixed by this sub-plan:

* `skills/ilk-loop/tests/test_plan_lint_gate_budget.py:226`
* `skills/ilk-loop/tests/test_plan_lint_one_branch.py:291`
* `skills/ilk-loop/tests/test_suite_gate_recognises_the_resolver.py:38`

All three currently `skip` or assert against an ambient path; the correct fix is
to compute the key through `ilk_paths.project_key` rather than to re-type it.
