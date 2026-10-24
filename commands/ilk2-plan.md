Plan a new batch of work for an ilk2 project, from a free-text task
description supplied by the user (anything after `/ilk2-plan` in the
chat input is the task description).

This is the **ilk2-native planner**, built beside `/ilk-plan` until the
switch (docs/07 §7.4.5). It keeps V1's authoring layout — a MASTER plus
one file per sub-plan, `### Step N — title` sections, `local_checks`
YAML blocks — because ilk2 imports that format (docs/07 D8). What
changes is where drafts live, how the plan is delivered, and which rules
are enforced: this planner drafts under `~/.ilk2/plans/`, delivers with
`ilk2 submit`, and never touches V1's paths, scripts or runner.

**Plans live OUTSIDE the project repo** so the project's git history
stays clean of plan artifacts. Drafts live under
`~/.ilk2/plans/<project>/<batch-id>/` — never in the repo, and
never in V1's external plans tree.

Follow these steps in order. Do NOT skip the user-approval gate unless
the operator passed `--yes` or `--draft` (see Arguments below).

## Paths

`<skill-root>` is this clone's `skills/` directory — the one beside the
`commands/` directory that holds this file's real path. It is never V1's
`~/.ilk/current/skills`.

Resolve that real path through whichever agent's link exists, first hit
wins: `~/.claude/commands/ilk2-plan.md`, `~/.codex/commands/ilk2-plan.md`,
`~/.cursor/commands/ilk2-plan.md`, then this command's own skill folders
`~/.codex/skills/ilk2-plan/SKILL.md` and
`~/.cursor/skills/ilk2-plan/SKILL.md`. With none of them installed the block
exits non-zero, naming the paths it tried.

```bash
SKILL_ROOT=""
for _p in \
  "$HOME/.claude/commands/ilk2-plan.md" \
  "$HOME/.codex/commands/ilk2-plan.md" \
  "$HOME/.cursor/commands/ilk2-plan.md"
do
  [ -e "$_p" ] || continue
  SKILL_ROOT="$(cd "$(dirname "$(realpath "$_p")")/../skills" && pwd)"
  if [ -n "$SKILL_ROOT" ]; then break; fi
done
if [ -z "$SKILL_ROOT" ]; then
  for _p in \
    "$HOME/.codex/skills/ilk2-plan/SKILL.md" \
    "$HOME/.cursor/skills/ilk2-plan/SKILL.md"
  do
    [ -e "$_p" ] || continue
    SKILL_ROOT="$(cd "$(dirname "$(realpath "$_p")")/.." && pwd)"
    if [ -n "$SKILL_ROOT" ]; then break; fi
  done
fi
if [ -z "$SKILL_ROOT" ]; then
  echo "error: cannot resolve SKILL_ROOT for ilk2-plan; tried:" >&2
  echo "  $HOME/.claude/commands/ilk2-plan.md" >&2
  echo "  $HOME/.codex/commands/ilk2-plan.md" >&2
  echo "  $HOME/.cursor/commands/ilk2-plan.md" >&2
  echo "  $HOME/.codex/skills/ilk2-plan/SKILL.md" >&2
  echo "  $HOME/.cursor/skills/ilk2-plan/SKILL.md" >&2
  exit 1
fi
```

## Arguments

- `--yes` / `-y` — skip the step-5 approval wait for this invocation.
  The full grouping proposal is still printed; only the pause-and-ask
  is skipped. Everything after the flag is the task description.
  A flag, not a timer — a timeout cannot distinguish consent from
  absence, and makes the same plan approve differently depending on
  whether you stepped away.
- `--draft` — write the files, run `plan_check.py` and
  `ilk2 submit --check`, report, and stop: no approval wait and no submit.
  The files stay on disk as an unsubmitted draft; delivery later is the
  same `ilk2 submit` with the same `--meta`. Everything after the flag is
  the task description. Like `--yes`, it is a flag the operator typed,
  not a timeout.

## 1. Load conventions

Read these two files in parallel:

1. `<skill-root>/ilk-loop/SKILL.md` — front-matter conventions,
   state machine, commit-message rules. Pay particular attention to
   "Standard workflows" → workflow #5 ("Generate plans from a task
   description").
2. `<skill-root>/ilk-loop/references/decomposition-principles.md` — how
   to slice a goal so the loop can actually drive it. This is the
   rubric for step 5 (grouping proposal) and step 6 (writing the
   files). Do not skip it.

## 2. Verify project context and resolve the drafts dir

The project is the **ilk2 project registered for this repo root**. Find
it in ilk2's registry — do not invent a project id, and do not derive
one from a path:

```bash
ilk2 status --json
```

Each batch it lists carries a `project` field, and `ilk2 doctor` prints
one line per registered project. The project you want is the one whose
repo root is this repository. If no registered project covers this repo
root, STOP and tell the user to run:

```bash
ilk2 project add
```

once per project. This command never registers a project itself.

Fields you need:

- `project` — the id you will pass to `ilk2 submit --project` in step 8.
- `drafts dir` — `~/.ilk2/plans/<project>/<batch-id>/`, where
  `<batch-id>` is the batch's own id: `YYYY-MM-DD` plus a lower-case
  letter for the day's batch (`2026-10-18c`). The MASTER file name
  uses that same id — `MASTER-<batch-id>-execution-plan.md` — so the
  folder and the master it holds are named by one thing. **This is
  where you will write all new plan files** in step 6. Never write
  them in the repo, and never in V1's external plans tree.

### Meta-project mode

If the repo root's parent (or the root itself) is a polyrepo umbrella
marked by a `.ilk-meta.json`, the user is working across sibling git
repos that ship together. The implications for planning:

- **Every sub-plan must touch exactly one member repo.** This is a
  hard convention — cross-repo sub-plans are not allowed. If a change
  truly needs simultaneous edits in two repos, split it into two
  sub-plans wired together via `depends_on:` in their frontmatter.
- **The `Repo` column is mandatory** in the step-5 grouping table.
- **Each sub-plan's frontmatter must include `repo: <member-name>`** in
  step 6; the value must exactly match a `name` in the marker's
  `repos` list (the legal member tags).
- The MASTER's "Repos in scope" section is required — a one-line
  rationale per repo touched in the batch (see master-template.md).
- Any path you reference inside a sub-plan's commands is relative to
  that member repo, not the umbrella root.

If you're unsure which member a given task belongs to, ask the user in
step 5 — guessing leaks scope into the wrong repo and produces
misleading results.

Bootstrap the drafts dir if it does not exist:

- `mkdir -p` the drafts dir resolved above
- Tell the user: "Created `~/.ilk2/plans/<project>/<batch-id>/`."

If a `MASTER-*.md` already exists in this project's drafts dir, check
whether you should add to the latest master or write a new one for a new
batch — ask the user if unclear. `ilk2 status --json` is the other half
of that picture: it shows what is already running or queued for the
project.

## 3. Read the task description

Whatever the user typed after `/ilk2-plan` in the chat is the task
description. If it begins with `--yes` / `-y` and/or `--draft`
(whitespace-separated, any order), consume the flags, set the matching
local toggles — "skip approval" for `--yes`/`-y`, "draft only" for
`--draft` — and use the rest as the task description. `--draft` also
sets the skip-approval toggle: a draft is written without the step-5
pause and is never submitted. If empty after stripping the flags:
- Stop and ask: "What's the task / batch you'd like me to plan?"

