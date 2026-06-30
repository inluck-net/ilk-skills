# Decomposition Principles

How to slice a goal into a master plan + sub-plans that the ilk loop
can actually drive to completion. Followed by `/ilk-plan` during
proposal, and re-checked at the QC gate before files are written.

These principles are tool-agnostic — they hold regardless of language
or framework — but the verification advice assumes the loop has access
to Claude Code built-ins (Bash / Edit / Read / Grep / Glob / Task /
Write) plus whatever MCPs `claude mcp list` reports.

---

## 1. Tight contracts

Every sub-plan needs `local_checks` that mechanically prove correctness.
Prefer **runtime smoke** (live API call, integration test against a
running dev deployment, browser check via `chrome-devtools`) over
**compile smoke** (`tsc`, `mypy`, `cargo check`). Compile smoke proves
the code compiles, not that it works — a worker can honestly report a
sub-plan as ready while shipping a route that crashes on first real
call.

When compile-only is the honest best (no testable contract exists yet),
the sub-plan body must document what manual verification covers and
why a runtime smoke is impossible.

### Wrapper vs bespoke distinction

- **Wrapper sub-plans** — the new code wraps an existing backend
  function (`mycli foo` → `service-call FooHandler`). A `<binary> --help`
  smoke catches **nothing** about whether the wrapper correctly calls
  the backend, parses args, or formats output. It's theater. The smoke
  MUST call the backing function directly and assert response shape.
- **Bespoke sub-plans** — the sub-plan ships a brand-new backend
  function. The smoke calls that new function. `--help` on the
  introductory CLI verb is acceptable as a floor; behavior smoke is
  the upgrade.
- **Diagnostic sub-plans** — see principle 7. Should be re-scoped to
  constructive work rather than smoke-checked.

## 2. Code prereqs ≠ data prereqs

`depends_on` enforces code-build order. It does NOT guarantee that
prerequisite **data** exists in the dev deployment. If sub-plan B reads
a row that sub-plan A is supposed to create, B's `data_prereqs` must
say so — otherwise B can pass `local_checks` (compile) and fail at
runtime because no row exists.

Either seed the data inline in A's verification step, or declare it
explicitly in B's frontmatter so the worker knows to verify before
starting.

**A data prereq must be locally *producible*, not merely *named*.** A
seed command or backfill helper existing in the repo is not enough if
the data it depends on lives on infra the worker can't reach. Real
case (crawler, 2026-05-29): a sub-plan's AC required `inventory_image_url`
rows that only exist after a backfill whose source was a prod-test box
that had been offline 12 days. The backfill command was in-repo — a
fixture scan would have "found" it — but the worker spent its whole
iteration probing DB topology and `tailscale status`, then stalled with
zero commits (`stuck-no-progress`). When a `data_prereqs` entry's
producer needs anything outside the dev box (a remote DB, a VPN-only
host, a third-party import), the planner must either (a) wire a
**local** seeding path the worker can actually run, or (b) move the
dependent AC to a "Manual user verification" section. A `verify_cmd`
that can only pass when remote infra is up is a latent stall, not a
contract.

## 3. Minimal cross-task dependency

If two sub-plans share state, merge them or declare `depends_on` /
`data_prereqs` explicitly. Don't rely on implicit ordering inside a
master plan — the loop processes pending sub-plans in registry order
without inferring dependencies.

## 4. Group boundaries = real dependencies

When the master plan is grouped, group boundaries should mark genuine
serialization needs (group N+1 needs something group N produced). Don't
add a group boundary for cosmetic reasons — it hurts parallelism if the
runtime ever supports it, and it costs nothing today.

## 5. Size for one execution window — and pass the fresh-session test

Each sub-plan body should fit in a single fresh AI session:

- Body ≤ ~200 lines (excluding frontmatter, AC tables, and steps)
- Tight scope, ≤ 5 files touched typically
- **Operational test**: a fresh AI instance, given only the path to the
  sub-plan file and nothing else, must produce correct work without
  asking the user for clarification

If it can't, the sub-plan is missing context — usually a missing
"Before you start" header or missing inputs list. If it's just too
big, split it.

## 6. Distinguish diagnostic vs constructive work

Constructive work (build X, ship feature Y) fits the tight-contract
model. **Diagnostic work** (root-cause a 500, find why a query is
slow, figure out why tests flake) does NOT — the patch surface is
unknown until investigation completes.

Either:

