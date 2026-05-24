---
name: ilk-loop
description: >-
  Resume and drive a multi-step execution plan organised as one MASTER plan +
  several sub-plans, each step recoverable from disk. Use when the user says
  "/ilk", "resume the loop", "continue the plan", "next step", or refers to
  files under `docs/plans/MASTER-*.md`. Also use to set up the convention in
  a new project, add a new sub-plan, or check loop status.
---

# ilk Loop — Master + Sub-Plan execution convention

A lightweight, file-driven workflow for breaking large changesets into
small, resumable steps. Each loop iteration runs in a fresh chat session so
the agent never grows past its context window.

## When to use

- The user invokes any of the slash commands `/ilk`, `/ilk-plan`, or
  `/ilk-lark-tickets`.
- The user says: "resume the loop", "continue the plan", "next step",
  "ship the next sub-plan", "where are we", "loop status".
- The user asks to **plan** something (turn a task description or batch of
  tickets into master + sub-plans).
- The user asks to set up `docs/plans/` in a new project.
- The user asks to add a new sub-plan to an existing master.

## Architecture

```
~/.cursor/skills/ilk-loop/
  SKILL.md                            ← this file
  scripts/
    loop_status.py                    ← universal status checker
  templates/
    README.md                         ← scaffold for <project>/docs/plans/README.md
    subplan-template.md               ← starter sub-plan body

~/.cursor/commands/
  ilk.md                            ← /ilk              -- execute next step
  ilk-plan.md                       ← /ilk-plan         -- plan from a task description
  ilk-lark-tickets.md               ← /ilk-lark-tickets -- plan from Lark 可执行 tickets

<project>/docs/plans/
  README.md                           ← convention reference (copied from template)
  MASTER-YYYY-MM-DD-execution-plan.md ← strategic index for one batch
  YYYY-MM-DD-<short-slug>.md          ← one sub-plan per workstream
```

The skill is project-agnostic: `loop_status.py` walks up from cwd looking
for `docs/plans/MASTER-*.md`, the same way `git` walks up looking for `.git`.

## Front-matter conventions

### Sub-plan

```yaml
---
plan: <short-slug>
status: pending | in-progress | shipped | blocked
current_step: 0           # 0-indexed pointer to the next step to execute
tickets:                  # ticket ids this sub-plan resolves (any tracker)
  - T-2026-0015
  - T-2026-0034
priority: P0 | P1 | P2 | P3
estimated_steps: 9
last_updated: YYYY-MM-DD
---
```

### Master plan

```yaml
---
master_plan: YYYY-MM-DD-execution
batch_date: YYYY-MM-DD
source_status: <ticket-tracker status that fed this batch>
total_tickets: 21
status: pending | in-progress | shipped
current_subplan: YYYY-MM-DD-<slug>   # cached pointer; loop_status verifies
---
```

The master plan body MUST contain a "Sub-plan registry" markdown table
(or any markdown linking to each sub-plan filename). `loop_status.py`
extracts ordering from the appearance order of `YYYY-MM-DD-*.md`
references in the body.

## State machine

```
pending ──(first step started)──> in-progress ──(last step done)──> shipped
                                       │
                                       └─(external dependency)──> blocked
```