If the task description is short and ambiguous, ask 1-3 clarifying
questions before proposing groupings. Don't guess.

If the task is thin AND the missing detail is **domain/industry knowledge
nobody can supply off the top of their head** (not facts in the user's head —
e.g. "build a tower-defense game" without the systems such a game needs),
clarifying questions won't help. Recommend the user run `/ilk-spec` first to
research conventions and elaborate a tier-tagged spec, then return here to plan
from it.

### Spec-input validation (when task comes from `/ilk-spec`)

If the task description contains spec pillar blocks (bold headings like
`**Pillar: X**`), run `"<skill-root>/ilk2-plan/scripts/plan_check.py" --spec`
against the spec text before proceeding to grouping. A pillar is NOT "done"
when only its model layer is gated — each pillar must carry:

1. A `verification_tier` tag (`loop-verified` / `compile-only` / `device-manual`).
2. At least one outcome-level AC (player/user-facing verb, not just
   "compiles" / "unit test passes").

If the linter finds issues, surface them and ask the user to fix the spec
before planning. This prevents under-gated pillars from reaching the loop.

## 3b. Record the batch's scope from its source (before drafting)

The trial (docs/07 §7.6) measures each batch against its *requested*
scope, so record that scope now — before you decide any sub-plans or
steps — and keep it as recorded. Three fields, written into the MASTER
frontmatter in step 6 and passed again at delivery (step 8b):

- `source:` — where the requested scope came from: the issue numbers,
  the spec path, the inbox entry, or `operator conversation, <date>`.
- `source_tickets:` — how many tickets the source names.
- `source_ac_groups:` — how many acceptance-criteria groups the source
  asks for. A group is one independently checkable outcome the source
  names. Count from the source text, not from the plan.

The counts are a recorded judgment, made once, before planning. Never
revise them to match the plan: if the batch you go on to write covers
more or fewer outcomes than the source asked for, that gap is a finding
for the step-9 report, not a reason to rewrite the counts.

Delivery carries them as `--meta`, kept with the batch and returned by
`ilk2 status`:

```bash
ilk2 submit --project <P> --meta '{"scope": {"source": "...", "tickets": N, "ac_groups": M}}' <master>
```

## 4. Read existing plans (collision avoidance)

If the drafts dir has any unfinished sub-plans, list them briefly so the
new batch doesn't overlap. Treat in-flight batches the same way —
`ilk2 status --json` shows what is already running or queued for this
project. If overlap is detected, surface it to the user and ask whether
to merge / supersede / proceed.

## 4b. The worker's tool surface — what the plan may assume

> **Plan against the worker's surface, not your interactive session's.**
> The worker is an ilk2 worker session: a fresh agent in a worktree,
> given only ilk2's brief for its step. Its tool surface comes from
> ilk2's worker template at `~/.ilk2/worker-template/` — the model,
> settings and MCP allow-list that template pins.

Name what the plan may assume **generically**: the agent's built-ins
(Bash / Edit / Read / Grep / Glob / Task / Write and the rest) plus the
MCP servers the worker template allows. Do **not** read the template's
credentials or any OAuth material, and never put a credential in a plan
file. A plan that names a tool must name its behaviour, not a token.

- If the batch needs a browser (`chrome-devtools`), an external design
  source, or another MCP, say so in the plan and gate on the
  behaviour the tool provides.
- **If the batch needs a tool the worker template does not provide**:
  **STOP before the step-5 approval.** Do not write a sub-plan that
  hard-gates on a tool the worker cannot reach. Tell the user to add it
  to the worker template first, then continue.
  - For a capability with a documented fallback (e.g. figma → build to
    an existing page pattern), prefer encoding the **degrade path** in
    step logic over a hard `env_prereq` (see decomposition-principles
    §"degrade-to-default" and the step-7 env_prereq-vs-fallback lint) —
    do NOT make it a fast-fail gate.

Mention the tool surface the plan was validated against in the step-5
proposal, so the user knows what the ACs will be checkable with.

## 4c. Fixture discovery (scan before drafting)

**This step is the single highest-leverage planner improvement** —
it prevents "false blocked" sub-plans where the loop reports "I need a
test account / seed data / fixture" while the asset is actually
sitting in the repo, just unindexed.

Before proposing groupings, sweep the project for existing fixtures
the planned sub-plans will likely need. Run these globs (`Glob` tool,
relative to the project root):

| Glob pattern | What we're looking for |
|---|---|
| `**/seed*.py`, `**/seeds/**`, `**/seed*.sh`, `**/seed*.ts` | Seed / factory commands (idempotent data setup) |
| `**/fixtures/**`, `**/factories/**` | Test fixture modules (Playwright helpers, factory_boy, pytest fixtures) |
| `**/{staging,test,dev,e2e}-accounts*`, `**/test-creds*` | Account credentials documents |
| `docs/loop/PRIMER.md`, `docs/loop/fixtures-registry.{yml,yaml,json}` | Project-side loop primer (see ilk-loop SKILL.md → "Project-side fixtures registry") |
| `AGENTS.md`, `CLAUDE.md` | Top-level agent docs — `grep` for "credentials"/"凭据"/"test account"/"primer" section headings |
| `**/conftest.py` (Python) | pytest fixture roots |

For each hit, briefly characterise it: 1-line "what it provides".
Examples:

- `api/apps/system/management/commands/seed_testdata.py` —
  idempotent seed for portal customers + admin staff (6 roles) + orders.
- `docs/portal/testing/staging-accounts.md` — portal customer +
  staff credentials, dev + staging.
- `docs/e2e-project/fixtures/api-client.ts::ensureCartHasItems` —
  helper that puts a known item in the active cart.

If `docs/loop/fixtures-registry.{yml,yaml,json}` exists, read it
fully — it is the authoritative machine-parseable index, and
sub-plan `data_prereqs` entries reference its keys directly.

**Surface this in the step-5 proposal** as a "Fixtures available"
table BELOW the sub-plan grouping table:

```
| Fixture | Location | Used by |
|---|---|---|
| portal customer test account | docs/portal/testing/staging-accounts.md | sub-plan-1, sub-plan-3 |
| seed_testdata cmd | api/apps/system/management/commands/seed_testdata.py | every authed sub-plan, step 0 |
| ensureCartHasItems helper | docs/e2e-project/fixtures/api-client.ts | sub-plan-2 |
```

The "Used by" column is what makes this useful — the planner explicitly
maps which fixture each sub-plan will rely on, so the user can challenge
("you forgot sub-plan-4 also needs auth"). Sub-plans that touch authed
routes / protected pages WITHOUT a corresponding fixture entry are a
red flag: either we missed scanning, or there's a real gap that needs
to be filed (e.g. "seed doesn't populate cart_items" → ticket for the
seed command).

**Producible-locally check (decomposition-principles.md §2).** A
fixture existing in the repo is NOT enough if the data it produces
depends on infra the worker can't reach. For each fixture you map to a
sub-plan, ask: *can the worker actually run this on the dev box?* If a
seed/backfill command's data source is a remote DB, a VPN-only host, or
a third-party import (e.g. crawler's `backfill_bigseller_inventory_image_url`,
whose source box was offline 12 days and stalled the loop), then either
plan a **local** seeding path or move the dependent AC to "Manual user
verification". Flag any such fixture in the proposal with a
`(remote-only — needs local path or manual AC)` note.

