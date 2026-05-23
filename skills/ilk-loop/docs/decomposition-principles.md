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

Fix obvious gaps inline (add the path, resolve the ambiguity). Surface
non-obvious ones as "review before launching" notes for the human.

This is a heuristic — the plan generator already knows the context, so
it can't truly simulate cold. The empirical version (a real fresh
session per sub-plan in preflight) is a future runtime feature; for
now, this is the skill-side approximation that catches obvious misses.
