# Meta-Projects (Polyrepo Umbrellas)

Some products are a non-git parent directory containing several sibling
git repos that ship together — a backend repo, a portal repo, an ops
repo, a docs repo, and so on. ilk treats such an umbrella as a single
**meta-project**: one MASTER plan, one ship narrative, but each sub-plan
declares which member repo it targets, and the loop cd's into that
member for commits, local_checks, CI waits, and ship reports.

## Opting in

Drop a `.ilk-meta.json` at the umbrella root:

```json
{
  "name": "myproj",
  "repos": [
    { "name": "api",    "path": "api" },
    { "name": "portal", "path": "portal" },
    { "name": "ops",    "path": "ops" },
    { "name": "docs",   "path": "docs" }
  ]
}
```

Or scaffold it from disk:

```powershell
python <skill-root>/ilk-loop/scripts/init_meta_project.py `
  --root C:\path\to\umbrella
```

The marker is recognized via the same lookup engine as `.git` (see
`ilk_paths.py`). Once present, the umbrella is **one** project: plans
land at `~/.ilk-data/projects/<umbrella-key>/`, the launcher launches
one window for the whole umbrella, and `loop_status.py` renders an
extra `repo` column.

## Per-sub-plan routing

Every sub-plan in a meta project MUST declare:

```yaml
---
plan: <slug>
repo: api                  # one of the names in .ilk-meta.json
status: pending
...
---
```

The loop driver uses this field to switch cwd before running git
operations and local_checks. A missing or unknown `repo:` makes the
relevant scripts refuse to run that sub-plan (exit 2 with a clear
error) — better to fail loudly than to commit into the wrong repo.

Cross-repo sub-plans are not supported by convention: keep one sub-plan
= one repo, and coordinate across repos at the MASTER level using
`depends_on:`. A "change a shared protocol and both consumers" feature
becomes three sub-plans: protocol change in repo A, consumer update in
repo B (depends on A shipped), consumer update in repo C (depends on
A shipped).

## What's isolated, what's shared

| | Per umbrella | Notes |
|---|---|---|
| `~/.ilk-data/projects/<umbrella-key>/` | Yes | One plans/runtime/logs dir for the whole umbrella |
| MASTER + sub-plan files | Yes | One MASTER references sub-plans across all members |
| PID file, launcher window title | Yes | Keyed by umbrella name, not member name |
| Git branches & remotes | No | Each member is still an independent git repo with its own branches, PRs, and CI |
| CI runs | No | Each member runs its own CI on its own PR; ship-reports are per sub-plan |
| Worktrees | No | A worktree of one member is just another git repo for ilk; you can give a member's worktree its own line in `.ilk-meta.json` if you want it driven separately |

## Atomic ship is not promised

Two PRs in two repos won't merge atomically. If sub-plan A in repo X
merges but sub-plan B in repo Y fails CI, repo X is on main with a
change that assumes Y will follow. This is the polyrepo trade — ilk
will not pretend otherwise. Two real coping strategies:

- **Feature-flag each PR** so independent ship is safe. The convention
  is small enough that you can write the flag-removal as the last
  sub-plan in the MASTER (after all feature sub-plans ship).
- **Order risk last** — put the riskiest member's sub-plan at the end
  of the MASTER's `depends_on:` graph; if it fails CI, the safer
  changes are already merged and need no rollback.

## Worked end-to-end example

See `skills/ilk-loop/docs/meta-projects.md` for a worked end-to-end
example.
