---
plan: <short-slug>
status: pending
current_step: 0
tickets:
  - T-YYYY-NNNN
priority: P2
estimated_steps: 0
last_updated: YYYY-MM-DD
# --- Sub-plan dependencies (see decomposition-principles.md §2-§3) ---
depends_on: []                 # IDs of prior sub-plans whose status==shipped is required
data_prereqs: []               # runtime data state required (distinct from depends_on)
# --- Machine-checkable acceptance (see decomposition-principles.md §1) ---
# Run by the loop driver after each step's commit. Fail-any → step does
# not advance, output written to "Findings" section.
local_checks: []
# Example:
# local_checks:
#   - command: cd portal && npm run typecheck
#     timeout: 120
#   - command: cd portal && npm test -- --testPathPattern=orders
#     timeout: 300
# --- Quality gates — see ai-coding-workflow/quality-gates-spec.md + ship-report-spec.md ---
scope_paths:
  - "<glob/prefix for files this sub-plan may touch>"
unit_test_targets:
  - "<pytest or vitest path>"
e2e_test_targets:
  - "<playwright tag or spec>"
must_add_tests: true
ci_required: true
ci_status_endpoint: gitee
ci_timeout_minutes: 30
ci_max_retries: 2
extra_dangerous_paths: []
allow_dangerous_paths: []
expected_entities:
  migrations: []
  api_endpoints: []
  db_tables: []
---

# Sub-plan: <human-readable title>

Part of [MASTER-YYYY-MM-DD-execution-plan](./MASTER-YYYY-MM-DD-execution-plan.md).
**Order #N** — <one-line rationale for execution position>.

## Before you start

A fresh AI session opening this file directly should also read, in
order:

1. The MASTER plan referenced above — workstream map, cross-cutting
   rules, execution rationale.
2. `~/.ilk-data/projects/<project-key>/PREREQUISITES.md` (if present)
   — active dev env facts, tools required, env vars, restore
   procedures.
3. Any project-specific specs or design docs listed in the
   "Reference reading" section at the bottom of this file.

When you finish a step, the loop driver (not you) runs `local_checks`
from the frontmatter. If they pass, `current_step` is bumped and the
next step starts in a fresh session. If they fail, the failure output
is appended to "Findings" below — read it and try again.

Do NOT mutate state declared in `PREREQUISITES.md` section A
("Active dev environment"). Workers must not restart, kill, or
reconfigure those services.

## Tickets in scope

| Ticket | Title | Type | Pri | Module |
|---|---|---|---|---|
| T-YYYY-NNNN | <short title> | bug / 新功能 / 体验优化 | P? | <module path> |

## Objectives

1. <one-line objective>
2. <one-line objective>

## Acceptance criteria

> **Loop-shippable rule**: every AC the loop is expected to verify must
> be checkable using a tool the loop has — either Claude Code's
> built-ins (Bash/Edit/Read/Grep/etc.) or an MCP shown by
> `claude mcp list`. Browser-based ACs are loop-shippable IF
> `chrome-devtools` is registered (preferred — see SKILL.md →
> "Loop-shippable verification" → Option A). Otherwise move them to
> the **Manual user verification** section at the bottom of this file.
>
> **Quality gates (trust stack v0)**: after this sub-plan ships, ilk-loop
> runs gate 2 (CI wait) → gate 3 (reviewer agent) → gate 4 (ship-report).
> Gate 1 (unit + e2e + build) is the **Verification** step below — must
> pass before the ship commit. Specs: `ai-coding-workflow/quality-gates-spec.md`,
> `reviewer-agent-spec.md`, `ship-report-spec.md`.

- **AC-1**: <observable, testable outcome>
- **AC-2**: <observable, testable outcome>

<!--
Cross-cutting AC — REQUIRED for any sub-plan that changes UI (Vue/React/
HTML/CSS/Tailwind tokens). Delete this block ONLY if no rendered surface
is touched.