Skip this step ONLY if the user's task description is purely a refactor
/ docs / config change with no runtime data dependency. When in doubt,
run the globs — they're cheap.

## 4d. Environment reachability scan (decomposition-principles.md §10)

`data_prereqs` (step 4c) covers data *state*. This step covers whether
the runtime *environment* each sub-plan needs is *reachable* — a
distinct failure mode and, empirically, the one that has actually
stalled the loop. Both `stuck-no-progress` runs observed across
projects to date were reachability failures the worker discovered
mid-iteration and then gave up on with zero commits:

- crawler — remote backfill source (`linexcx-server`) offline 12 days.
- uccargo — `localhost:3000` dev server refused; Figma context empty.

For each candidate sub-plan, identify the runtime dependencies its
steps will touch and whether a cheap reachability probe exists:

| Dependency kind | Example `verify_cmd` |
|---|---|
| Local dev/preview server | `curl -sf -o /dev/null http://localhost:3000` |
| Staging / preview URL | `curl -sf -o /dev/null https://staging.example.com/health` |
| Remote data source (VPN/tailnet) | `tailscale ping -c1 <host>` |
| External design source (Figma) | covered by a `get_design_context` probe at step 0 |
| Required MCP (`chrome-devtools`) | `claude mcp list \| grep -q chrome-devtools` |

These become each sub-plan's `env_prereqs` entries in step 6 — a
step-0 fast-fail so the loop reports "blocked: dependency unreachable"
in milliseconds rather than burning the iteration. When a reachability
check applies to EVERY authed sub-plan, prefer wiring it once into
`docs/loop/preflight.sh` as a cross-cutting invariant (see ilk-loop
SKILL.md → "Project-side preflight") instead of repeating it per
sub-plan; `env_prereqs` is for sub-plan-specific reachability.

Surface the env_prereqs you intend to assign in the step-5 proposal,
in an "Environment prereqs" table alongside the fixtures table.

## 4e. VCS topology scan (decomposition-principles.md §22)

`data_prereqs` (4c) covers data state; `env_prereqs` (4d) covers
runtime reachability. This step covers **where each candidate
sub-plan's `scope_paths` currently live** — a distinct failure mode:
a batch whose files straddle two branches corrupts two PRs.

For each candidate sub-plan, resolve every `scope_paths` entry against
the declared `base_branch:` (or `main` if none is declared yet):

1. **Present on base** — clean.
2. **Absent from all history** (new-file case) — clean.
3. **Present on a ref other than base** — a HARD finding at plan time;
   the loop would commit to whichever branch is checked out, landing
   half the changes on the wrong base.

Two lints enforce this mechanically at step 7g:

- **`lint_scope_path_off_base_branch`** (per-path) — flags any
  `scope_paths` entry that exists on a ref other than the declared
  base.
- **`lint_one_batch_one_branch`** (master-level) — rejects a batch
  whose sub-plans' paths resolve to different branches, or whose
  master lacks `base_branch:` entirely.

Both run under `plan_check.py --master`. The plan-time gate catches the
defect at proposal review — the cheaper interception point.

Surface the branch resolution in the step-5 proposal as a "Branch
targets" table alongside the Fixtures and Environment-prereqs tables:

```
| Sub-plan | Paths resolve to | Declared base | Status |
|---|---|---|---|
| <slug-1> | `main` | `main` | clean |
| <slug-2> | `fix/foo` | `main` | HARD — path lives on another branch |
```

## 5. Propose grouping (USER APPROVAL REQUIRED)

Apply the rubric from `decomposition-principles.md` while drafting:

- Does each candidate sub-plan have a tight, mechanically-checkable
  contract (principle 1)? If not, sharpen the AC or merge with a
  neighbour.
- Are code prereqs and data prereqs distinguished (principle 2)?
- Is anything actually a diagnostic sub-plan in disguise (principle 6)?
  If so, re-scope to constructive or move to master-plan notes.
- Does each sub-plan pass the fresh-session test (principle 5)?

New planning rules (docs/07 §7.4.2) — the planner fails its own QC on
each of these, enforced in 7h below through `ilk2 submit --check`:

