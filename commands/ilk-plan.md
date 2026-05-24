Plan a new batch of work for the ilk loop, from a free-text task
description supplied by the user (anything after `/ilk-plan` in the
chat input is the task description).

This is the universal planning entrypoint — source-agnostic. You will
produce a master plan + sub-plans in the project's **external plans
directory** (under `~/.ilk-data/`), ready to be executed by `/ilk`.

**Plans live OUTSIDE the project repo** so the project's git history
stays clean of skill artifacts. The exact location is derived from
the project's `.git` root and resolved by
`~/.cursor/skills/ilk-loop/scripts/ilk_paths.py`. Legacy projects
that still keep plans in-tree under `<root>/docs/plans/` are
supported by the resolver as a fallback; do not migrate an in-flight
project mid-run — use `migrate_plans_to_external.py` between batches.

Follow these steps in order. Do NOT skip the user-approval gate.

## 1. Load conventions

Read these two files in parallel:

1. `~/.cursor/skills/ilk-loop/SKILL.md` — front-matter conventions,
   state machine, commit-message rules. Pay particular attention to
   "Standard workflows" → workflow #5 ("Generate plans from a task
   description").
2. `~/.cursor/skills/ilk-loop/docs/decomposition-principles.md` — how
   to slice a goal so the loop can actually drive it. This is the
   rubric for step 5 (grouping proposal) and step 6 (writing the
   files). Do not skip it.

## 2. Verify project context and resolve the plans dir

Run the resolver to find out where this project's plans live:

```powershell
python "$HOME\.cursor\skills\ilk-loop\scripts\ilk_paths.py" --start .
```

It prints a JSON object. The fields you need:

- `project_root` — the actual root: a `.git` directory for single-repo
  projects, or the parent of `.ilk-meta.json` for meta projects. Must
  be non-null; if null, ask the user to run `/ilk-plan` from inside a
  recognized project
- `project_kind` — `"single"` or `"meta"`. See "Meta-project mode"
  below — when `meta`, every sub-plan must declare a target member
  repo
- `meta_members` — non-empty list only when `project_kind == "meta"`;
  each entry is `{name, path}`. The `name` values are the legal repo
  tags you can assign to sub-plans in step 6
- `project_key` — the externalisation key (read-only; do not invent
  your own)
- `external_plans_dir` — the canonical destination
  (`~/.ilk-data/projects/<key>/plans/`); **this is where you will
  write all new plan files** in step 6
- `resolved_plans_dir` + `resolved_source` — what `loop_status.py`
  currently reads. Possible sources:
  - `external` — already migrated; you'll add to / extend the
    existing layout
  - `in-tree` — legacy `<root>/docs/plans/` is being used (single
    mode only); new plans STILL go to `external_plans_dir`. Tell the
    user the project is on the legacy layout and recommend running
    `migrate_plans_to_external.py` between batches.
  - `walk-up` — even more legacy (non-git ancestor); same handling
    as `in-tree`
  - empty string — no plans yet; you'll bootstrap

### Meta-project mode

If `project_kind == "meta"` the user is working in a polyrepo umbrella
(a parent dir whose children are git repos). The implications for
planning:

- **Every sub-plan must touch exactly one member repo.** This is a
  hard convention — cross-repo sub-plans are not allowed. If a change
  truly needs simultaneous edits in two repos, split it into two
  sub-plans wired together via `depends_on:` in their frontmatter.
- **The `Repo` column is mandatory** in the step-5 grouping table.
- **Each sub-plan's frontmatter must include `repo: <member-name>`** in
  step 6; the value must exactly match one of the names in
  `meta_members`.
- **The MASTER's "Repos in scope" section is required** — a one-line
  rationale per repo touched in the batch (see master-template.md).
- The loop driver cd's into the named member repo for that sub-plan's
  commits, local_checks, CI waits, and ship-report generation. This is
  invisible from the planner's perspective except that any path you
  reference inside a sub-plan's commands is relative to that member
  repo, not the meta root.

If you're unsure which member a given task belongs to, ask the user in
step 5 — guessing leaks scope into the wrong repo and produces
misleading ship reports.

Bootstrap the destination if it does not exist:

- `mkdir` `external_plans_dir` (Python: the resolver also has
  `external_plans_dir(key)` if you want the path programmatically)
- Copy `~/.cursor/skills/ilk-loop/templates/README.md` into the new
  dir as `README.md`
- Tell the user: "Created `~/.ilk-data/projects/<key>/plans/`
  scaffolding."

