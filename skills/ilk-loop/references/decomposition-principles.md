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
