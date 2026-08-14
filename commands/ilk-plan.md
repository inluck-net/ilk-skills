Plan a new batch of work for the ilk loop, from a free-text task
description supplied by the user (anything after `/ilk-plan` in the
chat input is the task description).

This is the universal planning entrypoint — source-agnostic. You will
produce a master plan + sub-plans in the project's **external plans
directory** (under `~/.ilk-data/`), ready to be executed by `/ilk`.

**Plans live OUTSIDE the project repo** so the project's git history
stays clean of skill artifacts. The exact location is derived from
the project's `.git` root and resolved by
`<skill-root>/ilk-loop/scripts/ilk_paths.py`. Legacy projects
that still keep plans in-tree under `<root>/docs/plans/` are
supported by the resolver as a fallback; do not migrate an in-flight
project mid-run — use `migrate_plans_to_external.py` between batches.

Follow these steps in order. Do NOT skip the user-approval gate unless
the operator passed `--yes` (see Arguments below).

## Arguments

- `--yes` / `-y` — skip the step-5 approval wait for this invocation.
  The full grouping proposal is still printed; only the pause-and-ask
  is skipped. Everything after the flag is the task description.
  A flag, not a timer — a timeout cannot distinguish consent from
  absence, and makes the same plan approve differently depending on
  whether you stepped away.

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

## 2. Verify project context and resolve the plans dir

Run the resolver to find out where this project's plans live:

```powershell
python "<skill-root>/ilk-loop/scripts/ilk_paths.py" --start .
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
- Copy `<skill-root>/ilk-loop/templates/README.md` into the new
  dir as `README.md`
- Tell the user: "Created `~/.ilk-data/projects/<key>/plans/`
  scaffolding."

If `MASTER-*.md` already exists in the resolved plans dir, check
whether you should add to the latest master (workflow #3 in SKILL.md)
or create a new master for a new batch — ask the user if unclear.

## 3. Read the task description

Whatever the user typed after `/ilk-plan` in the chat is the task
description. If it begins with `--yes` or `-y` (whitespace-separated),
consume the flag, set a local "skip approval" toggle, and use the rest
as the task description. If empty after stripping the flag:
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
`**Pillar: X**`), run `plan_lint.py --spec` against the spec text before
proceeding to grouping. A pillar is NOT "done" when only its model layer is
gated — each pillar must carry:

1. A `verification_tier` tag (`loop-verified` / `compile-only` / `device-manual`).
2. At least one outcome-level AC (player/user-facing verb, not just
   "compiles" / "unit test passes").

If the linter finds issues, surface them and ask the user to fix the spec
before planning. This prevents under-gated pillars from reaching the loop.

## 4. Read existing plans (collision avoidance)

If the resolved plans dir has any unfinished sub-plans, list them briefly
so the new batch doesn't overlap. If overlap is detected, surface it to
the user and ask whether to merge / supersede / proceed.

## 4b. Probe loop CLI capabilities — the WORKER's MCP surface

> **Probe the WORKER, not your interactive session.** The loop runs via the
> `claude-worker` engine, which pins `CLAUDE_CONFIG_DIR` to a separate worker
> home (default `~/.claude-worker`) and reads its OWN `.claude.json`
> `mcpServers` — NOT the `~/.claude.json` that an interactive `claude mcp list`
> shows. A bare `claude mcp list` here probes the WRONG surface: it can show
> figma/chrome-devtools "connected" while the worker has none, so an MCP-naming
> `env_prereq`/AC passes review and then fast-fails to `blocked` in the loop
> (observed: uccargo, 2026-06-13 — two stalls from exactly this gap).

Get the worker's MCP set with the helper (this is the surface the loop runs in):

```bash
python "<skill-root>/ilk-loop/scripts/worker_mcp.py" list
# -> {"worker_home": "...", "mcpServers": ["chrome-devtools", "figma"]}
```

The loop's tool surface = Claude Code built-ins (Bash/Edit/Read/Grep/Glob/
Task/Write/etc.) PLUS the MCPs in that `mcpServers` list. This determines what
counts as loop-shippable in step 6. (You may also run the interactive
`claude mcp list` for comparison, but the WORKER set is authoritative.)

- If `chrome-devtools` is in the worker set → browser verification IS
  loop-shippable; plan browser ACs as normal loop steps.
- **If the batch needs an MCP the worker LACKS** (e.g. figma/chrome-devtools
  missing from `worker_mcp.py list`): **STOP before the step-5 approval.** Do
  not write a sub-plan that hard-gates on an MCP the worker can't reach. Tell
  the user to add it to the worker first:
  `<toolkit>/tools/claude-worker/ilk-worker-mcp add <name>` (the `ilk-worker-mcp`
  helper installs the server into the worker home + copies only that server's
  OAuth, never the planner's Claude identity). Re-probe, then continue.
  - For a capability with a documented fallback (e.g. figma → build to an
    existing page pattern), prefer encoding the **degrade path** in step logic
    over a hard `env_prereq` (see decomposition-principles §"degrade-to-default"
    and the step-7 env_prereq-vs-fallback lint) — do NOT make it a fast-fail gate.

Mention the **worker MCP set** (from `worker_mcp.py list`, not the interactive
session) in the step-5 proposal so the user knows which surface the plan was
validated against.

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

Two lints enforce this mechanically at `/ilk-plan` step 7g:

- **`lint_scope_path_off_base_branch`** (per-path) — flags any
  `scope_paths` entry that exists on a ref other than the declared
  base.
- **`lint_one_batch_one_branch`** (master-level) — rejects a batch
  whose sub-plans' paths resolve to different branches, or whose
  master lacks `base_branch:` entirely.

Both run under `plan_lint.py --master`. The plan-time gate catches the
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

Show the user a markdown table proposal. **In meta mode add a `Repo`
column** between Slug and Items so the user can see at a glance which
member each sub-plan targets:

Single-repo mode:
```
| # | Sub-plan slug | Items | Tier | Priority | Why grouped | Steps (est.) |
|---|---|---|---|---|---|---|
| 1 | <slug-1> | <list of items> | <tier> | P? | <one-line rationale> | <N> |
| ... |
```

Meta mode (`Repo` column required; values must come from `meta_members`):
```
| # | Sub-plan slug | Repo | Items | Tier | Priority | Why grouped | Steps (est.) |
|---|---|---|---|---|---|---|---|
| 1 | <slug-1> | <member-name> | <list of items> | <tier> | P? | <one-line rationale> | <N> |
| ... |
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