A sub-plan is `shipped` iff every step is done AND every listed ticket has
been transitioned to the next workflow state in its tracker (e.g. for
Lark tickets that's `待验证`).

## The loop

```
1. Run loop_status.py.
2. Exit code 0  → done, tell the user, stop.
   Exit code 1  → there is a next pending sub-plan; loop_status prints its path.
3. Read the master plan + the next sub-plan.
4. Execute exactly the next step (or a few consecutive ones if context allows).
5. After each step:
     - commit with message containing  [plan:<slug>#step-N]
     - bump `current_step` in sub-plan front-matter
     - commit:  chore(plans): bump <slug> current_step to <N+1>
6. When current_step reaches estimated_steps:
     - set sub-plan status to `shipped`
     - update last_updated date
     - transition every listed ticket in the tracker to the next state
       (use the lark-tickets skill if it's a Lark Bitable)
     - commit
7. Print loop_status.py output again so the human sees updated state.
8. Exit (let the human start a fresh chat for the next iteration).
```

## Step granularity rules

- **One commit per step.** Atomic, revertable, greppable.
- **Bump current_step in its own commit** so reverting code doesn't desync
  state from intent.
- **Never split mid-step.** Inside a step, you may do many tool calls; the
  step is the unit of recovery, not the file edit.
- **If a step uncovers a new bug**: file it in the tracker as a new ticket
  and add a one-line note under "Out of scope" in the current sub-plan.
  Do NOT silently expand the plan.

## Standard workflows

### 1. Resume / continue (`/ilk`)

This is the default operation. The `/ilk` slash command body codifies it.
You should:

1. Run `python3 ~/.cursor/skills/ilk-loop/scripts/loop_status.py`.
2. Open the chat with one line:
   `Next: <sub-plan> step N of M -- <step summary>. Starting work...`
3. Execute the step per "The loop" above.
4. Print `loop_status.py` output again before stopping.

### 2. Set up ilk-loop in a new project

When the user says "set up ilk-loop here" or similar:

1. Verify cwd is inside a git repo (or `<project>/`).
2. Create `<project>/docs/plans/` if missing.
3. Copy `~/.cursor/skills/ilk-loop/templates/README.md` to
   `<project>/docs/plans/README.md` (the in-repo copy is intentional — it
   makes the convention discoverable to anyone browsing the repo).
4. Ask the user what tickets / scope this first batch covers, then create a
   `MASTER-YYYY-MM-DD-execution-plan.md` from skeleton.
5. Create one or more sub-plan files using
   `~/.cursor/skills/ilk-loop/templates/subplan-template.md`.

### 3. Add a new sub-plan to an existing master

1. Read existing `MASTER-*.md` to understand the workstream layout.
2. Copy `templates/subplan-template.md` to
   `docs/plans/YYYY-MM-DD-<slug>.md`, fill in front-matter and steps.
3. Add a row to the master's "Sub-plan registry" table.
4. Increment master's `total_tickets` if applicable.

### 4. Status check (no execution)

Just run `loop_status.py` and report. Don't load any sub-plan or do work.

### 5. Generate plans from a task description (`/ilk-plan`)

Universal planning entrypoint. Source-agnostic — caller supplies a free-text
task description. Used directly by humans, and indirectly by source adapters
like `/ilk-lark-tickets`.

The agent's job:

1. **Confirm cwd has a project root** (walk up looking for `docs/` or `.git`).
   If `docs/plans/` doesn't exist, scaffold it from the templates first
   (workflow #2 above), then continue.
2. **Read the task description**. If empty, ask the user what to plan.
3. **Read existing plans** in `docs/plans/` (if any) so new work doesn't
   collide with sub-plans already in flight.
4. **Propose a workstream grouping** as a markdown table to the user:
   - Columns: `# | Sub-plan slug | Items | Priority | Why grouped`
   - Show estimated step count per sub-plan
   - Show suggested execution order with brief rationale
   - Note any cross-workstream dependencies
5. **Wait for user approval or edits.** Do NOT write any files yet. Iterate
   on the grouping as the user requests.
6. **Once approved, write the files** in one batch:
   - `MASTER-YYYY-MM-DD-execution-plan.md` — registry table + ordering
     rationale + workstream map
   - One sub-plan file per workstream, using `templates/subplan-template.md`
     as the skeleton. Fill in real per-step bullets, not placeholders.
7. **Commit and push** the new plans:
   ```powershell
   git add docs/plans/
   git commit -m "chore(plans): scaffold <batch-date> master + N sub-plans"
   git push
   ```
8. **Print loop_status.py output** so the user can immediately `/ilk`.

Heuristics for grouping:
- Same module / file cluster → one sub-plan.
- Same feature area but different layers (api+ui) → still one sub-plan;
  layers become consecutive steps inside it.
- Strong dependency between items (item B needs the primitive from item A)
  → same sub-plan; A is an early step, B is a later step.
- Independent items of similar small size → group to amortise context
  loading overhead (rule of thumb: 4-8 items per sub-plan max).
- A single large item with >8 distinct steps → its own sub-plan.

Heuristics for ordering:
- Blockers / bugs that gate other work → first.
- High priority (P0/P1) before low (P2/P3).
- Independent items last.

### 6. Generate plans from Lark tickets (`/ilk-lark-tickets`)

Lark-specific input adapter. Uses the `lark-tickets` skill to fetch
triaged-but-unplanned tickets, then delegates to workflow #5 with those
tickets as the task description.

The agent's job:

1. **Pull all `可执行` tickets** from the project's configured Lark Bitable:
   ```powershell
   python $HOME\.cursor\skills\lark-tickets\scripts\cli.py list --status 可执行 --limit 100
   ```
2. **Cache the list** of `(ticket_id, record_id, title)` for the post-step.
3. **Fetch full content** of each ticket (`cli.py show <record_id>` per
   ticket; parallelise with a small Python helper if there are many).
4. **Format the tickets as a task description** — one section per ticket
   with id, title, type, priority, modules, original description, AI 理解.
5. **Hand off to workflow #5** (the `/ilk-plan` core) using that
   formatted description. The user approval loop applies.
6. **After the plan files are written, committed, and pushed**, perform the
   Lark-specific post-step:
   - For each `(ticket_id, record_id)` in the cache:
     - Determine which sub-plan file the ticket landed in (the `tickets:`
       front-matter list of each sub-plan is the source of truth).
     - Compute `关联 plan` URL:
       `<gitee_blob_base>/docs/plans/<sub-plan-filename>` — get
       `<gitee_blob_base>` from `git remote get-url origin` + branch.
     - Update the ticket:
       ```powershell
       python $HOME\.cursor\skills\lark-tickets\scripts\cli.py update <record_id> `
         --field "关联 plan=<url>" `
         --field "状态=计划中"
       ```
7. **Verify** with `cli.py list --status 计划中` that all expected tickets
   moved.
8. **Print final summary** + `loop_status.py` output.

Tip: for batches of 10+ tickets, write a one-off helper script in
`docs/plans/_update_tickets.py` (delete after use) rather than calling
`cli.py update` 10 times manually. Pattern in `/ilk-lark-tickets.md`.

## Integration with lark-tickets skill

When a sub-plan lists `tickets:` whose ids match the `T-YYYY-NNNN` pattern,
they live in the Lark Bitable served by the `lark-tickets` skill. On
sub-plan ship:

1. For each `ticket_id` in `tickets:`:
   ```powershell
   python $HOME\.cursor\skills\lark-tickets\scripts\cli.py update <record_id> `
     --field "状态=待验证" `
     --field "关联 commit=<comma-separated short hashes>"
   ```
2. Resolve `record_id` from `ticket_id` via `cli.py list` if not already
   known (cache locally if you'll need it again).

## Commit message conventions

| When | Message format |
|---|---|
| Executing step N of sub-plan `<slug>` | `<type>(<scope>): <summary> [plan:<slug>#step-N]` |
| Bumping current_step | `chore(plans): bump <slug> current_step to <N+1>` |
| Shipping a sub-plan | `chore(plans): <slug> shipped [plan:<slug>#ship]` |
| Setting up ilk-loop | `chore(plans): scaffold ilk-loop` |
| Adding a sub-plan | `chore(plans): add <slug> sub-plan` |

`<type>` follows conventional-commits (feat / fix / chore / refactor / docs /
test / build / ci / perf / style).

## Common gotchas

- **Don't run the loop from outside a project.** `loop_status.py` will
  exit 2 and you should tell the user to `cd` into the project.
- **Don't trust master's `current_subplan` blindly** — it's a hint. The
  authoritative "next" is the first sub-plan in registry order whose
  `status != shipped`. `loop_status.py` enforces this.
- **Don't hold on to context across sub-plans.** When one sub-plan ships,
  finish your turn so the human can start a fresh chat for the next one.
  Context economy is the entire point of this convention.
- **PowerShell on Windows**: use `;` not `&&`, and quote paths with spaces.

## Project-side preflight (the standard escalation lever)

A common loop failure mode is "agent set `status: blocked` because it
didn't know how to do step N", where the answer was actually documented
in the project but not surfaced to the agent. Projects that drive the
loop hard should adopt this convention to short-circuit such false
blockers:

1. **A loop primer.** Drop a `docs/loop/PRIMER.md` at the project root
   listing what every fresh agent session needs to know: which
   repos/sub-repos exist, where test accounts live, what the seed
   command is, which routes are authed, dev vs staging endpoints.
   Reference it from MASTER's "Reference reading (loaded by every
   sub-plan)" so the planner and loop both load it.

2. **A machine-readable fixture registry** (optional but recommended):
   `docs/loop/fixtures-registry.{yml,yaml,json}` mapping short keys to
   `{creds_doc, seed_cmd, verify_cmd, helper}` entries. Sub-plan
   `data_prereqs` entries reference these keys directly. The planner's
   step 4c (in `/ilk-plan`) scans for this file and reads it fully.

3. **A preflight script wired as an invariant.** Drop a
   `docs/loop/preflight.sh` (idempotent: ensures seed has run, MCP
   servers connected, test accounts authenticate). Wire it into MASTER
   frontmatter:

   ```yaml
   cross_cutting_invariants:
     - id: loop-preflight
       applies_when:
         kind: substring
         patterns:
           - "src/app/[locale]/(main)"  # any authed route
       assert:
         command: bash docs/loop/preflight.sh
         timeout: 120
   ```

   The `/ilk-plan` QC pass 7b weaves the invariant into every matching
   sub-plan's `local_checks` automatically. When the agent runs that
   sub-plan, `preflight.sh` is the first check — its failure surfaces
   the real issue (seed not run, MCP broken, account expired) instead
   of letting the agent stumble through 5 steps and finally giving up
   with `blocked`.

The `/ilk` slash command's section 6 ("Before setting `status: blocked`")
specifically instructs the executor to read the primer, run the
preflight invariant if present, and retry — *before* flipping a step
to `blocked`. The skill assumes the project has done item 1; items 2-3
are upgrades that reduce false blockers further.

## Concurrent multi-worktree execution

The skill is designed so that **a single repository can host several
independent loops in parallel**, one per `git worktree`. This is the
foundation for working on a feature and a hotfix at the same time
without context-switching, and for higher-level patterns like
multi-agent orchestration or best-of-N parallel attempts.

### How it works

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

### What is isolated per worktree

| | Per worktree | Notes |
|---|---|---|
| `plans/` (MASTER + sub-plans) | ✅ | Each worktree plans its own batch |
| `runtime/last-exit.json` | ✅ | Watchdogs only see their own worktree's loop state |
| `runtime/` queue cursors | ✅ | Each worktree advances its own MASTER queue |
| `logs/` | ✅ | One log stream per worktree |
| Launcher PID file (`~/.ilk-launcher/<key>/running.pid`) | ✅ | `launch.ps1` for one worktree never sees the other as "already running" |
| Launched window title | ✅ | `Start-Process -WindowTitle "<project-name>"` distinguishes them on the desktop |

### What is shared across worktrees (Git's design, not ours)

- **Object store** (`<main-repo>/.git/objects/`) — one copy on disk.
  A commit made in worktree A is immediately readable from worktree B
  (subject to fetch/merge); this is why worktrees beat clones for
  disk + bandwidth.
- **Branch namespace** — branches live once, in the main repo.
  Git enforces "one worktree per checked-out branch" as a hard
  invariant. Two worktrees on different branches coexist fine; trying
  to `git checkout B` from a worktree that has B already checked out
  elsewhere will be rejected.

### Rebase / merge in a worktree

Each worktree has its own `HEAD`, index, and rebase/merge state, so
`git rebase main` inside worktree A does not perturb worktree B.
While a worktree is mid-rebase or has a dirty merge, the loop's next
step commit will likely fail. This is desirable: the failure surfaces
as a `local-checks-stuck` or `merge-conflict` postmortem class, lands
on the watchdog's blacklist, and the watchdog stops auto-resuming so
you can intervene.

### Typical concurrent-use lifecycle

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

### Avoid one footgun

Two worktrees both editing the same file rarely deadlock at commit
time (each has its own index), but the conflict will hit you at
merge/rebase time when both branches eventually reconcile.
Plan concurrent worktrees so they touch **disjoint modules**.
If you want two attempts at the *same* feature, see "Toward
multi-agent and best-of-N" below — that workflow expects only one of
the worktrees to merge.

### Toward multi-agent and best-of-N (forward-looking)

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

## Meta-projects (polyrepo umbrellas)

Some products are a non-git parent directory containing several sibling
git repos that ship together — a backend repo, a portal repo, an ops
repo, a docs repo, and so on. ilk treats such an umbrella as a single
**meta-project**: one MASTER plan, one ship narrative, but each sub-plan
declares which member repo it targets, and the loop cd's into that
member for commits, local_checks, CI waits, and ship reports.

### Opting in

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
python ~/.cursor/skills/ilk-loop/scripts/init_meta_project.py `
  --root C:\path\to\umbrella
```

The marker is recognized via the same lookup engine as `.git` (see
`ilk_paths.py`). Once present, the umbrella is **one** project: plans
land at `~/.ilk-data/projects/<umbrella-key>/`, the launcher launches
one window for the whole umbrella, and `loop_status.py` renders an
extra `repo` column.

### Per-sub-plan routing

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

### What's isolated, what's shared

| | Per umbrella | Notes |
|---|---|---|
| `~/.ilk-data/projects/<umbrella-key>/` | ✅ | One plans/runtime/logs dir for the whole umbrella |
| MASTER + sub-plan files | ✅ | One MASTER references sub-plans across all members |
| PID file, launcher window title | ✅ | Keyed by umbrella name, not member name |
| Git branches & remotes | ❌ | Each member is still an independent git repo with its own branches, PRs, and CI |
| CI runs | ❌ | Each member runs its own CI on its own PR; ship-reports are per sub-plan |
| Worktrees | ❌ | A worktree of one member is just another git repo for ilk; you can give a member's worktree its own line in `.ilk-meta.json` if you want it driven separately |

### Atomic ship is not promised

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

See `skills/ilk-loop/docs/meta-projects.md` for a worked end-to-end
example.

## Loop-shippable verification

The loop runs via Claude Code CLI (`run_ilk_loop_claude.ps1`).
Whether a verification step counts as "loop-shippable" depends on
which MCPs are registered for that CLI. Check at planning time:

```powershell
claude mcp list
```

The set of tools available to the loop = Claude Code's built-ins
(Bash/Edit/Read/Grep/Glob/Task/Write/etc.) PLUS whatever shows up in
`claude mcp list`. Anything outside that set is NOT loop-shippable
and must be moved to a "Manual user verification" section.

### Preferred: browser-enabled loop (Option A)

If the loop will frequently need browser verification, register
`chrome-devtools` MCP **once** for Claude Code CLI (the same MCP your
Cursor uses):

```powershell
claude mcp add chrome-devtools --scope user -- npx chrome-devtools-mcp@latest --browserUrl http://localhost:9222
```

Requires Chrome to be running with `--remote-debugging-port=9222`
(see `~/.cursor/skills/browser-automation/SKILL.md`).

Once registered, the loop has full parity with Cursor: it can
`take_snapshot`, `click`, `type_text`, `evaluate_script`,
`list_network_requests`, `list_console_messages`, etc. Browser-based
ACs become normal loop steps — no manual section needed.

This is the **default assumption** when authoring plans. `/ilk-plan`
should run `claude mcp list` during planning and, if `chrome-devtools`
is present, freely include browser-based verification as in-loop steps.

### Fallback: CLI-only sub-plan (Option B)

Use when `chrome-devtools` (or whatever MCP a verification needs) is
**not** registered for Claude Code CLI. Structure the sub-plan with
two distinct verify sections:

1. **Steps** — 100% completable with the tools `claude mcp list`
   actually shows. The final step ships the plan after either:
   - A pytest run (preferred — covers the contract in-process), AND/OR
   - An ad-hoc verification script the loop authors in
     `<project>/scripts/verify_<topic>.py` that exercises the live HTTP
     wire contract via `requests` + `AccessToken.for_user(user)` (or
     equivalent). The script must always-restore any DB state it
     mutates (use `try/finally`).

2. **Manual user verification (run AFTER the loop ships)** — at the
   bottom of the sub-plan file, separate H2 section. The browser walk
   lives here, with explicit click-by-click instructions, expected
   toasts/responses, and a one-liner re-open instruction
   (`flip status back to in-progress, current_step=N`) for the user
   in case anything fails.

Acceptance criteria split: AC-1..AC-N covers loop-shippable bits;
the final AC ("works in Chrome") moves to the Manual section. The
loop ships once AC-1..N pass; the user takes the browser walk on
their own time.

### Decision matrix

| Sub-plan needs… | `chrome-devtools` registered? | Pattern |
|---|---|---|
| No browser at all | either | Normal sub-plan, no manual section |
| Browser walk | yes | Browser steps stay in loop (Option A) |
| Browser walk | no | Move to Manual section (Option B) |
| Some other MCP not in `claude mcp list` | n/a | Move that step to Manual section |

**Rule of thumb**: prefer Option A whenever you can — fewer human
hand-offs, higher fidelity, and the user only has to tell the agent
"continue the loop" once. Use Option B only when registering the
needed MCP isn't worth it for a one-off verification.

## Mac-specific notes

The bash runner (`run_ilk_loop_claude.sh`) requires `gtimeout` from
GNU `coreutils` for per-iteration wall-clock time-boxing. On macOS:

```bash
brew install coreutils
```

Without `gtimeout` the runner refuses to start with a clear error.
Linux distributions usually ship `timeout` from `coreutils` by default.

## See also

- `~/.cursor/skills/lark-tickets/SKILL.md` — ticket-tracker integration.
- `~/.cursor/commands/ilk.md` — the slash command body that drives the loop.
- `~/.cursor/skills/ilk-loop/scripts/run_ilk_loop_claude.sh` — bash
  runner (macOS / Linux equivalent of `run_ilk_loop_claude.ps1`).
