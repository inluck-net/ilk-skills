---
plan: <short-slug>
status: pending
current_step: 0
tickets:
  - T-YYYY-NNNN
priority: P2
estimated_steps: 0
last_updated: YYYY-MM-DD
# --- Verification tier (see decomposition-principles.md §12) ---
# How trustworthy is "shipped" for this sub-plan?
#   loop-verified  — runtime gate proves correctness (pytest boots the app, real
#                    HTTP/CLI/browser smoke runs). Trustworthy when shipped.
#   compile-only   — only analyze/build/tsc/mypy runs. Ships scaffolding; a human
#                    must verify behaviour.
#   device-manual  — correctness needs a physical device / GUI / external app.
# Absent ⇒ loop-verified (back-compat with plans that predate tiers).
verification_tier: loop-verified
# --- Escaped-bug regression gate (see decomposition-principles.md §18) ---
# Set this field (to the escaped-bug tracker id, e.g. "T-2026-0042") ONLY
# when this sub-plan fixes a human-found bug that a gate should have caught.
# Doing so requires at least one reproducing local_check (frontmatter or
# per-step) — plan_lint.py enforces structural presence automatically.
# Leave unset (or empty) for normal sub-plans that don't fix escaped bugs.
regression_for:
# --- Meta-project routing (REQUIRED in meta projects, ignored otherwise) ---
# In a meta project (the parent dir has .ilk-meta.json), every sub-plan must
# declare exactly one member repo. The ilk-loop driver `cd`s into that
# repo for all of this sub-plan's commits, local_checks, CI waits, and
# ship-report generation. The value must match a `name` from .ilk-meta.json.
# Cross-repo sub-plans are not allowed by convention — coordinate them at
# the MASTER level via `depends_on` instead.
repo: <member-name>
# --- Sub-plan dependencies (see decomposition-principles.md §2-§3) ---
depends_on: []                 # IDs of prior sub-plans whose status==shipped is required
# --- Runtime data prerequisites (distinct from code deps in depends_on) ---
# What runtime data state must exist for this sub-plan's steps to run.
# Three accepted entry shapes — pick whichever fits the prereq:
#
#   registry_key:  reference into docs/loop/fixtures-registry.{yml,yaml,json}
#     - registry_key: test_accounts.portal_customer
#       # optional: override the registry's verify_cmd for this sub-plan
#       verify_cmd: "curl -sf -X POST $API/auth/login/ ... | jq -e .data.access"
#
#   verify_cmd:    standalone command that proves the prereq holds (exit 0)
#     - description: "DB has at least one order in PENDING_PAYMENT status"
#       verify_cmd: "psql -t -c \"select count(*)>0 from orders where status='PENDING_PAYMENT'\" | grep -q t"
#
#   description:   free-text fallback when no machine check exists
#     - description: "design system v2 tokens loaded in tailwind.config.js"
#
# A future preflight runner will parse the structured entries and try
# them in order before the step starts. For now, the planner uses these
# to drive sub-plan content (step 0 invokes the relevant seed, helper,
# or curl probe). Free-text "need a test account" is the anti-pattern
# /ilk-plan step 4c (fixture discovery) exists to prevent — always
# reference a concrete registry_key or verify_cmd when one exists.
data_prereqs: []
# --- Runtime environment reachability (see decomposition-principles.md §10) ---
# Distinct from data_prereqs (which is about data *state*). env_prereqs
# declares whether the runtime environment this sub-plan needs is
# *reachable*: dev/staging server up, remote data source online, an
# external design source (Figma) fetchable, a required MCP connected.
# Each entry carries a cheap verify_cmd that fast-fails (exits non-zero
# in milliseconds) when the dependency is down, so the loop reports
# "blocked: dependency unreachable" at step 0 instead of burning a whole
# iteration discovering it. Both stuck-no-progress stalls observed to
# date were reachability failures the worker stumbled into mid-iter.
#
# Leave empty ([]) only when the sub-plan touches no running service /
# remote source / external MCP. For project-wide reachability that
# applies to every authed sub-plan, prefer a docs/loop/preflight.sh
# invariant (see ilk-loop SKILL.md → "Project-side preflight") instead.
#
# env_prereqs:
#   - description: "portal dev server reachable"
#     verify_cmd: "curl -sf -o /dev/null http://localhost:3000"
#   - description: "chrome-devtools MCP connected"
#     verify_cmd: "claude mcp list | grep -q chrome-devtools"
env_prereqs: []
# --- Post-ship: restart affected long-running services (P6) ---
# If this sub-plan modifies code that a long-running service (dev server,
# queue worker, background process) loads at startup, restart that service
# after the loop ships. A dev server started before the loop keeps serving
# stale code → manual verification hits removed/renamed endpoints (HTTP 405).
# Note the restart requirement here so the human (or a future automation)
# knows to do it.
# ---
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
  Text-only worker? Interpret screenshots via `vl_describe.py`
  (see references/vl-describe-tool.md).
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
  **Note:** If the remote is shared (check `.ilk-remote-type`), omit the
  `[plan:<slug>#step-N]` trailer from the commit message.

### Step 1 — <short title>
- <bullet>
- Commit: `<type>(<scope>): <summary> [plan:<slug>#step-1]`
  **Note:** If the remote is shared (check `.ilk-remote-type`), omit the
  `[plan:<slug>#step-N]` trailer from the commit message.

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

  Runtime failure-mode checklist (device-manual / compile-only only):
  A build cannot catch these — verify each on a real device/runtime:
  - [ ] **Cross-isolate / cross-process shared state** —
        `SharedPreferences` (and similar) caches are **per-isolate**;
        a write in one isolate is NOT visible to another. Pass state
        in the message payload; don't re-read shared storage across
        an isolate boundary.
  - [ ] **Event-listener registration ordering** — register `on(event)`
        listeners **before** any `await` that could let the event fire
        first (dropped-event class).
  - [ ] **Cold-start vs warm-start** — deep links / intents behave
        differently on a fresh process vs a running one. Test **both**,
        from a true `pm clear` (or platform equivalent).
  - [ ] **Concurrent connect/reconnect on shared resources** — coalesce;
        never run two connects on one socket / tunnel / shared resource.
  - [ ] **Permission/consent timing** — foreground vs background; OEM
        background-launch blocks (e.g. Huawei). Grant runtime permissions
        before first use; no crash from accessing a protected API before
        the consent dialog resolves.
  - [ ] **OEM divergence** — Huawei/HarmonyOS: no Google backup,
        background-launch interception, hidden log tags, custom USB debug
        bridge. Test on target OEM if the feature touches platform APIs.

  **Observability AC (P7):** device-manual sub-plans must include, as an
  acceptance criterion, `debugPrint` / structured logs at every decision
  point the human verifier will need: which config was read, which branch
  was taken, the connection / operation target, and success/failure with
  the error. A device-manual sub-plan whose only diagnostics are "it works
  or it doesn't" is under-specified — the human will spend entire device
  cycles guessing instead of reading a log line.

  **Non-UI trigger path (keep-pattern-2):** device features should ship a
  **non-UI trigger / provisioning path** (e.g. `adb shell am start -d
  'scheme://...'`, a CLI command, or a scriptable API call) so each device
  cycle is automatable — the human can reproduce and test without typing /
  scanning / tapping through the UI every time.

- If anything fails, re-open the loop with this sub-plan flipped back
  to `status: in-progress, current_step: N`. Otherwise no action.
-->

## Findings

_(filled by the loop during early steps)_

## Reference reading

- <doc path or URL>
- <doc path or URL>
