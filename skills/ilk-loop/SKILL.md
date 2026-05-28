---
name: ilk-loop
description: >-
  Resume and drive a multi-step execution plan organised as one MASTER plan +
  several sub-plans, each step recoverable from disk. Use when the user says
  "/ilk", "resume the loop", "continue the plan", "next step", or refers to
  files under `docs/plans/MASTER-*.md`. Also use to set up the convention in
  a new project, add a new sub-plan, or check loop status. Works across
  Cursor, Claude Code, and Codex.
---

# ilk Loop — Master + Sub-Plan execution convention

A lightweight, file-driven workflow for breaking large changesets into
small, resumable steps. Each loop iteration runs in a fresh chat session so
the agent never grows past its context window.

## When to use

- The user invokes any of the slash commands `/ilk`, `/ilk-plan`, or
  `/ilk-lark-tickets` (Cursor, Claude Code, or Codex).
- The user says: "resume the loop", "continue the plan", "next step",
  "ship the next sub-plan", "where are we", "loop status".
- The user asks to **plan** something (turn a task description or batch of
  tickets into master + sub-plans).
- The user asks to set up `docs/plans/` in a new project.
- The user asks to add a new sub-plan to an existing master.
- Codex users may say: "continue the ilk loop", "run the next step",
  "plan this task", or "ilk plan" without a slash prefix.

## Architecture

`<skill-root>` below means the installed skills base directory —
`~/.cursor/skills/` (Cursor), `~/.claude/skills/` (Claude Code), or
`~/.codex/skills/` (Codex) — depending on the host agent. The companion
`commands/` directory is its sibling, e.g. `~/.cursor/commands/`.

```
<skill-root>/ilk-loop/
  SKILL.md                            ← this file
  scripts/
    loop_status.py                    ← universal status checker
  templates/
    README.md                         ← scaffold for <project>/docs/plans/README.md
    subplan-template.md               ← starter sub-plan body

<skill-root>/../commands/
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

## Runtime state layout

All per-project runtime artifacts live outside the project tree under:

```
~/.ilk-data/projects/<project-key>/
    plans/         # MASTER-*.md and sub-plans
    runtime/       # last-exit.json, queue cursors
    runtime/launcher/   # PID files, launch metadata, MCP worker configs
    runtime/watchdog/   # watchdog PID, activity log
    logs/          # per-project loop output
```

To discover the exact paths for the project in the current directory:

```bash
python3 <skill-root>/ilk-loop/scripts/ilk_paths.py --start . --where
```

If you have legacy in-project `.ilk-launcher/` or `.ilk-watchdog/` directories
from an earlier version, migrate them once with:

```bash
python3 <skill-root>/../tools/migration/migrate_project_runtime_dirs.py --project . --apply
```

This keeps the project repo clean and avoids accidental commits of skill
artifacts.

### Preserving active-run evidence before log cleanup

Before deleting any legacy `<skill-root>/ilk-loop/logs/` directory or
migrating log files, preserve the current run's evidence so
`/ilk-feedback` can still generate a postmortem:

```bash
python3 <skill-root>/ilk-loop/scripts/preserve_active_run.py --project-path .
```

This copies per-iteration logs, JSONL entries, the sentinel, and launcher
metadata into `~/.ilk-data/projects/<key>/logs/archive/<run-id>/`. The
helper is idempotent — running it twice does not duplicate files.

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

1. Run `python3 <skill-root>/ilk-loop/scripts/loop_status.py`.
2. Open the chat with one line:
   `Next: <sub-plan> step N of M -- <step summary>. Starting work...`
3. Execute the step per "The loop" above.
4. Print `loop_status.py` output again before stopping.

### 2. Set up ilk-loop in a new project

When the user says "set up ilk-loop here" or similar:

1. Verify cwd is inside a git repo (or `<project>/`).
2. Create `<project>/docs/plans/` if missing.
3. Copy `<skill-root>/ilk-loop/templates/README.md` to
   `<project>/docs/plans/README.md` (the in-repo copy is intentional — it
   makes the convention discoverable to anyone browsing the repo).
4. Ask the user what tickets / scope this first batch covers, then create a
   `MASTER-YYYY-MM-DD-execution-plan.md` from skeleton.
5. Create one or more sub-plan files using
   `<skill-root>/ilk-loop/templates/subplan-template.md`.

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

## Advanced topics (references)

- **Concurrent multi-worktree execution** — run independent loops in
  parallel via `git worktree`. See
  [references/worktree-concurrency.md](references/worktree-concurrency.md).
- **Meta-projects (polyrepo umbrellas)** — drive multiple repos from one
  MASTER plan with per-sub-plan routing. See
  [references/meta-projects.md](references/meta-projects.md).
- **Loop-shippable verification** — decide whether browser/MCP steps
  belong in-loop or in a manual section. See
  [references/loop-shippable-verification.md](references/loop-shippable-verification.md).

## Mac-specific notes

The bash runner (`run_ilk_loop_claude.sh`) requires `gtimeout` from
GNU `coreutils` for per-iteration wall-clock time-boxing. On macOS:

```bash
brew install coreutils
```

Without `gtimeout` the runner refuses to start with a clear error.
Linux distributions usually ship `timeout` from `coreutils` by default.

## See also

- `<skill-root>/lark-tickets/SKILL.md` — ticket-tracker integration.
- `<skill-root>/../commands/ilk.md` — the slash command body that drives the loop.
- `<skill-root>/ilk-loop/scripts/run_ilk_loop_claude.sh` — bash
  runner (macOS / Linux equivalent of `run_ilk_loop_claude.ps1`).