If `--yes` was passed, print `Approval gate skipped (--yes).` and
proceed to step 6 without waiting. Otherwise:

ASK the user explicitly: "OK to proceed with this grouping, or want
to adjust?"

Iterate until they approve. Do NOT write any files yet.

## 6. Write the plan files

Once approved, write all files in one batch under the
`external_plans_dir` resolved in step 2 (i.e.
`~/.ilk-data/projects/<key>/plans/`):

- `<external_plans_dir>/MASTER-YYYY-MM-DD-execution-plan.md`:
  - Front-matter per SKILL.md spec — **write `status: draft`**. The master is
    authored-but-not-yet-released; `draft` is non-runnable (invisible to the
    scheduler and `loop_status`), so a live scheduler/loop cannot grab it
    mid-authoring. It is flipped to `queued` only in step 8, after QC passes.
  - **Write `supervised_only: false`** — this is the default and it is almost
    always correct. Set it `true` in exactly one case: ANY sub-plan's
    `scope_paths` *modifies* loop infrastructure (`loop_status.py`,
    `scheduler_scan.py`, `promote_next_master.py`, `plan_status.py`,
    `scheduler.*`). Mere mention, or an import in prose/test code, does not
    warrant it. Such a self-modifying batch must never be autonomously
    dispatched — the scheduler and `promote_next_master` skip
    `supervised_only`; only manual `/ilk` runs it. Do NOT auto-flip it to
    `queued` while a scheduler is live (keep `draft`, run supervised with the
    scheduler stopped). Warn about this in the step-9 report.

    In practice only a batch planned against the **ilk-skills toolkit clone**
    can have those paths in scope. **In a consumer project, write
    `supervised_only: false` and do not reconsider** unless the user explicitly
    asks for `true` in this session. Never reach for it to mean "risky",
    "unverified", "needs human review", "touches auth", or "external API
    contract" — that is `status: draft` plus a verification tier
    (decomposition-principles.md §13, §15). It is a costly flag: it removes
    autonomous dispatch permanently AND makes `ilk-runner` preflight hard-stop
    even a manual `/ilk-run` while a cross-project scheduler is alive. For real
    side-effect hazards (mutating a live clone, pushing to a shared remote),
    fix them in config (`clone_path` → throwaway clone) or with `--dry-run`
    gates, and keep the batch autonomous. Step 7g enforces both directions.
  - Workstream map (ascii box diagram is fine)
  - Sub-plan registry markdown table
  - Execution rationale section
  - Cross-workstream dependency notes
  - "Out of scope" section (anything explicitly excluded)
  - "Rollout strategy" section — state which sub-plans are autonomous
    (loop-verified) vs supervised/human-paired (compile-only, device-manual),
    and the recommended run order per tier.
  - "Progress log" table (1 row: the creation entry)