Rationale: 2026-05-22 portal/Figma post-mortem showed that every component-
level sub-plan can pass in isolation while the assembled page still drifts
from design (legacy layout wrappers, missing utility-bar elements, Tailwind
v4 token bugs in @theme inline causing rounded-* to evaluate to 0px). Each
sub-plan must independently verify the assembled page, not just its own
component.
-->

- **AC-VIS** (UI sub-plans only): chrome-devtools verification on the
  rendered surface this sub-plan touches.
  - `take_snapshot` of the full viewport at the canonical breakpoint.
  - For each Figma frame referenced in this sub-plan, assert at least
    one element-level shape constraint via `evaluate_script`:
    ```js
    () => {
      const el = document.querySelector('[data-testid="..."]');
      const r = el.getBoundingClientRect();
      return { w: r.width, h: r.height, br: getComputedStyle(el).borderRadius };
    }
    ```
    expected to match the Figma spec within ±2 px / exact radius token.
  - For Tailwind v4 projects (myproj portal, myproj), probe at
    least one `rounded-md/lg/xl/2xl` utility on a real rendered node
    using `getComputedStyle(...).borderRadius` and assert non-zero.
  - Out-of-scope: pixel diff against Figma render (manual eyeball at
    ship-report review time is sufficient for v1).

## Out of scope

- <explicit non-goal that prevents loop scope creep>
- <explicit non-goal>

## Steps

> Each step MAY declare its own `local_checks:` block (yaml) right
> under the heading. If present, the loop driver runs those checks
> after the step's commit. If absent, only the sub-plan-level
> `local_checks` from frontmatter run at ship time.

### Step 0 — <short title>
```yaml
local_checks:
  - command: <command that proves this step's outcome>
    timeout: 60
```
- <bullet>
- <bullet>
- Commit: `<type>(<scope>): <summary> [plan:<slug>#step-0]`

### Step 1 — <short title>
- <bullet>
- Commit: `<type>(<scope>): <summary> [plan:<slug>#step-1]`

<!-- ... add more steps as needed; remember to update estimated_steps in front-matter -->

### Step N-1 — Verification (gate 1)

- Run scoped unit tests:
  - Python: `pytest -x <unit_test_targets>` all green
  - Node: `npm test -- <unit_test_targets>` or `vitest run <unit_test_targets>` all green
- Run scoped e2e: `npx playwright test <e2e_test_targets>` all green
- Run build: `npm run build` / `python -m mypy` (per project)
- Any failure → do **not** commit; refine and re-run this step.
- chrome-devtools browser smoke (keep existing ad-hoc validation beyond e2e).
- Commit (test): `test(<scope>): pass <slug> verification suite [plan:<slug>#step-N-1]`

### Step N — E2E + handoff
- CLI verification covering all loop-shippable acceptance criteria
  (pytest run + any verify scripts authored in earlier steps).
- Move all listed tickets to the next tracker state (e.g. `待验证` for Lark).
- Update plan: `status: shipped`.
- Commit: `chore(plans): <slug> shipped [plan:<slug>#ship]`

<!--
If the final verification needs a browser:

  PREFERRED: keep it as a normal loop step using `chrome-devtools` MCP
  (take_snapshot / click / type_text / list_network_requests / etc.).
  Confirm `claude mcp list` shows chrome-devtools first; if not, ask
  the user to run:
    claude mcp add chrome-devtools --scope user -- npx chrome-devtools-mcp@latest --browserUrl http://localhost:9222

  FALLBACK (only when registering the MCP isn't worth it): keep the
  steps above CLI-only and add the section below.

## Manual user verification (run AFTER the loop ships this plan)

The loop ships once the CLI ACs pass; this is the human's final
check that the contract holds inside the actual UX.

- Open Chrome, navigate to <url>.
- <step-by-step instructions>
- Expected: <toast / response / state>
- If anything fails, re-open the loop with this sub-plan flipped back
  to `status: in-progress, current_step: N`. Otherwise no action.
-->

## Findings

_(filled by the loop during early steps)_

## Reference reading

- <doc path or URL>
- <doc path or URL>