If `MASTER-*.md` already exists in the resolved plans dir, check
whether you should add to the latest master (workflow #3 in SKILL.md)
or create a new master for a new batch — ask the user if unclear.

## 3. Read the task description

Whatever the user typed after `/ilk-plan` in the chat is the task
description. If empty:
- Stop and ask: "What's the task / batch you'd like me to plan?"

If the task description is short and ambiguous, ask 1-3 clarifying
questions before proposing groupings. Don't guess.

## 4. Read existing plans (collision avoidance)

If the resolved plans dir has any unfinished sub-plans, list them briefly
so the new batch doesn't overlap. If overlap is detected, surface it to
the user and ask whether to merge / supersede / proceed.

## 4b. Probe loop CLI capabilities

Run `claude mcp list` once and remember the result. The loop runs via
Claude Code CLI, so its tool surface = Claude Code built-ins
(Bash/Edit/Read/Grep/Glob/Task/Write/etc.) PLUS whatever MCPs that
command lists. This determines what counts as loop-shippable in step 6:

- If `chrome-devtools` is listed → browser verification IS
  loop-shippable; plan browser ACs as normal loop steps.
- If `chrome-devtools` is NOT listed but the batch needs browser
  verification → either (a) suggest the user run
  `claude mcp add chrome-devtools --scope user -- npx chrome-devtools-mcp@latest --browserUrl http://localhost:9222`
  before approving, or (b) plan those ACs into a "Manual user
  verification" section per SKILL.md → "Loop-shippable verification"
  → Option B.

Mention the probe result in the step 5 proposal so the user knows
which path the plan assumes.

## 4c. Fixture discovery (scan before drafting)

**This step is the single highest-leverage planner improvement** —
it prevents "false blocked" sub-plans where the loop reports "I need a
test account / seed data / fixture" while the asset is actually
sitting in the repo, just unindexed.

Before proposing groupings, sweep the project for existing fixtures
the planned sub-plans will likely need. Run these globs (`Glob` tool,
relative to the resolved project root from step 2):

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

Skip this step ONLY if the user's task description is purely a refactor
/ docs / config change with no runtime data dependency. When in doubt,
run the globs — they're cheap.

## 5. Propose grouping (USER APPROVAL REQUIRED)

Apply the rubric from `decomposition-principles.md` while drafting:

- Does each candidate sub-plan have a tight, mechanically-checkable
  contract (principle 1)? If not, sharpen the AC or merge with a
  neighbour.
- Are code prereqs and data prereqs distinguished (principle 2)?
- Is anything actually a diagnostic sub-plan in disguise (principle 6)?
  If so, re-scope to constructive or move to master-plan notes.
- Does each sub-plan pass the fresh-session test (principle 5)?

Show the user a markdown table proposal. **In meta mode add a `Repo`
column** between Slug and Items so the user can see at a glance which
member each sub-plan targets:

Single-repo mode:
```
| # | Sub-plan slug | Items | Priority | Why grouped | Steps (est.) |
|---|---|---|---|---|---|
| 1 | <slug-1> | <list of items> | P? | <one-line rationale> | <N> |
| ... |
```

Meta mode (`Repo` column required; values must come from `meta_members`):
```
| # | Sub-plan slug | Repo | Items | Priority | Why grouped | Steps (est.) |
|---|---|---|---|---|---|---|
| 1 | <slug-1> | <member-name> | <list of items> | P? | <one-line rationale> | <N> |
| ... |
```

Plus:
- Suggested execution order (with a brief 1-2 sentence rationale per
  position).
- Cross-workstream dependencies (if any).

Then ASK the user explicitly: "OK to proceed with this grouping, or want
to adjust?"

Iterate until they approve. Do NOT write any files yet.

## 6. Write the plan files

Once approved, write all files in one batch under the
`external_plans_dir` resolved in step 2 (i.e.
`~/.ilk-data/projects/<key>/plans/`):

- `<external_plans_dir>/MASTER-YYYY-MM-DD-execution-plan.md`:
  - Front-matter per SKILL.md spec
  - Workstream map (ascii box diagram is fine)
  - Sub-plan registry markdown table
  - Execution rationale section
  - Cross-workstream dependency notes
  - "Out of scope" section (anything explicitly excluded)
  - "Progress log" table (1 row: the creation entry)

- One file per sub-plan: `<external_plans_dir>/YYYY-MM-DD-<slug>.md`, derived from
  `~/.cursor/skills/ilk-loop/templates/subplan-template.md`. Fill in
  REAL content, not placeholders:
  - Front-matter with accurate `tickets:` and `estimated_steps:` values
  - **In meta mode:** `repo: <member-name>` (REQUIRED; must match a
    name from step-2 `meta_members`). In single mode the field is
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
  - Tickets-in-scope table with title / type / priority / module per item
  - Concrete objectives (1-line each)
  - Concrete acceptance criteria (observable, testable). **Each AC the
    loop is expected to verify must be checkable using a tool the loop
    has** — Claude Code built-ins or an MCP shown by the step-4b
    `claude mcp list` probe.
    - If `chrome-devtools` is registered: browser ACs stay as loop
      steps using `take_snapshot` / `click` / `type_text` /
      `list_network_requests` / `evaluate_script` etc. (Preferred —
      see SKILL.md → "Loop-shippable verification" → Option A.)
    - If it's NOT registered and the AC needs a browser: move that
      AC to a "Manual user verification" H2 section at the bottom of
      the sub-plan (Option B). The loop ships after the CLI ACs pass.
    - Other CLI-friendly verifications: pytest, an authored verify
      script (`scripts/verify_<topic>.py` using `requests` +
      `AccessToken.for_user`), `rg` spot-checks, `curl`.
  - Out-of-scope guardrails
  - Sequenced steps (one bullet list per step, ending with a commit line
    in the `<type>(<scope>): <summary> [plan:<slug>#step-N]` format).
    Each step must only use tools the step-4b probe confirms are
    available to the loop.
  - Empty "Findings" section (loop fills during execution)
  - Reference reading section (any docs the executor should pre-load)

## 7. Final QC (five passes)

Run these passes against every newly-written sub-plan BEFORE
committing. 7a / 7e findings are warnings (surface to the user);
7b mutates files; **7c (meta projects only) and 7d-errors are hard
gates** — never advance to step 8 with an unresolved hard finding.

### 7a. `local_checks` anti-pattern lint

Walk every sub-plan's frontmatter `local_checks` list **and** every
per-step `local_checks` yaml block. Warn on each occurrence:

- `| head` / `| tail` / `| awk 'NR==1'` after a check command —
  pipeline exit-status is lost; rewrite as `grep -q PATTERN file`
  (single command, real exit code) or split into separate entries
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
single batch commit in step 8.

If `cross_cutting_invariants:` is empty or missing, skip 7b.

When in doubt about whether the predicate matches, **default to
including the assertion** — false positives are cheaper than missed
invariant violations.

### 7c. `repo:` validation (META PROJECTS ONLY)

Skip this pass when `project_kind == "single"`.

For each newly-written sub-plan, assert:

1. Its frontmatter contains a `repo:` field with a non-empty value.
2. The value exactly matches one of the names in step-2 `meta_members`.

Output format per failing sub-plan:

```
ERROR: <slug>: repo=<value> not in meta_members (known: <list>)
```

Treat this as a HARD failure, not a warning: do not advance to step 8
until every sub-plan passes. A mistagged sub-plan would route commits
to the wrong member repo and corrupt the ship report.

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
should be fixed before launching the loop; warnings are advisory.

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

Fix obvious gaps inline (add the path, resolve the ambiguity).
Surface non-obvious ones to the user as "review before launching".

This is a heuristic — the planner already knows the context, so it
can't truly simulate cold. The empirical version (a real fresh
session per sub-plan in preflight) is a future runtime feature; this
step is the skill-side approximation that catches obvious misses now.