- One file per sub-plan: `<external_plans_dir>/YYYY-MM-DD-<slug>.md`, derived from
  `<skill-root>/ilk-loop/templates/subplan-template.md`. Fill in
  REAL content, not placeholders:
  - Front-matter with accurate `tickets:` and `estimated_steps:` values
  - **`verification_tier`** — one of `loop-verified`, `compile-only`,
    `device-manual` (see decomposition-principles §12 for definitions and
    when each tier is trustworthy). Absent ⇒ treated as `loop-verified`
    for back-compat, but every new sub-plan should declare it explicitly.
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
committing. 7a / 7d-env / 7e findings are warnings (surface to the
user); 7b mutates files; **7c (meta projects only), 7d-errors, and 7g's
`supervised_only` scope guard are hard gates** — never advance to step 8
with an unresolved hard finding.

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
- **Diagnostic / no-commit step 0** (decomposition-principles.md §6) —
  a step 0 whose verb is purely investigative ("Reproduce",
  "Investigate", "Root-cause", "Figure out", "复现/排查") AND that
  carries no commit line is a stall waiting to happen (uccargo,
  2026-05-26: "Reproduce + Figma reference" step 0 ended with zero
  commits, `stuck-no-progress`). Either fold the reproduction into
  step 1 so the first step ends in a constructive commit, or give
  step 0 a concrete artifact + `local_checks` it must produce. This
  check reads each sub-plan's step structure, not just `local_checks`.