1. **Step size.** A step is about 25-30 worker-minutes. Both measures
   are per plan, per step: a sub-plan's steps share its scope and its
   preamble, so the counts are the plan's own divided by its steps —
   - **at most 6 files** of `scope_paths` divided by its steps;
   - **at most 12,000 characters** of plan text divided by its steps
     (its shared preamble counted once, plus each step's own section).

   These are the measures ilk2's `submit --check` reports as the
   plan-shape warnings `files-per-step` and `chars-per-step` (docs/07
   §7.4.1). Split a large sub-plan by acceptance-criteria group,
   red-first (failing tests first) inside each step.

   Basis: batch 17d (gh-resolve, Oct 17) ran 5.23 h with steps of about
   2,958 inserted lines; the plans above these bounds were slow two
   times in three.
2. **Dependencies are justified.** A sub-plan lists another in
   `depends_on` only when they **share files** (`scope_paths` overlap)
   or one needs the other's behaviour — and the MASTER's dependency
   section gives the reason for each edge, naming which of the two
   applies. Plans with disjoint scope have no edge, so ilk2 runs them in
   lanes. ilk2 warns `serial-chain` when dependencies order every plan
   although some ordered pairs share no files: a chain copied from an
   earlier batch, not justified here.
3. **No re-reading what the brief preloads** (see step 6's "Before you
   start").

Show the user a markdown table proposal. **In meta mode add a `Repo`
column** between Slug and Items so the user can see at a glance which
member each sub-plan targets:

Single-repo mode:
```
| # | Sub-plan slug | Items | Tier | Priority | Why grouped | Steps (est.) |
|---|---|---|---|---|---|---|---|
| 1 | <slug-1> | <list of items> | <tier> | P? | <one-line rationale> | <N> |
| ... |
```

Meta mode (`Repo` column required; values must come from the marker's
member names):
```
| # | Sub-plan slug | Repo | Items | Tier | Priority | Why grouped | Steps (est.) |
|---|---|---|---|---|---|---|---|---|
| 1 | <slug-1> | <member-name> | <list of items> | <tier> | P? | <one-line rationale> | <N> |
| ... |
```

**A master ends with a batch-verification sub-plan when the project
verifies** — the last row in the table when one is owed. It declares
`batch_verification: true` and holds **exactly one step**, whose gate is
the project's full suite. ilk2 recognises it as the batch's verification
plan and judges it against the batch base: the driver runs the gates (the
full suite) at the batch head and puts the failures, compared with the
base, in the worker's evidence. The worker fixes what that comparison
names, or changes nothing if it names nothing. No other sub-plan runs a
broad gate; they stay change-scoped.

**Write one where the project verifies, and not otherwise.** The project
must have a `verify_command` (ilk2 replaces V1's verification gates with
it; set it with `ilk2 project set`), **and** the project's own
instructions (its `AGENTS.md` / `CLAUDE.md`) must not say to omit it.
Read the current value with `ilk2 project show <project>` — its
`config.verify_command` — not from the store. Write one anyway
when the task asks for one. Otherwise write **no** verification sub-plan,
and say in the MASTER which reason applied — no `verify_command`, or the
project's instructions omit it (name both when both do). A master that
owes one and has no such row is a HARD lint finding. When one is owed, add
it as the last row in your grouping table:

```
| N+1 | <slug>-verify | batch verification — full suite | loop-verified | P0 | the batch's verdict against the base; written only when owed | 1 |
```

Plus:
- Suggested execution order (with a brief 1-2 sentence rationale per
  position).
- Cross-workstream dependencies (if any).
- **Branch targets table** (from step 4e) — shows where each
  sub-plan's `scope_paths` resolve to, beside the Fixtures and
  Environment-prereqs tables.
- **Batch-by-tier recommendation** — group `loop-verified` sub-plans into
  an autonomous batch (loop can drive without human) and
  `compile-only`/`device-manual` into a supervised or human-paired batch.
  Note which batch runs first and why.
- **Device-manual: don't stack, verify incrementally (P9).** After a batch
  containing `device-manual` sub-plans ships, **do the human+device pass
  before planning the next batch that builds on it.** Stacking unverified
  device work multiplies the debugging surface — two batches' runtime bugs
  can compound and become exponentially harder to root-cause (see
  decomposition §14.2). Flag any batch that contains more than one
  `device-manual` sub-plan and recommend the incremental-verify workflow.
- **Device-manual: budget as a debugging session (P10).** For
  `device-manual` work, the human cost is **root-causing, not coding**, and
  each iteration is minutes (build+flash). A 30-line fix that takes 6 device
  cycles to root-cause is not "small" in the relevant sense. Size batches
  accordingly — treat each `device-manual` sub-plan as a human debugging
  session, not a quick diff review (see decomposition §14.3).

If `--yes` or `--draft` was passed, print `Approval gate skipped (--yes).`
or `Approval gate skipped (--draft).` and proceed to step 6 without
waiting. Otherwise:

ASK the user explicitly: "OK to proceed with this grouping, or want
to adjust?"

Iterate until they approve. Do NOT write any files yet.

## 6. Write the plan files

Once approved, write all files in one batch under the drafts dir resolved
in step 2 (i.e. `~/.ilk2/plans/<project>/<batch-id>/`):

- `<drafts dir>/MASTER-<batch-id>-execution-plan.md`:
  - Front-matter per SKILL.md spec, keeping only what the plan's readers
    need: `title`, `slug`, `created`, `priority`, `base_branch`, the
    optional `branch` child-branch policy, `goal`, `out_of_scope`,
    `cross_cutting_invariants`, and the step-3b scope record
    (`source:`, `source_tickets:`, `source_ac_groups:`).
  - **Write no runner-owned frontmatter.** No step cursor, no recorded
    base sha, `pause_after_ship`, `supervised_only` or `ilk_profile` —
    none of them. Submission is the hand-off; after it nothing edits
    these files, and ilk2 keeps its own state. There is also no
    `status:` flip to perform: a draft on disk is simply not submitted
    yet, and step 8 is the delivery.
  - Workstream map (ascii box diagram is fine)
  - Sub-plan registry markdown table (links to each sub-plan file;
    ilk2 imports the batch from those links)
  - Execution rationale section
  - Cross-workstream dependency notes — for every `depends_on` edge,
    say which of the two justifications applies: shared `scope_paths`,
    or this plan needs the other's behaviour (planning rule 2).
  - "Out of scope" section (anything explicitly excluded)
  - "Rollout strategy" section — state which sub-plans are autonomous
    (loop-verified) vs supervised/human-paired (compile-only, device-manual),
    and the recommended run order per tier.
  - "Progress log" table (1 row: the creation entry)

- **When one is owed (step 5), the last sub-plan is ilk2's verification
  sub-plan**, and it has **one step**. Declare `batch_verification: true`
  and `plan: <slug>-verify` so ilk2 marks it as the batch's verification
  plan. Its single step's `local_checks` is the project's full suite —
  the project's `verify_command`, the one invocation ilk2 runs at the
  batch head. Read that command from `ilk2 project show <project>`
  (its `config.verify_command`), not from the store; do not hand-type a
  narrower selection, and do not write
  your own baseline or attribution runner: ilk2 judges the run against
  the batch base itself.

  There is no `ship_audit` resolver, no V1 baseline file, and no
  verification record for the worker to read or write. Any gate that
  calls V1's verification tooling is dropped when the batch is
  imported and replaced by the project's full suite; a step left with
  no gate that way is refused until the project has a `verify_command`
  (set it with `ilk2 project set`). A master that owes this sub-plan and
  whose project shows no `config.verify_command` is a HARD lint finding.

  When none is owed, write no verification sub-plan: the registry ends at
  the last work sub-plan, and the MASTER says which reason applied — no
  `verify_command`, or the project's instructions omit it.

  The step's instructions say two things. First, the run context: the
  driver has already run the gates at the batch head and compared the
  failures with the batch base; fix what that comparison names, or
  change nothing if it names nothing. Second, write the list of
  expected contract changes — the behaviour the batch changes on
  purpose (renamed verbs, removed fields, changed defaults), one line
  each — so the worker can tell an intended change from a regression in
  the suite's failures. The worker does get the MASTER as a readable
  document, but the verification step's text is what it starts from, so
  the list lives here. A verification step may produce no commit, and
  that is accepted — never instruct it to re-run the suite itself.

- One file per sub-plan: `<drafts dir>/<batch-id>-<slug>.md`, derived from
  `<skill-root>/ilk-loop/templates/subplan-template.md`. Fill in
  REAL content, not placeholders:
  - Front-matter with accurate `tickets:` and `estimated_steps:` values
  - **`verification_tier`** — one of `loop-verified`, `compile-only`,
    `device-manual` (see decomposition-principles §12 for definitions and
    when each tier is trustworthy). Absent ⇒ treated as `loop-verified`
    for back-compat, but every new sub-plan should declare it explicitly.
  - **A docs-only sub-plan must not claim `loop-verified`.** A sub-plan
    that changes only documentation and declares
    `verification_tier: loop-verified` with `must_add_tests: false` and
    no `unit_test_targets` is a HARD finding from `plan_check.py`: the
    tier claims a runtime gate while forbidding its evidence. Pick one
    of the three honest exits instead —
    1. declare `compile-only` for the docs-only sub-plan (it makes no
       runtime claim),
    2. name in `unit_test_targets` the existing tests that guard those
       docs (doc-consistency tests), or
    3. fold the docs into the code sub-plan they describe.

    Do not weaken the checker.
  - **In meta mode:** `repo: <member-name>` (REQUIRED; must match a
    `name` in the marker's `repos` list). In single mode the field is
    absent — do not invent values.
  - **`data_prereqs`** — for every sub-plan that touches authed routes
    / protected data / seeded state, list the fixtures from the step-4c
    discovery that this sub-plan will use. Use the structured schema
    from `subplan-template.md` (one of `registry_key` / `verify_cmd` /
    `description` per entry):

    ```yaml
    data_prereqs:
      # preferred: reference fixtures-registry.* key
      - registry_key: test_accounts.portal_customer
      # next best: standalone verify command
      - description: "cart has ≥1 item populated"
        verify_cmd: "curl -sf $API/api/v1/cart | jq -e '.data.items | length > 0'"
      # fallback only when nothing machine-checkable exists
      - description: "Figma v2 design tokens referenced (manual eyeball)"
    ```

    Each `registry_key` MUST exist in the project's
    `docs/loop/fixtures-registry.{yml,yaml,json}` when one is present
    (verified in step 7d below). Never leave `data_prereqs` as
    free-text "needs a test account" prose — that is the anti-pattern
    step 4c exists to kill.
  - **`env_prereqs`** — for every sub-plan whose steps touch a running
    service, remote data source, external design source, or required
    MCP, list the reachability probes from the step-4d scan. Each entry
    is `{description, verify_cmd}` and must fast-fail (exit non-zero in
    milliseconds when the dependency is down):

    ```yaml
    env_prereqs:
      - description: "portal dev server reachable"
        verify_cmd: "curl -sf -o /dev/null http://localhost:3000"
      - description: "chrome-devtools MCP connected"
        verify_cmd: "claude mcp list | grep -q chrome-devtools"
    ```

    Leave empty only when the sub-plan touches no running service /
    remote source / external MCP. When the same reachability check
    applies to every authed sub-plan, prefer a `docs/loop/preflight.sh`
    invariant over repeating it here (see ilk-loop SKILL.md →
    "Project-side preflight").
  - **`depends_on`** — only with the master's justification for each
    edge (shared `scope_paths`, or needs the other's behaviour).
  - Tickets-in-scope table with title / type / priority / module per item
  - Concrete objectives (1-line each)
  - Concrete acceptance criteria (observable, testable). **Each AC the
    loop is expected to verify must be checkable with the tool surface
    named in step 4b** — the agent's built-ins or an MCP the worker
    template allows.
    - If the worker has a browser MCP: browser ACs stay as loop steps
      using `take_snapshot` / `click` / `type_text` /
      `list_network_requests` / `evaluate_script` etc. (Preferred —
      see SKILL.md → "Loop-shippable verification" → Option A.)
    - If it does not and the AC needs a browser: move that AC to a
      "Manual user verification" H2 section at the bottom of the
      sub-plan (Option B). The loop ships after the CLI ACs pass.
    - Other CLI-friendly verifications: pytest, an authored verify
      script (`scripts/verify_<topic>.py` using `requests` +
      `AccessToken.for_user`), `rg` spot-checks, `curl`.
  - Out-of-scope guardrails
  - **A "Before you start" section that names only what ilk2's brief
    does NOT already preload.** ilk2 preloads the step, its sub-plan,
    and windows of the files and tests the step names (plus the batch's
    earlier accepted changes). So never tell the worker to "read the
    MASTER in full" or to "re-read every symbol" — that is paid for
    twice. Name only genuinely external context: project docs the step
    does not name, environment facts, or specs outside the preloaded
    windows.
  - Sequenced steps — `### Step N — <title>` sections under `## Steps`,
    one bullet list per step, ending with a commit line in the
    `<type>(<scope>): <summary> [plan:<slug>#step-N]` format. The step
    title is what the driver uses as the commit subject, so write it in
    that same `<type>(<scope>): <summary>` form. Each step must only use
    tools the step-4b surface confirms are available to the worker. The
    worker never runs `git commit` or `git push` — the driver makes the
    commit after the step's gates pass, and adds the trailer. No step
    tells the worker to ship or write a `#ship` commit; the driver
    ships after the last step's gate passes, and a worker-session ship
    is refused.
  - Reference reading section (any docs the executor should pre-load)

  Write every section complete. ilk2 never edits a plan file after
  submission, so no section is written blank for someone else to
  complete later.

## 7. Final QC

Run these passes against every newly-written sub-plan BEFORE
delivering. The enforced instrument is `ilk2 submit --check` (7h): its
refusals **and** its plan-shape warnings are failures of this QC, and
the plan is fixed before it is submitted. 7a / 7d-env / 7e findings are
warnings (surface to the user); 7b mutates files; **7c (meta projects
only) and 7d-errors** are hard gates — never advance to step 8 with an
unresolved hard finding. 7g is an optional authoring lint (see 7h).

### 7a. `local_checks` anti-pattern lint

Walk every sub-plan's frontmatter `local_checks` list **and** every
per-step `local_checks` yaml block. Warn on each occurrence:

- `| head` / `| tail` / `| awk 'NR==1'` after a check command —
  pipeline exit-status is lost; rewrite as `grep -q PATTERN file`
  (single command, real exit code) or split into separate entries.
  **Automated:** `plan_check.py` `lint_exit_status_discarded` flags this
  at step 7g
- Broken process-wait idiom: `while ... | grep -q ... | grep -v grep`
  — `grep -q` closes the pipe on first match, so the loop tests the
  second grep's status, not the first's. Use `pgrep` or `kill -0 $pid`.
  **Automated:** `plan_check.py` `lint_broken_process_wait` flags this
  at step 7g
- **Re-launching a backgrounded command to read its output** — when a
  long command is auto-backgrounded by the harness (exceeds
  `BASH_DEFAULT_TIMEOUT_MS`), its `.output` file accumulates results.
  Do NOT re-run the command to "get the result." Instead, poll the
  file for the `[exited with code` marker with
  `wait_for_background_output.sh`. See
  `decomposition-principles.md` §8 for the protocol and the
  `20260818-154347` incident that motivated it (~24 of 43 min burned).
- `grep` without `-q` and without an `-E '<expected-pattern>'` regex
  — tests for existence of a string, not for the contract value;
  tighten the pattern, or use `jq -e` for JSON
- `<binary> --help` for a CLI verb that wraps a backend function —
  tests the binary exists, not that it works. See
  `decomposition-principles.md` §1, wrapper-vs-bespoke distinction
- Compile-only smokes (`tsc`, `mypy`, `cargo build`, `npm run build`)
  as the **only** check on a sub-plan that adds an HTTP route, CLI
  verb, or new exported function — no runtime smoke; worker can ship
  a route that 500s and report ready
- Multi-step bash pipelines without `set -o pipefail` semantics —
  mid-pipeline failures slip through; split into separate check
  entries (each its own exit code) or wrap with `bash -o pipefail -c`
- **Diagnostic / no-commit step 0** (decomposition-principles.md §6) —
  a step 0 whose verb is purely investigative ("Reproduce",
  "Investigate", "Root-cause", "Figure out", "复现/排查") AND that
  carries no commit line is a stall waiting to happen (uccargo,
  2026-05-26: "Reproduce + Figma reference" step 0 ended with zero
  commits, `stuck-no-progress`). Either fold the reproduction into
  step 1 so the first step ends in a constructive commit, or give
  step 0 a concrete artifact + `local_checks` it must produce. This
  check reads each sub-plan's step structure, not just `local_checks`.
- **Red-first step 0 whose per-step gate demands green** (`plan_check`:
  `lint_redfirst_step0_per_step_gate_demands_green`) — a red-first step 0
  whose per-step gate runs its own test file with exit-0 semantics is
  required to fail. Either mark the pins `@pytest.mark.xfail(strict=True)`
  (preferred) or assert the red count with a grep gate.
- **per-file-only gate on a shared module** — a `local_check` that runs
  only the new file's tests while the change touches a shared/imported
  module hides integration + test-state-leak bugs (decomposition-principles
  §8, field-log bugs #1/#2).  HARD when the last step's gate misses a
  required test (the last step must widen beyond the new file to the
  module's tests plus its resolved callers' tests).  A warning only for a
  sub-plan with no per-step last gate (frontmatter-only or no `### Step`).
  Advisory (`INFO:`) under `--verified` — see 7g.
- **Whole-project-only compile gate** (decomposition-principles §16) —
  a sub-plan whose ONLY `local_check` is a whole-project compile
  command (`tsc`, `mypy`, `cargo build`, `npm run build`,
  `bun run typecheck`) with no change-scoped runtime smoke. This is a
  warning:
  the planner should add a targeted smoke or document why one is
  impossible. If a whole-project gate is unavoidable, the planner must
  confirm it's green on the BASE commit (baseline) to avoid
  false-blocking on pre-existing errors.

Output format per finding:

```
WARN: <slug> step <N>: <anti-pattern>: <offending command>
```

Print `OK: local_checks lint clean` if no findings. Surface counts
in the final report.

### 7b. Cross-cutting invariant weaving

If the MASTER plan's frontmatter `cross_cutting_invariants:` list is
non-empty, for every invariant × sub-plan pair:

1. Evaluate the invariant's `applies_when` predicate against the
   sub-plan body text (substring / regex per the predicate's syntax)
2. If matches AND the sub-plan's `local_checks` does NOT already
   carry the invariant's `assert` block:
   - Append the `assert` block to that sub-plan's `local_checks`
   - Mark the invariant as "woven" for that sub-plan in the report

This pass mutates sub-plan files; the mutations are picked up by the
delivery in step 8.

If `cross_cutting_invariants:` is empty or missing, skip 7b.

When in doubt about whether the predicate matches, **default to
including the assertion** — false positives are cheaper than missed
invariant violations.

### 7c. `repo:` validation (META PROJECTS ONLY)

Skip this pass when the project is a single repo.

For each newly-written sub-plan, assert:

1. Its frontmatter contains a `repo:` field with a non-empty value.
2. The value exactly matches a `name` in the marker's `repos` list
   (step 2).

Output format per failing sub-plan:

```
ERROR: <slug>: repo=<value> not in the marker's members (known: <list>)
```

Treat this as a HARD failure, not a warning: do not advance to step 8
until every sub-plan passes. A mistagged sub-plan would route the work
to the wrong member repo.

### 7d. `data_prereqs` schema validation

For each sub-plan, walk its frontmatter `data_prereqs:` list. Apply:

1. **Empty allowed** only if the sub-plan touches NO authed routes /
   protected data / seeded state. If the body mentions login / authed
   path / customer data / orders / cart / staff role etc. and
   `data_prereqs` is empty, this is a finding:

   ```
   WARN: <slug>: body touches authed surface but data_prereqs is empty
   ```

2. **Entry shape** — each entry must have exactly one of:
   - `registry_key: <key>` (preferred when a fixtures-registry exists)
   - `verify_cmd: <command>` (with optional `description`)
   - `description: <text>` (free-text fallback, lowest preference)

   An entry with NONE of these (a bare string left over from the
   pre-schema convention) is a finding:

   ```
   WARN: <slug>: data_prereqs entry "<value>" is not in the structured
         schema — convert to registry_key / verify_cmd / description
   ```

3. **Registry key existence** — if the project has
   `docs/loop/fixtures-registry.{yml,yaml,json}`, every `registry_key:`
   value MUST resolve to a real key in that file (use dotted-path
   notation: `test_accounts.portal_customer` → top-level
   `test_accounts` map, key `portal_customer`). A miss is a hard
   finding:

   ```
   ERROR: <slug>: data_prereqs registry_key "<key>" not in fixtures-registry
   ```

4. **`registry_key` without registry** — if a sub-plan uses
   `registry_key:` entries but the project has no fixtures-registry
   file at all, suggest creating one OR converting those entries to
   `verify_cmd:` form. Surface as a planning recommendation, not a
   blocker.

Finding counts (warnings + errors) go in the final report. Errors
should be fixed before delivering; warnings are advisory.

### 7d-env. `env_prereqs` schema + reachability-gap lint

For each sub-plan, walk its frontmatter `env_prereqs:` list:

1. **Entry shape** — each entry must carry a `verify_cmd:` (a
   `description:` is recommended alongside it). An entry with no
   `verify_cmd` can't fast-fail and is a finding:

   ```
   WARN: <slug>: env_prereqs entry "<value>" has no verify_cmd —
         a reachability prereq the loop can't probe is just prose
   ```

2. **Reachability gap** — if the sub-plan body mentions a running
   service, remote source, or external MCP (`localhost:`, a staging
   URL, `tailscale`/VPN host, `chrome-devtools`, `get_design_context`/
   Figma) but `env_prereqs` is empty AND no `docs/loop/preflight.sh`
   invariant covers it, this is a finding — it's the exact shape of the
   two `stuck-no-progress` stalls:

   ```
   WARN: <slug>: body needs a reachable runtime dependency but
         env_prereqs is empty and no preflight invariant covers it
   ```

Both are warnings (advisory); surface counts in the final report.

### 7e. Cold-read self-check

Re-read every sub-plan body under this prompt-frame:

> "If a fresh AI session opened this file with NO prior conversation,
> what information would it need that isn't on this page?"

For each sub-plan, list gaps in this category:

- Missing absolute file paths
- Ambiguous decisions left open
- Undeclared external state
- Terminology not defined in the master plan
- Design-choice judgment calls not pre-resolved
- Artifact/tmp paths outside the project root — any path a
  step writes to (screenshots, dumped API responses, scratch files)
  must sit under the project root (in meta mode: under the sub-plan's
  `repo` member dir). The worker sandbox rejects out-of-root writes
  (crawler, 2026-05-29: a dump to `e-com-ops/tmp/` was denied because
  root was `e-com-ops/crawler/`, wasting a retry). Rewrite any such
  path to a project-relative scratch dir.
- **A "Before you start" that re-asks for what the brief preloads** —
  "read the MASTER in full", "re-read every symbol in X". Cut those;
  keep only what is genuinely not in the preloaded windows (rule 3).

Fix obvious gaps inline (add the path, resolve the ambiguity).
Surface non-obvious ones to the user as "review before launching".

This is a heuristic — the planner already knows the context, so it
can't truly simulate cold. The empirical version (a real fresh
session per sub-plan in preflight) is a future runtime feature; this
step is the skill-side approximation that catches obvious misses now.

### 7f. `verification_tier` validation

Walk each newly-written sub-plan's frontmatter. For the `verification_tier`
field:

1. **Presence** — if `verification_tier` is absent, emit a warning (absent
   is allowed for back-compat but every new sub-plan should declare it):

   ```
   WARN: <slug>: verification_tier absent (assuming loop-verified)
   ```

2. **Enum** — if present, assert the value is one of `loop-verified`,
   `compile-only`, `device-manual`. Anything else is a hard error:

   ```
   ERROR: <slug>: verification_tier "<value>" not in {loop-verified, compile-only, device-manual}
   ```

Finding counts go in the final report. Errors should be fixed before
advancing to step 8.

### 7g. Degrade-discipline lints (`plan_check.py`, optional)

An **optional authoring lint** over every newly-written sub-plan — the
heuristics that used to be prose (and were skipped, stalling uccargo
twice on 2026-06-13). Nothing here is a gate: the enforced QC is
`ilk2 submit --check` (7h). Run it when you want the extra eyes. Under
`--draft`, run it — that mode's report includes both this and 7h, so
neither is optional there:

```bash
python "<skill-root>/ilk2-plan/scripts/plan_check.py" \
  --master <MASTER .md> <each new sub-plan .md>
```

Pass `--master` — it enables the master-level checks (slug-collision and
the branch checks) which cannot run from sub-plan text alone.

For an ilk2 project, pass `--path-prelude` with the project's
`path_prelude` directories joined by `:` (from `ilk2 project show
<project>`), when known — the checker uses them instead of
`.ilk-launch.json` to resolve gate executables on the driver's PATH.

When the batch has a verification sub-plan (step 5), also pass
`--verified`:

```bash
python "<skill-root>/ilk2-plan/scripts/plan_check.py" \
  --verified --master <MASTER .md> <each new sub-plan .md>
```

With it, the importer-coverage findings (`lint_shared_module_gate`, D9)
are advisory: printed as `INFO:` and not counted toward the exit code,
because ilk2's verification step and attribution cover them. Without a
verification sub-plan, do **not** pass it — those findings stay hard,
and the step gates must run the importers' tests. The checker
auto-detects the same condition (`--master` plus a linted sub-plan
declaring `batch_verification: true`), but say so explicitly so the
invocation is readable.

It emits finding classes (warnings except where marked **hard finding**;
surface counts in the step-9 report):

- **env_prereq-vs-fallback contradiction** — a sub-plan that hard-gates on an
  MCP via `env_prereqs: claude mcp list | grep -q X` AND documents a
  fallback/degrade path for the *same* X. The env_prereq fast-fails to
  `blocked` before the fallback can run, so they contradict. X is optional ⇒
  encode the degrade path in step logic; do NOT make it a hard env_prereq.
- **block-when-default-exists** — a step instructs `set status: blocked` while
  the sub-plan documents a safe default/fallback. On a headless loop `blocked`
  = stall + human; prefer degrade-to-default (see
  decomposition-principles.md → "Degrade-to-default over block").
- **escaped-bug-regression-gate** — a sub-plan has `regression_for:` set
  (declares it fixes a human-found escaped bug) but carries zero
  `local_checks` (neither frontmatter nor per-step). An escaped-bug fix
  must have a reproducing local_check to prevent the same class of bug
  from escaping a gate twice. See decomposition-principles.md §18.
- **tier forbids its evidence** (**hard finding**) — a sub-plan declares
  `verification_tier: loop-verified` while `must_add_tests: false` and
  `unit_test_targets` is empty or absent. The tier claims a runtime gate
  while both fields forbid the evidence that claim requires. The common
  shape is a docs-only sub-plan; the three exits are the ones step 6
  names (declare `compile-only`, arm `unit_test_targets` with the
  doc-consistency tests, or fold the docs into the code sub-plan they
  describe). `batch_verification: true` is exempt — its evidence is the
  suite its step runs, not a test it adds. Measured:
  kira-cloudflare issue-5445-work, 2026-09-15. See
  decomposition-principles.md §12.
- **e2e/device-polling local_check lacks env_prereq** — a sub-plan declares an
  e2e, browser-automation, or service-poll `local_check` (e.g. `node e2e/*.mjs`,
  `playwright test`, devtools, poll phrasing) but has no `env_prereqs`
  reachability probe and no `docs/loop/preflight.sh` reference. The gate will
  burn its timeout into `local-checks-stuck` when the dependency is unreachable.
  Add an `env_prereqs` entry with a fast-fail `verify_cmd` (see
  decomposition-principles.md §10).
- **POSIX-only test assertion** — a `local_check` shell command (or referenced
  `.sh` test) asserts a POSIX file mode (`rw-------`, `stat -c %A`, `chmod`
  perm check) without a `uname`/`OSTYPE` platform guard. Cannot pass on
  Windows Git Bash. See decomposition-principles.md §8.
- **network-tool mock-only gate** — a sub-plan ships a new HTTP/network tool
  (body mentions `urllib`/`requests`/`_post`/`api.`) whose only gate mocks the
  network boundary (`patch(... _post)`, injected fake) with no
  integration/import-resolve/live smoke and no `env_prereqs`. The live path can
  ship broken (cf. draw.py `ModuleNotFoundError`). See
  decomposition-principles.md §8.
- **vertical-slice AC (orphaned model)** — a sub-plan adds a model/logic symbol
  (`def`/`class`/`export function`) in a non-UI module whose every `local_check`
  is a pure-unit test with no consumer entry-point keyword (UI hit-test, CLI
  verb, HTTP route, e2e sim). The model compiles and unit-tests pass but nothing
  proves a player/user can reach it (the 'orphaned model' shape). See
  decomposition-principles.md §8.
- **anti-hardcode integration gate** — a sub-plan introduces per-instance data
  (per-stage path, per-tenant config, per-level theme) and says an existing
  module should consume it, but no `local_check` asserts the consumer reads the
  new data vs a hardcoded constant. Data exists but consumer is still hardcoded
  (the 'data-present but runtime-broken' shape). See decomposition-principles.md §8.
- **UI-promise-wiring** — a sub-plan introduces a UI affordance/prompt that
  advertises a capability (key hint, button label, shortcut, indicator) but
  neither `local_checks` nor the body contains a wiring/trigger assertion (event
  handler, keybind, click, press_key, e2e). The user is prompted to act but
  nothing is bound (the 'promise-without-wiring' shape). See
  decomposition-principles.md §8.
- **balance-regression-flag** — a sub-plan changes a core mechanic or tunable
  formula (coefficient, multiplier, threshold, rate, weight, pricing/scoring)
  but contains no baseline before/after regression assertion (baseline, golden,
  snapshot compare, before-and-after). The change silently shifts behaviour
  without a before/after delta check (the 'balance-drift' shape). See
  decomposition-principles.md §8.
- **shared-module gate** — a sub-plan's `scope_paths` modifies a module that
  other production files import, and every gate in the sub-plan runs only a
  single test file. The callers' integration is never exercised.  HARD when
  the last step's gate misses a required test (the last step must widen
  beyond the new file to the module's tests plus its resolved callers'
  tests).  A warning only for a sub-plan with no per-step last gate
  (frontmatter-only or no `### Step`).  Advisory (`INFO:`) under
  `--verified` — see above. The finding names the importing files.
  Uses a caller-aware detector (grep for `from <mod> import`
  / `import <mod>`, excluding test files); if the oracle cannot run, it
  reports nothing rather than firing. See decomposition-principles.md §8.
- **gate executable off driver PATH** — a `local_checks` command's leading
  executable cannot resolve on the effective PATH (getconf PATH + the project's
  `path_prelude`). The finding names both the executable and the searched
  directory list. Shell builtins, keywords, and `VAR=value` prefixes are
  skipped. See decomposition-principles.md §23.
- **scope_path off base branch** (**hard finding**, needs `--master`) — a
  sub-plan's `scope_paths` entry exists on a ref other than the master's
  declared `base_branch:`. The loop commits to whichever branch is checked
  out, so a straddling batch corrupts two PRs. A file absent from all
  history (new-file case) passes; a file present only on an open PR's
  branch is a HARD finding. See decomposition-principles.md §22.
- **one batch one branch** (**hard finding**, needs `--master`) — a
  master's sub-plans' `scope_paths` resolve to different branches, or
  the master lacks `base_branch:` entirely. Every sub-plan in a batch
  must target files on the same branch. See decomposition-principles.md §22.

`plan_check.py` exits non-zero when it finds anything; treat findings as
authoring advice worth fixing before delivery (a contradiction here is
what actually stalled the loop). They do not block delivery — 7h does.

### 7h. Plan-shape QC (`ilk2 submit --check`)

Run ilk2's own check against the authored batch here, before step 8 —
the same command step 8a delivers with, used as this planner's QC:

```bash
ilk2 submit --check --project <project> <drafts dir>/MASTER-<batch-id>-execution-plan.md
```

**Treat its refusals AND its plan-shape warnings as failures of your own
QC.** ilk2 itself only reports the warnings and lets the batch through —
a threshold is a judgment call, and refusing on it would stall an
unattended consumer (docs/07 §7.4.1). This planner is the other half of
that bargain: it fails on the same rules and fixes the plan before
submitting. Split steps that break the step-size bounds (rule 1), and
drop `depends_on` edges the MASTER cannot justify (rule 2). Fix, re-run
the check, and only then go to step 8.

The plan-shape warnings, named as ilk2 reports them:

- `files-per-step` — a plan's distinct `scope_paths` entries divided by
  its steps is over 6.
- `chars-per-step` — a plan's text (its shared preamble once, plus each
  step's own section) divided by its steps is over 12,000 characters.
- `serial-chain` — `depends_on` orders every plan although some ordered
  pairs share no files.

`plan_check.py --master` (7g) is an **optional authoring lint**;
`ilk2 submit --check` is what this QC enforces.

## 8. Deliver the batch to ilk2

Plans live OUTSIDE the project repo, so there is **nothing to git-add
inside the project** and **no status flip to perform**. The files were
written to the drafts dir in step 6; delivering the master to ilk2 is
what makes the batch exist.

### 8a. Check (creates nothing)

```bash
ilk2 submit --check --project <project> <drafts dir>/MASTER-<batch-id>-execution-plan.md
```

`--check` validates exactly as a submit would and creates nothing. It
prints each refusal it finds and exits non-zero while any stands —
a broken plan, not a judgment call. **Fix every refusal it prints**, then
run the check again. Do not submit while one remains.

What it refuses is the structural class docs/07 §7.4.1 defines for an
authored batch: a linked sub-plan file that is missing or cannot be
parsed, a mismatch between the master's registry table and the sub-plans
it links, a step with no gate, a gate whose executable does not resolve,
a `local_checks` block that cannot be read, a step whose declared writes
touch the project's `deny_paths`, and a gate that cannot run at the base.
Take the printed `problems` list as authoritative over this summary.
Warnings (the plan-shape thresholds of 7h) never refuse a batch — ilk2
only reports them — but they are QC failures for this planner: if one
stands here, 7h did not finish. Go back and fix the plan rather than
submitting past it.

### 8b. Submit

If `--draft` was passed, STOP here. Report what 7g and 7h found and
where the files sit, and say that delivery is the command below with the
same `--meta`. No submit, and no batch id — the drafts stay on disk
until a later invocation delivers them.

Otherwise, submit with the scope record from step 3b:

```bash
ilk2 submit --project <project> --meta '{"scope": {"source": "...", "tickets": N, "ac_groups": M}}' <drafts dir>/MASTER-<batch-id>-execution-plan.md
```

`--meta` is kept with the batch and returned by `ilk2 status`. Fill the
three fields from the step-3b record exactly as written there — never
re-derive them from the plan.

This creates the batch and prints its batch id — keep it for step 9.
Optional flags, when the batch needs them:

- `--base` / `--branch` — the commit and the branch to build on.
- `--deny-path` — a path or glob no step may change, for this batch
  only (repeatable; adds to the project's `deny_paths`).
- `--consumer-ref` — a label to find this batch by later.
- `--json` — machine-readable output.

The project must already be registered (step 2); submit never registers
one. Leave the drafts where they are: they are the authored copy of what
was submitted.

## 9. Final report

End your turn with:

1. A brief summary: "Wrote 1 master plan + N sub-plans covering M items
   to `~/.ilk2/plans/<project>/<batch-id>/`, submitted as batch `<id>`."
   Under `--draft`, say instead that the draft is on disk unsubmitted and
   that delivery is `ilk2 submit` with the same `--meta`.
2. QC pass results: plan-shape warnings fixed (count), lint findings
   (count, incl. diagnostic step-0), invariants woven (count),
   env_prereqs findings (count), cold-read gaps surfaced (count),
   step-8 refusals fixed (count). Each non-zero count expanded with a
   short bullet list so the user can act.
3. The batch id and the output of:

   ```bash
   ilk2 status --batch <id>
   ```

   so the user sees the batch and its plans' pending state.
4. **Gate-status line.** If ANY sub-plan declares `local_checks`, report:
   *"Gates: N/N sub-plans declare `local_checks` — the driver runs them
   after every attempt."*
5. **Verification note.** If ANY newly-written sub-plan carries runtime
   `local_checks` (frontmatter or per-step), say so plainly: ilk2 runs
   each step's declared gates after every attempt — a declared gate has
   no skip path, and a step with red gates does not ship. When the batch
   owes a verification sub-plan (step 5) and wrote one, add that ilk2
   judges its full suite against the batch base. When it wrote none,
   restate the MASTER's reason (no `verify_command`, or the project's
   instructions omit it). Sub-plans that need a human or a device pass
   are named in the tier mix below.
6. **Tier-mix summary.** List the tier breakdown of the batch:

   ```
   Tier mix: N loop-verified, M compile-only, K device-manual
   ```

   If any sub-plan is NOT `loop-verified`, append:

   > **NEEDS HUMAN VERIFICATION**: compile-only and device-manual sub-plans
   > require a human + device pass after the loop marks them done.

7. Tell the user: "Ready to execute. ilk2's supervisor picks the batch
   up — watch it with `ilk2 status --batch <id>`."

## Boundary rules

- **Never skip step 5 (user approval)** unless the operator passed
  `--yes` or `--draft` — grouping decisions are subjective; humans
  always sign off.
- **Never write files in step 5** — only after approval (or after the
  operator's `--yes` / `--draft`).
- **Never revise the step-3b scope counts to match the plan** —
  `source_tickets` and `source_ac_groups` are a recorded judgment, made
  once from the source text. A gap between them and what the plan
  covers belongs in the step-9 report.
- **Never skip the step-7 QC passes** — they are quality gates between
  writing the files and delivering them to ilk2. Skipping sends
  under-specified or weak-contract sub-plans to worker sessions that
  will burn cycles before failing or — worse — pass a too-permissive
  `local_check` and ship broken work. In meta projects, 7c
  specifically is a HARD gate: an unresolved error there means the work
  would land in the wrong repo.
- **Never deliver while a refusal stands** — `--check` is the gate
  between authoring and submission, and its refusals are structural.
  Its plan-shape warnings are this planner's QC failures too (7h): fix
  the plan and re-check rather than submitting past them.
- **Never write inside the project repo** — drafts live under
  `~/.ilk2/plans/`. Any sub-plan mutations from step 7b (invariant
  weaving) are written to those same draft files before step 8.
- **Don't auto-update any external trackers** (Lark, GitHub, etc.) — that
  is the source-adapter's job (e.g., `/ilk-lark`).