## 8. Persist (no project-repo commit)

Plans live OUTSIDE the project repo, so there is **nothing to git-add
inside the project**. The files have already been written to
`~/.ilk-data/projects/<key>/plans/` in step 6.

If you (or the user) want version history for the plans themselves,
that's a separate concern: `~/.ilk-data` can be its own git repo or
backed up however the operator prefers. **Do not initialise that
repo from this command** — leave it to the operator's own tooling.

If the project is still on the legacy in-tree layout
(`resolved_source` was `in-tree` or `walk-up` in step 2), tell the
user explicitly:

> The new plans were written to `~/.ilk-data/projects/<key>/plans/`,
> NOT to `<project>/docs/plans/`. Run
> `python ~/.cursor/skills/ilk-loop/scripts/migrate_plans_to_external.py
> --project . --apply` once the current loop is idle to migrate the
> legacy plans alongside, then commit the resulting deletions in the
> project repo.

## 9. Final report

End your turn with:

1. A brief summary: "Wrote 1 master plan + N sub-plans covering M items
   to `~/.ilk-data/projects/<key>/plans/`."
2. QC pass results: lint findings (count), invariants woven (count),
   cold-read gaps surfaced (count). Each non-zero count expanded with
   a short bullet list so the user can act.
3. The output of `python "$HOME\.cursor\skills\ilk-loop\scripts\loop_status.py"`
   so the user sees the new pending state.
4. Tell the user: "Ready to execute. Open a fresh chat and type `/ilk`."

## Boundary rules

- **Never skip step 5 (user approval)** — grouping decisions are
  subjective; humans always sign off.
- **Never write files in step 5** — only after approval.
- **Never skip the step-7 QC passes** — they are quality gates between
  writing the files and shipping them to the loop. Skipping sends
  under-specified or weak-contract sub-plans to worker sessions that
  will burn cycles before failing or — worse — pass a too-permissive
  `local_check` and ship broken work. In meta projects, 7c
  specifically is a HARD gate: an unresolved error there means the
  loop would route commits to the wrong repo.
- **No project-repo commit in step 8** — plans live outside the
  project tree. Any sub-plan mutations from step 7b (invariant
  weaving) are written directly to the external files in step 6/7.
- **Don't auto-update any external trackers** (Lark, GitHub, etc.) — that
  is the source-adapter's job (e.g., `/ilk-lark-tickets`).