- (a) Re-scope the diagnostic work to a constructive equivalent
  ("rewrite the legacy route to the new bridge" instead of "patch
  the 500"), or
- (b) Leave the investigation as a manual note in the master plan's
  success criteria, to be done outside the loop

Diagnostic sub-plans burn worker sessions without bounded progress.

**The same trap hides at step granularity.** A constructive sub-plan
can still open with a diagnostic *step 0* — "Reproduce the bug",
"Investigate current state", "Figure out the Figma reference" — that
produces no commit and has nothing mechanically-checkable to advance
on. Real case (uccargo, 2026-05-26): step 0 was "Reproduce + Figma
reference"; the local dev server was down, the Figma fetch came back
empty, and the iteration ended with zero commits (`stuck-no-progress`).
A no-commit step 0 is a stall waiting to happen. Either fold the
reproduction into step 1 (so the first step ends in a constructive
commit), or give step 0 a concrete artifact + `local_checks` it must
produce (e.g. a saved baseline snapshot the AC later diffs against).
The QC lint flags any step 0 whose verb is purely investigative and
that carries no commit line.

## 7. Cross-cutting invariants become test code

If the master plan declares cross-cutting invariants (e.g. "secret
never appears in unauthenticated payload", "no PII in logs", "every
audit write carries an actor"), each invariant ships as an executable
`assert` block, not prose hope.

The plan generator should weave each invariant's `assert` block into
every sub-plan whose body matches the invariant's `applies_when`
predicate. Invariants are constraints; if they can't be expressed as
a runnable command, keep them in prose rules but stop calling them
invariants — the worker will not enforce them.

## 8. Avoid common `local_checks` anti-patterns

Surfaced by the QC lint pass before sub-plans go to the loop:

- `| head` / `| tail` / `| awk 'NR==1'` after a check command →
  pipeline exit-status is lost; the check always "passes" regardless
  of upstream result. Use `grep -q PATTERN file` (single command, real
  exit code) or split into separate `local_checks` entries.
- `grep` without `-q` and without `-E '<expected-pattern>'` → tests
  for existence of a string, not for the contract value. Tighten the
  pattern, or use `jq -e` for JSON.
- `<binary> --help` on a CLI verb that wraps a backend function →
  tests that the binary exists, not that it works (see principle 1's
  wrapper-vs-bespoke distinction).
- Compile-only smokes (`tsc`, `mypy`, `cargo build`) as the **only**
  check on a sub-plan that adds an HTTP route, CLI verb, or new
  exported function → no runtime smoke; worker can ship a route that
  500s and report ready honestly.
- Multi-step bash pipelines without `pipefail` → mid-pipeline failures
  slip through. Either split into separate check entries or wrap with
  `bash -o pipefail -c '...'`.
- **per-file-only gate on a shared module** → a `local_check` that
  runs only the new file's tests (`pytest <one file>`) while the
  change touches a shared/imported module hides integration +
  test-state-leak bugs (WeChatRelay bugs #1/#2). When the change
  touches a shared module, the LAST step must run the FULL suite.
- **exact-equality on a growing set** (FM-0002) → a `local_checks`
  command asserts `== ["area", "perimeter"]` or
  `deepStrictEqual(result, ['a', 'b'])` against a registry /
  active-set accessor that is *designed to grow* (one entry per
  curriculum unit, per feature flag, per label). Adding a member
  breaks the gate — a brittle assertion, not a real regression.
  **Fix:** use a **superset / contains** assertion instead:
  `jq '.types | contains(["area"])'`, `assert set(result) >= {'area'}`,
  `expect(result).toEqual(expect.arrayContaining(['area']))`.
  The gate tests that required members are present, not that the
  set is frozen. `plan_lint.py` warns on this shape automatically.
- **frontmatter `local_checks` referencing a later-created path** →
  subplan-scope (frontmatter) `local_checks` run at **every** step. If a
  command references a path that the plan's own later steps create (e.g.
  `pytest tools/xbar/tests/` before step 2 creates that directory), the
  check fails on every earlier step (pytest exit 4 "file or directory not
  found") and stalls the loop. **Fix:** move the check to that step's
  per-step `local_checks` block so it runs only after the path exists.
  `plan_lint.py` (`lint_frontmatter_path_created_later`) warns on this
  shape automatically at `/ilk-plan` step 7g.
- **POSIX-only test assertions without platform guard** → a `.sh` test
  (or a `local_check` shell command) that asserts `rw-------`,
  `stat -c %A`, or `chmod` perm checks without a `uname`/`OSTYPE`
  guard cannot pass on Windows Git Bash. Real case: the 2026-06-28
  drawing-worker run's `test_worker_bootstrap.sh` false-blocked on
  Windows. **Fix:** add a `uname` guard or skip on non-POSIX platforms.
  `plan_lint.py` (`lint_posix_only_test_assertion`) warns automatically.
- **mock-only gate for a network tool** → a sub-plan that ships a new
  HTTP/network tool (urllib/requests/`_post`/`api.`) whose only gate
  mocks the network boundary (`patch(... _post)`, injected fake) with
  no integration/import-resolve/live smoke and no `env_prereqs`. The
  live path can ship broken. Real case: draw.py `_load_minimax_token`
  `ModuleNotFoundError` (2026-06-28). **Fix:** add an import-resolve or
  live smoke check alongside the mock-based unit tests.
  `plan_lint.py` (`lint_network_tool_mock_only_gate`) warns automatically.
- **model-only gate, no consumer AC (vertical-slice gap)** → a sub-plan adds
  a model/logic capability (new exported function or class in a non-UI module)
  whose every `local_check` is a pure-unit test (pytest/vitest) with no
  consumer entry-point keyword (UI hit-test, CLI verb, HTTP route, e2e sim).
  The model compiles and unit-tests pass but nothing proves a player/user can
  actually reach it. Field case: an `upgrade(econ)` method fully
  implemented and unit-tested, but no UI affordance to trigger it. **Fix:** add
  an AC that exercises the symbol through its real entry point.
  `plan_lint.py` (`lint_vertical_slice_ac`) warns automatically.
- **hardcoded consumer, data not wired (anti-hardcode gap)** → a sub-plan
  introduces per-instance data (per-stage path, per-tenant config, per-level
  theme) that an existing module should consume, but no `local_check` asserts
  the consumer actually reads the new data vs a hardcoded constant. The data
  exists but the consumer is still hardcoded to a different source. Field case:
  enemy movement hardcoded to one stage's `WAYPOINTS` while
  per-stage path arrays existed in the registry. **Fix:** add a local_check that
  verifies the consumer reads from the new data source.
  `plan_lint.py` (`lint_anti_hardcode_integration`) warns automatically.
- **promise-without-wiring (UI affordance without binding)** → a sub-plan
  introduces a UI affordance/prompt that advertises a capability ("press C",
  button label, tooltip, key hint, shortcut, speed indicator) but neither
  `local_checks` nor the body contains a wiring/trigger assertion (event
  handler, keybind, `click`, `press_key`, `take_snapshot`, e2e). The user is
  prompted to act but nothing is bound — worse than a missing feature because
  the prompt reads as a *promise*, and the unmet promise breaks trust. Field
  case: "press C for the codex" displayed on level-select with no event
  listener registered. **Fix:** add a local_check that verifies the affordance
  is actually wired (press_key, click, take_snapshot, or a binding assertion).
  `plan_lint.py` (`lint_ui_promise_wiring`) warns automatically.

### Orphaned-capability detector (post-ship QC tool)

`plan_lint.py` checks run at **planning** time (before the loop starts).  The
orphaned-capability detector (`orphan_check.py`) runs at **post-ship** time:
given a repo root and a list of newly-exported symbol names, it reports each
symbol whose only call sites are test files as "built but unwired."  This
catches the orphaned-model shape that planning-time lints can only warn about
(the lint says "add a consumer AC"; the detector confirms the consumer was
actually wired after the code ships).

```bash
python <skill-root>/ilk-loop/scripts/orphan_check.py --root <repo> --symbol foo --symbol bar
```

Uses `rg` when available for speed, with a pure-Python `os.walk` fallback for
hermetic testing and boxes without ripgrep.  A symbol is "unwired" iff every
non-definition reference is in a test file (`tests/`, `test_*.py`, `*.spec.ts`,
etc.).

**Known limitation:** this is a textual scan, not a type-aware analyzer.  It
cannot resolve re-exports, dynamic imports, or indirect references through
string keys.  Document false-negative expectations when using on a codebase
with heavy metaprogramming.

## 9. Cold-read self-check

Before declaring the plan ready, re-read every sub-plan under this
prompt-frame:

> "If a fresh AI session opened this file with NO prior conversation,
> what information would it need that isn't on this page?"

Surface gaps in this category:

- Missing absolute file paths
- Ambiguous decisions left open
- Undeclared external state
- Terminology not defined in the master plan
- Design-choice judgment calls not pre-resolved
- **Artifact/tmp paths outside the project root.** Any path a step
  writes to (screenshots, dumped API responses, scratch files) must sit
  **under the resolved `project_root`**. Real case (crawler, 2026-05-29):
  a step tried to write a network-response dump to
  `e-com-ops/tmp/...` while the project root was `e-com-ops/crawler/`,
  and the tool sandbox rejected it (`Access denied: path … is not
  within …`), wasting a retry. In meta projects this is per-member: the
  path must be under the sub-plan's `repo` member dir, not the meta
  root.

Fix obvious gaps inline (add the path, resolve the ambiguity). Surface
non-obvious ones as "review before launching" notes for the human.

This is a heuristic — the plan generator already knows the context, so
it can't truly simulate cold. The empirical version (a real fresh
session per sub-plan in preflight) is a future runtime feature; for
now, this is the skill-side approximation that catches obvious misses.

## 10. Environment-reachability prereqs (fast-fail, don't discover)

`data_prereqs` covers *data state* (§2). It does NOT cover whether the
*runtime environment* a step needs is reachable: is the local dev
server up? is the staging/preview URL live? is the remote data source
online? is the external design source (a Figma node) fetchable? is the
required MCP (`chrome-devtools`) actually connected?

Both `stuck-no-progress` stalls observed across projects to date were
**reachability** failures, not missing-fixture failures — the worker
spent an entire iteration *discovering* that a dependency was down,
then quit with no commit:

- crawler — remote backfill source (`linexcx-server`) offline 12 days.
- uccargo — `localhost:3000` dev server refused; Figma context empty.

The fix is to make reachability a cheap, declared, **step-0** check
that fast-fails clean instead of a discovery the worker stumbles into.
Each such prereq is an `env_prereqs` entry carrying a `verify_cmd` that
exits non-zero in milliseconds when the dependency is down, so the loop
can surface "blocked: dependency unreachable" immediately rather than
burning the iteration. Examples drawn from the two failures:

```yaml
env_prereqs:
  - description: "portal dev server reachable"
    verify_cmd: "curl -sf -o /dev/null http://localhost:3000"
  - description: "staging API reachable"
    verify_cmd: "curl -sf -o /dev/null https://staging.example.com/health"
  - description: "backfill data source (linexcx-server) online"
    verify_cmd: "tailscale ping -c1 linexcx-server"
  - description: "chrome-devtools MCP connected"
    verify_cmd: "claude mcp list | grep -q chrome-devtools"
```

This is the **per-sub-plan** complement to a project-wide
`docs/loop/preflight.sh` (see ilk-loop SKILL.md → "Project-side
preflight"). The preflight invariant is the right home for checks that
apply to *every* authed sub-plan; `env_prereqs` is for reachability
that's specific to one sub-plan (this sub-plan needs Figma; that one
needs the queue worker running). When a project has a `preflight.sh`
wired as a cross-cutting invariant, prefer extending it; otherwise
declare `env_prereqs` per sub-plan.

**Enforced by `plan_lint`.** The `lint_e2e_check_without_env_prereq` check
in `plan_lint.py` (see §8 local_checks anti-patterns) flags sub-plans that
declare an e2e, browser-automation, or service-poll `local_check` without a
corresponding `env_prereqs` reachability probe or `docs/loop/preflight.sh`
reference. This is the deterministic floor; the `/ilk-plan` step-7d-env
agent-prose pass stays as the richer advisory surface.

**Restart affected long-running services after the loop changes their
code.** A dev server started before the loop keeps serving stale code,
so manual verification hits removed/renamed endpoints (HTTP 405/410).
When a sub-plan modifies code that a long-running service loads at
startup, note the restart requirement in the sub-plan's env_prereqs or
post-ship section so the human (or a future automation) knows to do it.

## 11. Verification only counts when it's enforced (shipped ≠ verified)

A sub-plan's `local_checks` are the entire correctness story — and they
run **only when the loop is launched with `-RunLocalChecks`**. Without
that flag the loop advances on the worker's *self-report*: it sets
`status: shipped` because the agent said the step was done, never
having run the playwright/pytest/curl gate. Every other principle in
this document is theater if the gate never fires.

Real case (uccargo, run 20260602-071915): the postmortem classified it
`clean-success`, but the loop had been launched without
`-RunLocalChecks`. A broken e2e test shipped on self-report; the
classifier only reflected `status: shipped`, not real verification.
"clean-success" meant "the agent claims it's done", not "the tests are
green".

Two consequences the planner must encode:

1. **The final report (step 9 of `/ilk-plan`) must warn** whenever any
   sub-plan carries runtime `local_checks`: tell the user to launch
   with `-RunLocalChecks`, because otherwise those gates do not run.
2. **`shipped` is commit-only and local.** It does not mean pushed,
   does not mean CI-green, does not mean verified in the cloud. A human
   verify → push → cloud-re-run step is required before trusting a
   batch the loop reports as shipped. State this in the master plan's
   "Final success criteria (manual / out-of-band)" section so it's not
   lost.

## 12. Verification tier

Not all "shipped" sub-plans are equally trustworthy. A sub-plan gated
by a real runtime smoke (pytest boots the app, a real HTTP/CLI call
runs) is genuinely verified. A sub-plan gated only by `analyze`/
`build`/`tsc`/`mypy` is *compile-green* — it may still crash at
runtime. And a sub-plan whose correctness needs a physical device,
GUI, or external app cannot be verified by the loop at all.

**Field evidence** (WeChatRelay, 2026-06-08): the loop shipped 20
sub-plans "green"; 8 were compile-green-but-broken. Every bug fell
into one of two buckets — (a) **integration** bugs hidden by per-file
gates, or (b) **runtime/device/platform** bugs that `analyze`/`build`/
`compile` fundamentally cannot catch.

### The three tiers

Every sub-plan SHOULD declare a `verification_tier` frontmatter field:

- **`loop-verified`** — a runtime gate proves correctness in-loop
  (pytest boots the app; a real HTTP/CLI/browser smoke runs).
  Trustworthy when `shipped`. **This is the default** when the field
  is absent (back-compat with plans that predate tiers).

- **`compile-only`** — only `analyze`/`build`/`tsc`/`mypy` runs.
  Ships scaffolding; a human must verify behaviour before trusting
  the sub-plan as working.

- **`device-manual`** — correctness needs a physical device / GUI /
  external app that the loop cannot reach. The loop can ship the code
  but cannot confirm it works.

### Dependency rule (don't build blind on blind)

**Never queue a sub-plan whose runtime correctness depends on a
`compile-only` or `device-manual` sub-plan that has not been
human-verified.** Stacking unverified on unverified compounds risk —
each layer may be wrong in ways the next layer silently accommodates,
making the eventual failure harder to diagnose.

Example: queuing capture-dependent work behind a `device-manual`
`mediaprojection-capture` sub-plan would stack unverified on
unverified. Server batches (gated by full pytest) were correctly kept
independent of the capture.

### Mechanical enforcement

This dependency rule is now **mechanically enforced** at two levels:

1. **Promotion gate (`promote_next_master.py`).** A master that declares a
   `builds_on` front-matter field listing sub-plan slugs is checked at
   promotion time. If any dependency is shipped with `verification_tier` ∈
   {`compile-only`, `device-manual`} and lacks a `verified: true` marker,
   promotion is skipped with a logged reason. See
   `detached-component-contracts.md` Contract 4 for the marker spec.

2. **Batch-end summary (`loop_status.py`).** At the end of a batch,
   `loop_status.py` renders a `HUMAN VERIFY REQUIRED` banner listing
   shipped `compile-only`/`device-manual` sub-plans and their slugs. This
   ensures the signal is loud even when the loop runs without the promotion
   gate (e.g. manual `/ilk` sessions).

The `verified:` marker is set by a human after a manual or device pass (see
Contract 4 in `detached-component-contracts.md`). Absent ⇒ unverified
(back-compat). Malformed values degrade to unverified (never crash).

### Shared-module full-suite rule

When a sub-plan's changes touch a shared/imported module (not just a
leaf file), the LAST step must run the FULL test suite, not just the
new file's tests. Per-file-only gates hide integration bugs and
test-state-leak bugs that only surface when the full suite runs
(WeChatRelay bugs #1/#2). See also §8 anti-pattern
"per-file-only gate on a shared module".

---

## 13. Bias toward autonomy

The loop's default posture is **autonomous by default, safely**. Every
design decision should bias toward removing human bottlenecks, not adding
them.

### Default to scheduler-pickup

A new master plan should flow `draft` → `queued` → scheduler-dispatched
without human intervention. `supervised_only: false` is the default — only
flip it when there is a concrete, articulable reason (see the narrow rule
below). Do not set `supervised_only: true` as a "readiness gate" or
"because we haven't verified it yet" — that is what verification tiers
(§12) and gated dispatch (gated autonomous `local_checks`) are for.

### supervised_only is a narrow + persistent SAFETY flag

`supervised_only` exists for one purpose: batches that **modify the
loop's own runtime infrastructure** (`loop_status.py`,
`scheduler_scan.py`, `promote_next_master.py`, `plan_status.py`,
`scheduler.*`). A self-modifying batch must not be dispatched by the
scheduler or `promote_next_master` while they are live — they would be
reading code they are simultaneously rewriting.

Key properties:
- **Narrow** — only triggered when `scope_paths` (not body/prose)
  actually *modifies* one of those files. A test that imports
  `loop_status.py`, or prose that mentions `scheduler_scan.py`, does
  not warrant it.
- **Persistent** — once set, it stays set until the batch ships. It is
  not auto-cleared by any "readiness" signal.
- **Not a readiness gate** — never set `supervised_only: true` because
  the batch "isn't ready yet" or "needs human review". Use `status:
  draft` for not-yet-released, and verification tiers for trust level.

### Prefer fixing the gap over inserting a human

When a sub-plan's ACs seem to require human judgment, ask first: "can we
close this gap with a gate, a test, or a script?" The answer is usually
yes — and every gate we add is one fewer human-in-the-loop stall.

- Missing runtime smoke → write a pytest or curl-based check (§1).
- Uncertain dispatch → add a hermetic test that exercises the path (§11).
- External dependency → declare `env_prereqs` with a fast-fail probe
  (§10), don't just note "needs human to check".

Insert a human only when the gap is genuinely un-closeable by automation
(physical device, GUI-only verification, external service with no API).

### The limit: autonomy must stay gated

Autonomy is not reckless. The safety boundary is that autonomous dispatch
must always be gated — the scheduler applies `active`/`queued` +
`supervised_only` filters, and `local_checks` (when `-RunLocalChecks` is
on) prove correctness before shipping. Never:

- Auto-run a batch with no `local_checks` and no verification tier.
- Self-modify mid-flight (a batch editing `scheduler_scan.py` while the
  scheduler is running it).
- Remove gates without replacing them with an equivalent safety net.

See also §11 (shipped ≠ verified — gates must run) and §12 (verification
tier — what each level actually proves).

---

## 14. Planning device-manual work

Device-manual sub-plans (§12) live in a different economy from loop-verified
work: the loop produces correct *code* but cannot see *runtime* — isolate
boundaries, event ordering, cold-vs-warm, OEM quirks. The WeChatRelay
cold-start bug (30-line fix, ~6 device cycles to root-cause) was expensive
almost entirely because there were no logs at the decision points. These rules
make the plan buy down that root-causing cost up front.

### 14.1 Ship with observability (P7)

A device-manual sub-plan must declare, as an AC, **structured
logs/`debugPrint` at every decision point** the human verifier will need:
which config was read, which branch was taken, the connection target, and
success/failure with the error. A device-manual sub-plan whose only
diagnostics are "it works or it doesn't" is **under-specified** — the human
will spend entire device cycles guessing instead of reading a log line.

> Evidence: the WeChatRelay cold-start bug only cracked once a
> `debugPrint('connectWith: host=… token=…')` existed and surfaced
> `token=EMPTY`. Every cycle before that was blind.

### 14.2 Verify incrementally; don't stack device-manual sub-plans (P9)

After a batch containing device-manual sub-plans ships, **do the
human+device pass before planning the next batch that builds on it.**
Stacking unverified device work multiplies the debugging surface — two
WeChatRelay batches' bugs compounded because they were never verified on a
device until both had shipped. `/ilk-plan` should flag any batch that
contains more than one device-manual sub-plan, and recommend the
incremental-verify workflow.

See also §12 (verification tier — dependency rule: never queue a sub-plan
whose runtime correctness depends on an unverified device-manual sub-plan).

### 14.3 Budget the asymmetry (P10)

For device-manual work, the human cost is **root-causing, not coding**, and
each iteration is minutes (build+flash). Plans should:

1. **Front-load observability (14.1) and the runtime checklist (§5 / §9)**
   to cut the number of device iterations.
2. **Treat a device-manual sub-plan as a human debugging session**, not a
   "review the diff" — size batches accordingly. A 30-line fix that takes
   6 device cycles to root-cause is not "small" in the relevant sense.

### 14.4 Extract the pure core as a loop-verified gate

Even when the surrounding flow is device-manual, **extract the pure core
and ship it loop-verified.** A parser unit-tested loop-side, a config
builder with a real `pytest` gate, a protocol encoder/decoder — these are
all pure cores that can be verified without a device. The loop-verified
gate catches regressions in the pure logic while the human focuses device
cycles on the integration / lifecycle / platform-specific layer.

> Evidence: WeChatRelay's `parseProvisionUri` shipped loop-verified with a
> real unit test while the scan flow around it was device-manual. The split
> held perfectly — server work was flawless (58 pytest), device work needed
> the human pass.

### Cross-references

- **§12 (Verification tier)** — defines `device-manual` and the dependency
  rule that prevents stacking unverified work.
- **§5 / §9** — existing P5 runtime failure-mode checklist; sub-plan #2
  fleshes it out with the concrete 6-item content from the cold-start case
  study.

---

## 15. Autonomy tiers

Every change the loop can make falls into one of three autonomy tiers.
The tier determines who approves, how the master plan is configured, and
whether the scheduler can dispatch it.

### The three tiers

- **Tier 1 — agent-auto-apply.** Low-risk additive changes: docs,
  heuristics, QC lints, fixture additions, test scaffolding. The agent
  plans and applies without human gate; the loop ships on `local_checks`
  pass alone. No `supervised_only`, no `draft` hold. Trust comes from the
  change being additive (new docs/QC can't break existing behavior) and
  from `verification_tier: loop-verified` gates catching regressions.

- **Tier 2 — agent-plans-human-approve.** Behavior changes to loop
  infrastructure: runner, launcher, scheduler, feedback, adapter. The
  agent plans, the master is `draft` + `supervised_only`, and the human
  reviews each sub-plan before the loop runs it. This is the default for
  any sub-plan whose `scope_paths` modifies runtime behavior.

- **Tier 3 — human-only.** Safety-model changes, contested design
  decisions, external-facing API contracts. The human drives; the agent
  assists on request. No autonomous dispatch.

### How to assign a tier

1. Walk the sub-plan's `scope_paths`. If any file is loop infrastructure
   (`loop_status.py`, `scheduler_scan.py`, `promote_next_master.py`,
   `plan_status.py`, `scheduler.*`, runner scripts, launcher scripts,
   feedback scripts, adapter commands) and the change modifies runtime
   behavior → **Tier 2**.
2. If the change is purely additive (new docs, new QC lint, new fixture,
   new test file) and touches no runtime path → **Tier 1**.
3. If the change affects safety model, external API contract, or is
   contested → **Tier 3**.

When a batch mixes tiers, the master plan's "Rollout strategy" section
must list each sub-plan's tier. The highest tier in the batch governs the
master's `supervised_only` setting (any Tier 2 sub-plan → master is
`supervised_only`).

### Relationship to verification tiers (§12)

Autonomy tier and verification tier are orthogonal:
- A Tier 1 sub-plan (auto-apply) can be `loop-verified` (docs gated by
  grep) or `compile-only` (heuristic only).
- A Tier 2 sub-plan (human-approve) is typically `loop-verified` with
  runtime `local_checks`, but may be `compile-only` if the change is
  infra config.

### Cross-references

- **§12 (Verification tier)** — what each verification level proves.
- **§13 (Bias toward autonomy)** — the general principle this tier
  system operationalizes.
- **§8 (local_checks anti-patterns)** — gates that even Tier 1
  auto-apply must pass.

---

## 16. Gate-scoping: prefer change-scoped over whole-project

A `local_check` that compiles or tests the **entire project** (`tsc`,
`mypy`, `cargo build`, `npm run build`, `bun run typecheck`, `./gradlew
assembleDebug`) is a blunt instrument. It catches regressions broadly,
but it also:

- **Inflates wall-clock** — a full compile when only one module changed
  wastes worker time.
- **Hides the real contract** — a sub-plan that ships a new CLI verb
  needs a runtime smoke on that verb, not a project-wide typecheck that
  would pass even if the verb's handler 500s.
- **Creates latent false-blockers** — if the project has pre-existing
  type errors on the base commit (common in early-stage repos), a
  whole-project gate fails on every sub-plan regardless of the change.

### The rule

1. **Prefer change-scoped gates.** Run the test suite for the module
   that changed (`pytest apps/orders/`, `bun test src/checkout/`), or
   a targeted smoke (`curl localhost:3000/api/new-endpoint`). A gate
   that only exercises the changed code is faster, more honest, and
   less fragile than a whole-project compile.

2. **If a whole-project gate is unavoidable**, the planner must:
   - Confirm the gate is **green on the BASE commit** (the commit the
     sub-plan branches from). If it fails on the base, it's a
     pre-existing failure, not a regression — running it as a
     `local_check` would false-block every step.
   - Confirm the gate is **green on the RUN platform** (not just the
     planner's dev box). A suite that passes on Linux may fail on
     Windows (e.g. POSIX-only file permission checks). If the loop
     runs on a different OS than where the planner verified, the gate
     will false-block every step.
   - Note in the sub-plan body that the whole-project gate was
     baseline-green on the run platform and the date of that check.
     `plan_lint.py` (`lint_wholesuite_gate_baseline`) warns when this
     note is missing.

3. **A sub-plan whose ONLY `local_check` is a whole-project compile
   command** (`tsc`, `mypy`, `cargo build`, `npm run build`, `bun run
   typecheck`) with no change-scoped runtime smoke is a warning signal.
   The QC lint in `/ilk-plan` §7a flags this — the planner should add a
   targeted smoke or document why one is impossible.

### Why not ban whole-project gates outright?

Some projects genuinely have no per-module test runner, or the
regression surface is cross-module (shared types, config files). In
those cases a whole-project compile is the honest gate — but the
planner must still verify baseline-green. The rule is "prefer
change-scoped; if whole-project, verify baseline", not "never
whole-project".

### Cross-references

- **§1 (Tight contracts)** — runtime smoke over compile smoke.
- **§8 (local_checks anti-patterns)** — compile-only as the only check.
- **§15 (Autonomy tiers)** — Tier 1 auto-apply still needs gates.

---

## 17. Degrade-to-default over block (headless autonomy)

On a headless autonomous loop, `status: blocked` is not a clean outcome — it
is a **stall that requires a human**, the opposite of autonomy (§13). When a
sub-plan depends on a capability that may be absent (a design source, an
optional MCP, an external service) **but a safe default exists**, the guard
must *take the default*, not block.

**Field evidence (uccargo, 2026-06-13 — two stalls).** `/announcements`,
`/privacy`, and `tickets` were authored to "block cleanly" if Figma was
unavailable. The worker's Figma MCP was absent (a separate config bug), so the
sub-plans set `status: blocked` and the loop stalled with zero progress —
across two runs — even though these are simple pages that follow the existing
`help/terms` / `notifications`-`orders` pattern. The right behavior was to
**build to that pattern** when no design is fetchable; the agent itself
proposed exactly that, but the hard guard pre-empted it.

### Rules

1. **Prefer degrade-to-default.** If `capability X` is absent and a documented
   safe default/pattern exists, implement to the default + record a Findings
   note ("built to the help/terms pattern; no Figma frame found"). Proceed.
2. **Reserve `status: blocked` for genuinely un-closeable gaps** — no safe
   default, no API, a credential only a human can issue. Blocking because "we
   haven't verified it" or "the design is uncertain" is an anti-pattern; that
   is what degrade-to-default and verification tiers (§12) are for.
3. **A capability with a fallback must not be a hard `env_prereq`.** A hard
   `env_prereqs: claude mcp list | grep -q X` fast-fails to `blocked` *before*
   the step-0 fallback can run — the gate and the fallback contradict. Encode
   the optionality in step logic instead. The `/ilk-plan` step-7g
   `plan_lint.py` check flags this contradiction mechanically.
4. **A headless sub-plan must never `AskUserQuestion`.** A headless loop cannot
   answer it — the call burns idle iterations and stalls (uccargo's first stall
   fell back to a question). The AC-GUARD must pick the safe default or, only
   for a truly un-closeable gap, set `blocked` with a specific Findings note.

### Cross-references

- **§10 (env_prereqs)** — hard reachability gates are for dependencies with NO
  fallback; a degradable capability does not belong there.
- **§13 (Bias toward autonomy)** — remove human bottlenecks; fix the gap with a
  default, don't insert a stall.

---

## 18. Escaped-bug → regression gate

When a human finds a bug that a gate *should* have caught — a test gap, a
missing smoke, an unhandled edge case — the fix must **close the gate** so
the same class of bug cannot escape twice. This is the "escaped bug"
pattern: a `kind=escaped-bug` entry in the improvement tracker
(`improvement_backlog.py`) whose fix sub-plan carries a reproducing
`local_check`.

### The contract

1. **Declare the signal.** The fix sub-plan sets `regression_for:
   <escaped-bug-tracker-id>` in its frontmatter. This field tells the
   planner and the linter that this sub-plan fixes a human-found bug.
2. **Add a reproducing `local_check`.** The sub-plan must declare at least
   one `local_check` — either in frontmatter `local_checks:` or in a
   per-step `local_checks:` yaml block — that exercises the code path the
   bug exposed. The linter cannot verify the check truly *reproduces* the
   bug, so the enforceable contract is structural presence: an escaped-bug
   fix with zero `local_checks` is a finding.
3. **`plan_lint.py` enforces it.** The `lint_escaped_bug_regression_gate`
   check in `plan_lint.py` fires automatically during `/ilk-plan` step 7g.
   A sub-plan whose `regression_for` is set but has no `local_check`
   yields a `WARN` finding.

### Why structural presence, not semantic verification

The linter reads only the sub-plan's frontmatter — it cannot reason about
whether a `local_check` command actually exercises the bug's code path.
That semantic verification is the human's job during the post-ship pass
(§11). The structural gate catches the obvious gap (no check at all)
without false-flagging legitimate checks that don't mention the bug id.

### Upstream tie-in

Escaped bugs are filed as `kind=escaped-bug` in the improvement tracker.
When the planner sources a fix from such an entry, it sets `regression_for`
on the resulting sub-plan. The tracker entry and the sub-plan are linked
but independent — the linter reads only the sub-plan's frontmatter and
never touches the tracker.

### Cross-references

- **§8 (local_checks anti-patterns)** — the reproducing check must still
  follow the anti-pattern rules (no `| head`, no compile-only, etc.).
- **§11 (shipped ≠ verified)** — the gate proves structural presence;
  semantic verification (does the check actually reproduce the bug?) is
  the human's post-ship pass.
- **§12 (Verification tier)** — an escaped-bug fix sub-plan should be
  `loop-verified` (the reproducing check is a runtime smoke).

---

## 19. Comprehension debt — the loop is leverage on work you understand

`shipped` ≠ verified (§11), and verified ≠ *understood*. As the loop ships more
code per human-hour, a second debt accrues alongside correctness risk:
**comprehension debt** — the gap between what the loop has merged and what the
human actually understands. Even when every gate is green, code piles up faster
than anyone reads it, and every un-reviewed merge widens the gap.

> External framing (Addy Osmani, "loop engineering", 2026): the loop reporting
> "done" is its *claim*, not proof; the most dangerous use is running the loop
> to **avoid** understanding rather than to **leverage** understanding you
> already have — that accelerates decline instead of compounding skill.

### The principle

The loop is a lever on work you understand, not a substitute for understanding
it. Two operational consequences for the planner:

1. **Keep a human read in the trust chain for non-trivial batches.** §11's
   "verify → push → cloud-re-run" already mandates a human pass; comprehension
   debt is why that pass must include *reading the diff*, not just confirming
   the gate is green. A batch whose every sub-plan is `loop-verified` can still
   accrue comprehension debt if the human only watches the `shipped` banner.

2. **Surface the debt as a diagnostic metric, don't just hope.** A proxy the
   `metrics.py` aggregator can compute: **un-reviewed-merge ratio** (commits
   shipped by the loop with no recorded human review / total shipped). It is the
   *complement* of the correctness KPIs — §11/§2 track whether shipped work is
   *right*; this tracks whether it is *understood*. The north star stays
   "human-touch ↓ AND escaped-bug ↓", but **comprehension-debt ↑ is a reverse
   alarm**: if human-touch falls fast while comprehension debt climbs, the loop
   is borrowing against the future, not getting better.

### Cross-references

- **§11 (shipped ≠ verified)** — correctness enforcement; this principle is its
  comprehension-side complement.
- **§13 (bias toward autonomy)** — autonomy removes human *bottlenecks*, not
  human *understanding*; the two are different and only the first should shrink.

---

## 20. Orchestration runs on the cheap/free tier, not the planner

Launching a loop, watching it, and re-running deterministic gates are **mechanical**
acts — they need no planner-tier model. Doing them from an expensive planner session
(e.g. driving `/ilk-run` + a Monitor babysit loop from Opus) pays planner rates for
clerical work; the *watching* is the worst offender, because every step/ship event
re-invokes the planner and each notification can drag the full sub-plan into context.
This is the operational edge of cost-effectiveness (the framework doc's decision #13).

### Rules

1. **Don't launch+watch from the planner.** Free launch paths exist: the **scheduler**
   (autonomous, for non-`supervised_only` masters) and the **tray "Start now"** button
   (a detached PowerShell process — zero model tokens). If a model session must launch,
   use a **cheap worker session**, not the planner.
2. **Don't babysit.** The watchdog already supervises (restart on whitelist / block on
   blacklist) and `collect.py` auto-emits the postmortem on stop. Check `status` once at
   the end; do not react to every event on a model.
3. **Deterministic verification needs no planner.** `loop-verified` gates already ran
   in-loop with `-RunLocalChecks`; re-running them is redundant. A cheap session can
   re-run them if independent confirmation is wanted. The strong/planner model as an
   independent checker only earns its cost on **un-gateable tiers** (`compile-only` /
   `device-manual`) — see §15 / the framework's cross-model-verify decision.
4. **Reserve the planner tier for planning + judgment**: decomposition, design calls,
   reading the diff for intent + comprehension debt (§19), and outward actions (push).

### Cross-references

- **§13 (bias toward autonomy)** / framework decision #13 + corollary #13a — this is #13
  applied to *which tier runs orchestration*.
- **§19 (comprehension debt)** — the human read this preserves is judgment, not clerical.
- **§15 (verification tier)** — when a checker model IS worth it (weak tiers only).

---

## 21. Never launch or relaunch the loop from a model session

A model session (Claude Code, Codex, etc.) should **never** relaunch the
ilk loop.  Relaunch is the **watchdog's** job — it classifies the exit
state, applies whitelist/blacklist policy, and decides whether a retry
is warranted.  A model session that relaunches bypasses all of that:
it can't see the blacklist, doesn't know the backoff window, and can't
distinguish "drained exit (success)" from "crash (needs restart)".

### The 2026-06-22 incident

A cheap-model worker hand-authored Monitor scripts, picked the **wrong
log** among three (timestamped launcher log / `.ilk-loop.log` JSONL /
per-iteration logs), and misread an **all-shipped fast-exit as a crash**
→ relaunched in a loop, spawning duplicate loops.  Root cause: the model
session had no canonical way to distinguish "exited because queue drained
(success)" from "died (restart)".

### Rules

1. **A model session launches the loop once and stops.** It does not
   watch, babysit, or relaunch.  Use the scheduler or tray "Start now"
   for autonomous runs.
2. **Use `ilk_watch.py` for state queries.** Any session that needs to
   know the loop's state (running / all-shipped / blocked / idle) must
   call `skills/ilk-launcher/scripts/ilk_watch.py --project <p>`.  This
   helper resolves the **correct** sentinel and log — it never guesses.
3. **The runner prints an explicit drained-exit signal.** On all-shipped
   exit, both `run_ilk_loop_claude.sh` and `.ps1` print:
   `[ilk] ALL SHIPPED — nothing to run. Do NOT relaunch.`
   Any monitoring tool must recognise this line as a terminal success
   signal, not a crash.  The canonical form is "Do NOT relaunch" (title
   case); matching should be case-insensitive (`do NOT relaunch` also
   appears in tool output).

### Cross-references

- **§20 (cheap orchestration)** — relaunching from a planner session is
  the most expensive way to get it wrong.
- **`ilk_watch.py`** — the canonical state-query helper (AC-1/AC-2/AC-3
  in the loop-watch-helper sub-plan).