- **per-file-only gate on a shared module** — a `local_check` that runs
  only the new file's tests while the change touches a shared/imported
  module hides integration + test-state-leak bugs (decomposition-principles
  §8, field-log bugs #1/#2); the last step must run the FULL suite.
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
- Artifact/tmp paths outside the resolved `project_root` — any path a
  step writes to (screenshots, dumped API responses, scratch files)
  must sit under `project_root` (in meta mode: under the sub-plan's
  `repo` member dir). The tool sandbox rejects out-of-root writes
  (crawler, 2026-05-29: a dump to `e-com-ops/tmp/` was denied because
  root was `e-com-ops/crawler/`, wasting a retry). Rewrite any such
  path to a project-relative scratch dir.

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

### 7g. Degrade-discipline lints (`plan_lint.py`)

Run the planner degrade-discipline lints over every newly-written sub-plan —
these are the *enforced* form of guards that used to be prose (and were
skipped, stalling uccargo twice on 2026-06-13):

```bash
python "<skill-root>/ilk-loop/scripts/plan_lint.py" \
  --master <MASTER .md> <each new sub-plan .md>
```

Pass `--master` — it enables the master-level checks (slug-collision and the
`supervised_only` scope guard) which cannot run from sub-plan text alone.

It emits finding classes (warnings except where marked **hard finding** —
surface counts in the step-9 report; fix before launching):

- **env_prereq-vs-fallback contradiction** — a sub-plan that hard-gates on an
  MCP via `env_prereqs: claude mcp list | grep -q X` AND documents a
  fallback/degrade path for the *same* X. The env_prereq fast-fails to
  `blocked` before the fallback can run, so they contradict. X is optional ⇒
  encode the degrade path in step logic; do NOT make it a hard env_prereq.
- **block-when-default-exists** — a step instructs `set status: blocked` while
  the sub-plan documents a safe default/fallback. On a headless loop `blocked`
  = stall + human; prefer degrade-to-default (see
  decomposition-principles.md → "Degrade-to-default over block").
- **contract-change-review** — a sub-plan's `scope_paths` touch a
  contract-governed file but the body doesn't reference the contract docs.
  See 7h below.
- **escaped-bug-regression-gate** — a sub-plan has `regression_for:` set
  (declares it fixes a human-found escaped bug) but carries zero
  `local_checks` (neither frontmatter nor per-step). An escaped-bug fix
  must have a reproducing local_check to prevent the same class of bug
  from escaping a gate twice. See decomposition-principles.md §18.
- **e2e/device-polling local_check lacks env_prereq** — a sub-plan declares an
  e2e, browser-automation, or service-poll `local_check` (e.g. `node e2e/*.mjs`,
  `playwright test`, devtools, poll phrasing) but has no `env_prereqs`
  reachability probe and no `docs/loop/preflight.sh` reference. The gate will
  burn its timeout into `local-checks-stuck` when the dependency is unreachable.
  Add an `env_prereqs` entry with a fast-fail `verify_cmd` (see
  decomposition-principles.md §10).
- **whole-suite gate baseline** — a `local_checks` command runs a pre-existing
  whole test suite (bare `pytest`/`vitest` with no file arg, `bash tests/*.sh`,
  `npm test`) with no "baseline-green on \<platform\>" note in the sub-plan
  body. If that suite is baseline-red on the run platform (e.g. POSIX-only
  perms check on Windows), every step will false-block. See
  decomposition-principles.md §16.
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
  single test file. The callers' integration is never exercised. The finding
  names the importing files and warns that widening the gate to a directory or
  whole suite will also require a 'baseline-green on \<platform\>' note (see
  whole-suite gate baseline above). Uses a caller-aware detector (grep for
  `from <mod> import` / `import <mod>`, excluding test files); if the oracle
  cannot run, it reports nothing rather than firing. See
  decomposition-principles.md §8.
- **supervised_only scope guard** (**hard finding**, needs `--master`) — fires
  in both directions: (a) the MASTER sets `supervised_only: true` but no
  sub-plan's `scope_paths` modifies loop infra — the flag is unwarranted, set it
  `false` unless the user explicitly asked for it; (b) a sub-plan's
  `scope_paths` names a loop-infra file but the flag is off — a self-modifying
  batch must not be autonomously dispatchable. Never resolve (a) by inventing a
  rationale; resolve it by setting the flag `false` and using `status: draft` if
  a human gate was what you wanted. See decomposition-principles.md §13.
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

`plan_lint.py` exits non-zero when it finds anything; treat findings as
must-fix-before-launch (a contradiction here is what actually stalled the loop).

### 7h. Contract-change review (`plan_lint.py`)

The same `plan_lint.py` invocation from 7g also checks **contract-change
discipline**: if a sub-plan's `scope_paths` touch a contract-governed file
(`collect.py`, `watchdog.*`, `scheduler.*`, `run_ilk_loop_claude.*`,
`loop_status.py`, `promote_next_master.py`, `plan_status.py`, `status_all.py`,
`render_tray.py`), the sub-plan body MUST reference at least one of:

- `skills/ilk-loop/references/orchestration-collaboration.md`
- `skills/ilk-loop/references/detached-component-contracts.md`

This enforces the "Adding a new reader or writer" checklist from
`detached-component-contracts.md` — a new reader/writer of a shared contract
can't be authored without consulting the contract docs. See
`orchestration-collaboration.md` L1-L4 for the invariant layer this protects.

A finding here is a warning (surface in the step-9 report; fix before
launching).

## 8. Persist (no project-repo commit)

Plans live OUTSIDE the project repo, so there is **nothing to git-add
inside the project**. The files have already been written to
`~/.ilk-data/projects/<key>/plans/` in step 6.

### 8a. Auto-register the project (idempotent, opt-out aware)

After the plans are written, register the project so a running
scheduler can discover it — unless the project opts out.

1. Check for opt-out: read `<project_root>/.ilk-launch.json` (if it
   exists). If it contains `"autoschedule": false`, **skip
   registration** and note this in the step-9 report ("Project opted
   out of auto-scheduling — not registered in projects.json").
2. Otherwise, run:
   ```powershell
   python "<skill-root>/ilk-loop/scripts/register_project.py" --path "<project_root>"
   ```
   This is idempotent — safe to re-run if the project is already
   registered. It writes a `{name, path}` entry to the launcher's
   `projects.json` registry (BOM-free utf-8).
3. Capture the output. The JSON result has `added: true/false` and
   `name`. You'll report this in step 9.

**Why this matters:** a brand-new project is `skip-unresolved` by the
scheduler until it appears in `projects.json`. This one command makes
it discoverable — but the scheduler still applies its own
`active`/`queued` + `supervised_only` + `draft` gates (no surprise
autonomous runs of supervised/draft work).

### 8b. Release the master (`draft` → `queued`)

The MASTER was written `status: draft` in step 6, so the live scheduler/loop
could not pick it up while you authored and QC'd it. **Now that step-7 QC has
passed**, flip the MASTER's front-matter `status: draft → queued` so it
becomes runnable. Skip this only if QC produced an unresolved hard finding —
then leave it `draft` and tell the user what to fix.

> ⚠️ A `queued` master is immediately dispatchable by a running scheduler. For
> a **self-modifying** batch (edits `loop_status.py` / `scheduler_scan.py` /
> `promote_next_master.py` / `scheduler.*` / `plan_status.py`), keep it `draft`
> while the scheduler is live and run it supervised with the scheduler stopped.
> Surface this in the step-9 report.
>
> Such a batch should ALSO already carry `supervised_only: true` from step 6 —
> but the two are separate gates and **`supervised_only` is not an alternative
> to holding at `draft`**. `draft` = not released; `supervised_only` = never
> self-dispatched. Do not set `supervised_only` to express "not ready" or "human
> should review first" (decomposition-principles.md §13); that is what `draft`
> is for, and `plan_lint` reports the substitution as a hard finding.

If you (or the user) want version history for the plans themselves,
that's a separate concern: `~/.ilk-data` can be its own git repo or
backed up however the operator prefers. **Do not initialise that
repo from this command** — leave it to the operator's own tooling.

If the project is still on the legacy in-tree layout
(`resolved_source` was `in-tree` or `walk-up` in step 2), tell the
user explicitly:

> The new plans were written to `~/.ilk-data/projects/<key>/plans/`,
> NOT to `<project>/docs/plans/`. Run
> `python <skill-root>/ilk-loop/scripts/migrate_plans_to_external.py
> --project . --apply` once the current loop is idle to migrate the
> legacy plans alongside, then commit the resulting deletions in the
> project repo.

## 9. Final report

End your turn with:

1. A brief summary: "Wrote 1 master plan + N sub-plans covering M items
   to `~/.ilk-data/projects/<key>/plans/`."
2. QC pass results: lint findings (count, incl. diagnostic step-0),
   invariants woven (count), env_prereqs findings (count), cold-read
   gaps surfaced (count). Each non-zero count expanded with a short
   bullet list so the user can act.
3. **Registration outcome** (from step 8a):
   - If registered: *"Registered `<name>` in projects.json — a running
     scheduler can now auto-dispatch its non-`supervised_only` batches;
     remove the entry to opt out."*
   - If opted out: *"Project opted out of auto-scheduling
     (`.ilk-launch.json` `autoschedule: false`) — not registered in
     projects.json."*
   - If already registered: *"`<name>` already in projects.json — no
     change."*
4. The output of `python "<skill-root>/ilk-loop/scripts/loop_status.py"`
   so the user sees the new pending state.
5. **Gate-status line.** If ANY sub-plan declares `local_checks`, report:
   *"Gates: N/N sub-plans declare `local_checks` → auto-enabled on launch
   (banner: `gates ON`)."*
6. **Verification-enforcement warning (decomposition-principles.md §11).**
   If ANY newly-written sub-plan carries runtime `local_checks`
   (frontmatter or per-step), tell the user verbatim:

   > These sub-plans' `local_checks` **auto-enable** when you launch the
   > loop — `launch.ps1` detects them and defaults `-RunLocalChecks` ON.
   > Confirm the **`gates ON (-RunLocalChecks auto-enabled)`** banner in
   > the launch output. The `-RunLocalChecks`/`-NoLocalChecks` flags live
   > on `launch.ps1`, not on `ilk-run`. Skipping verification risks
   > marking broken work `shipped` (this exact gap shipped a broken e2e
   > test on uccargo, run 20260602). Also: `shipped` is commit-only and
   > local — verify (run the e2e) → push → cloud-re-run before trusting
   > the batch.

   Skip this only if no sub-plan has any runtime `local_checks`.
7. **Tier-mix summary.** List the tier breakdown of the batch:

   ```
   Tier mix: N loop-verified, M compile-only, K device-manual
   ```

   If any sub-plan is NOT `loop-verified`, append:

   > **NEEDS HUMAN VERIFICATION**: compile-only and device-manual sub-plans
   > require a human + device pass after the loop marks them `shipped`.

8. Tell the user: "Ready to execute. Open a fresh chat and type `/ilk`
   — gates auto-enable when sub-plans declare `local_checks`; confirm
   the **`gates ON`** banner in the launch output."

## Boundary rules

- **Never skip step 5 (user approval)** unless the operator passed
  `--yes` — grouping decisions are subjective; humans always sign off.
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
  is the source-adapter's job (e.g., `/ilk-lark`).
