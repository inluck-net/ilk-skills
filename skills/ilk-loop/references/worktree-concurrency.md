# Concurrent Multi-Worktree Execution

The skill is designed so that **a single repository can host several
independent loops in parallel**, one per `git worktree`. This is the
foundation for working on a feature and a hotfix at the same time
without context-switching, and for higher-level patterns like
multi-agent orchestration or best-of-N parallel attempts.

## How it works

Every ilk artifact (plans, runtime state, logs, launcher PID file) is
keyed by a `project_key` derived from the absolute path of the `.git`
root containing the current working directory. `ilk_paths.git_root()`
treats `.git` as either a directory **or** a file, which is exactly
what `git worktree add` produces:

```
main repo:    /path/to/proj/        .git/ is a directory
worktree A:   /path/to/proj-feat-x/ .git is a file → "gitdir: …/.git/worktrees/feat-x"
worktree B:   /path/to/proj-fix-y/  .git is a file → "gitdir: …/.git/worktrees/fix-y"
```

Each location resolves to its own `project_key`, so the per-project
directories never collide:

```
~/.ilk-data/projects/
├── path-to-proj/             # main repo
│   ├── plans/                # MASTER-*.md, sub-plans
│   ├── runtime/              # last-exit.json, queue cursors
│   └── logs/
├── path-to-proj-feat-x/      # worktree A — independent universe
│   └── …
└── path-to-proj-fix-y/       # worktree B — independent universe
    └── …
```

> **Windows**: the same layout lives under `%USERPROFILE%\.ilk-data\projects\`,
> e.g. `C:\Users\<you>\.ilk-data\projects\c-path-to-proj-feat-x\plans\`.
> The key derivation lower-cases the path and replaces non-alphanumeric
> characters with hyphens, so `C:\path\to\proj` becomes `c-path-to-proj`.

## What is isolated per worktree

| | Per worktree | Notes |
|---|---|---|
| `plans/` (MASTER + sub-plans) | Yes | Each worktree plans its own batch |
| `runtime/last-exit.json` | Yes | Watchdogs only see their own worktree's loop state |
| `runtime/` queue cursors | Yes | Each worktree advances its own MASTER queue |
| `logs/` | Yes | One log stream per worktree |
| Launcher PID file (`~/.ilk-launcher/<key>/running.pid`) | Yes | `launch.ps1` for one worktree never sees the other as "already running" |
| Launched window title | Yes | `Start-Process -WindowTitle "<project-name>"` distinguishes them on the desktop |

## What is shared across worktrees (Git's design, not ours)

- **Object store** (`<main-repo>/.git/objects/`) — one copy on disk.
  A commit made in worktree A is immediately readable from worktree B
  (subject to fetch/merge); this is why worktrees beat clones for
  disk + bandwidth.
- **Branch namespace** — branches live once, in the main repo.
  Git enforces "one worktree per checked-out branch" as a hard
  invariant. Two worktrees on different branches coexist fine; trying
  to `git checkout B` from a worktree that has B already checked out
  elsewhere will be rejected.

## Rebase / merge in a worktree

Each worktree has its own `HEAD`, index, and rebase/merge state, so
`git rebase main` inside worktree A does not perturb worktree B.
While a worktree is mid-rebase or has a dirty merge, the loop's next
step commit will likely fail. This is desirable: the failure surfaces
as a `local-checks-stuck` or `merge-conflict` postmortem class, lands
on the watchdog's blacklist, and the watchdog stops auto-resuming so
you can intervene.

## Typical concurrent-use lifecycle

```bash
# Day 1 morning — main-line feature
cd /path/to/proj                              # main branch
/ilk-plan "implement user login"              # writes to ~/.ilk-data/projects/path-to-proj/plans/
launch.sh --project-path /path/to/proj        # detached window 1

# Day 1 afternoon — urgent hotfix, do not interrupt the feature
cd /path/to/proj
git worktree add ../proj-hotfix -b hotfix/payment-bug
cd ../proj-hotfix
/ilk-plan "fix payment webhook retry"         # writes to …/path-to-proj-hotfix/plans/
launch.sh --project-path /path/to/proj-hotfix # detached window 2

# Two windows + two watchdogs run in parallel.
# status_all.py shows both rows (if registered in projects.json).

# Day 2 — hotfix loop ships its queue, watchdog exits cleanly.
cd ../proj-hotfix
git push origin hotfix/payment-bug
# open PR, merge, then:
git worktree remove ../proj-hotfix
# Plans + logs at ~/.ilk-data/projects/path-to-proj-hotfix/ are
# preserved for retrospective. Delete that directory whenever you
# want; the main repo's loop is untouched.
```

> **Windows**: replace `launch.sh --project-path …` with
> `& launch.ps1 -ProjectPath …`, and `/path/to/proj` with the
> equivalent `C:\path\to\proj` literal.

## Avoid one footgun

Two worktrees both editing the same file rarely deadlock at commit
time (each has its own index), but the conflict will hit you at
merge/rebase time when both branches eventually reconcile.
Plan concurrent worktrees so they touch **disjoint modules**.
If you want two attempts at the *same* feature, see "Toward
multi-agent and best-of-N" below — that workflow expects only one of
the worktrees to merge.

## Toward multi-agent and best-of-N (forward-looking)

What worktrees give you for free is exactly the primitive needed for
two adjacent patterns:

- **Multi-agent collaboration** — N worktrees, each driven by its own
  `ilk-loop`, working on **different** sub-plans of a shared MASTER.
  Today this works manually: split the MASTER queue across worktrees
  and launch one loop per worktree. A future `ilk-orchestrator` skill
  could partition automatically and gate on cross-cutting invariants
  before allowing each branch to merge.
- **Best-of-N attempts** — N worktrees, each running the *same*
  sub-plan with a different model / temperature / prompt variant.
  Whichever finishes first with passing `local_checks` and the
  cleanest reviewer score wins; the other worktrees are discarded.
  Today, this is "do it by hand": create N worktrees, launch the same
  plan in each, compare on ship. A future skill (`ilk-bestof` or
  similar) would automate the picking step.

These are not implemented in v0.1. The point is that the **isolation
contract is already in place** — adding orchestration on top is a
pure-coordination problem, not a refactor.
