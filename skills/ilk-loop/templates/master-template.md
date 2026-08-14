---
title: <human-readable plan title>
slug: <short-slug>
created: 2026-MM-DDTHH:MM:SS+08:00     # ISO 8601 with TZ + seconds — authoritative sort key
status: queued                          # draft | queued | active | shipped (draft = authored, not yet released; non-runnable)
priority: null                          # optional integer; lower = jumps queue
pause_after_ship: false                 # if true, watchdog stops after this MASTER ships
supervised_only: false                  # KEEP false unless scope_paths MODIFIES loop infra (loop_status.py / scheduler_scan.py / promote_next_master.py / plan_status.py / scheduler.*) — effectively ilk-skills only; in a consumer project leave false unless the user explicitly asks. If true: scheduler + promote never dispatch it, and ilk-runner preflight hard-stops even a manual /ilk-run while a scheduler is alive. NOT a readiness/risk/"needs review" gate — that is `status: draft`. See decomposition-principles.md §13; enforced by `plan_lint.py --master`.
base_branch: main                       # ref that scope_paths are validated against (declared, not derived — see decomposition-principles.md §22). Distinct from branch: which is the child-branch policy.
branch: null                            # optional child-branch policy:
#   create_from: HEAD                   #   base ref to fork from
#   name: spike/<slug>                  #   target branch name
#   merge_back: false                   #   auto-merge to base on ship (default false)
goal: <one-sentence summary>
out_of_scope:
  - <explicit non-goal>
cross_cutting_invariants: []            # see decomposition-principles.md §7
# Example invariants:
# cross_cutting_invariants:
#   - id: no-secret-in-logs
#     description: generated logs/output must not contain credential values
#     applies_when: sub-plan body mentions `authToken` OR `apiKey` OR `secret`
#     assert: |
#       ! grep -E '(authToken|apiKey|password|secret)[":= ]+[A-Za-z0-9_-]{20,}' /tmp/last-smoke.log
---

# MASTER plan: <human-readable plan title>

> Before launching any sub-plan, run the project's `check-prereqs`
> script to verify environment readiness.
> See `~/.ilk-data/projects/<project-key>/PREREQUISITES.md` for
> what it checks.

## Goal

<one-sentence summary>

## Workstream map

<ascii box diagram or grouped list — optional but useful for batches
that span more than 3 sub-plans>

## Sub-plan registry

| # | Order | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|---|
| 1 | 1 | [YYYY-MM-DD-<slug-1>.md](./YYYY-MM-DD-<slug-1>.md) | <list> | <N> | pending |
| 2 | 2 | [YYYY-MM-DD-<slug-2>.md](./YYYY-MM-DD-<slug-2>.md) | <list> | <N> | pending |

<!--
META PROJECTS ONLY — delete this block in single-repo projects.

For meta projects (parent dir has .ilk-meta.json), add a `Repo` column
mapping each sub-plan to exactly one member repo. The value must match
a `name` in .ilk-meta.json; the loop driver routes that sub-plan's
commits/CI/ship to the named member.

| # | Order | Slug | Repo | Items | Steps (est.) | Status |
|---|---|---|---|---|---|---|
| 1 | 1 | [YYYY-MM-DD-<slug-1>.md](./YYYY-MM-DD-<slug-1>.md) | <member-name> | <list> | <N> | pending |
| 2 | 2 | [YYYY-MM-DD-<slug-2>.md](./YYYY-MM-DD-<slug-2>.md) | <member-name> | <list> | <N> | pending |
-->

## Repos in scope (META PROJECTS ONLY)

<!--
For meta projects: list which member repos this batch touches, with a
one-line rationale per repo. Helps reviewers understand cross-repo
coupling before reading individual sub-plans. Single-repo projects:
delete this section.

| Repo | Why it's in this batch |
|---|---|
| <member-1> | <one-line reason> |
| <member-2> | <one-line reason> |
-->


## Execution rationale

Why this order. Reference [decomposition-principles.md](../skills/ilk-loop/references/decomposition-principles.md)
when justifying group boundaries (principle 4).

## Cross-workstream dependencies

If group N+1 needs something group N produced (data, code, deploys),
say so explicitly. The loop does not infer dependencies.

## Out of scope

Anything explicitly excluded from this batch. Lock it down to prevent
loop scope creep.

## Cross-cutting rules (apply to every sub-plan)

Commit attribution policy, NDA/privacy discipline, branch policy,
verb naming — derived from project conventions. Prose rules; the
loop driver does NOT enforce these.

## Cross-cutting invariants (mechanically asserted)

If `cross_cutting_invariants:` in frontmatter is non-empty, the plan
generator wove each invariant's `assert` block into every sub-plan
whose body matches the `applies_when` predicate. Invariants are
constraints, not guidance — they ride in `local_checks` and run
mechanically.

If you're reading this in a partially-complete plan and want to
verify coverage:
- Check that each invariant has at least one matching sub-plan
- Check that each matching sub-plan's `local_checks` includes the
  invariant's `assert` line

## Rollout strategy

- **Supervised mode** — first N sub-plans (typically 2-3): the user
  reviews each completion before the loop advances. Validates the
  contract design before trusting it autonomously.
- **Autonomous mode** — subsequent sub-plans: loop advances on its own
  as long as `local_checks` pass. The user intervenes only on
  classified failure (see ilk-feedback whitelist/blacklist).

Re-enter supervised mode whenever a sub-plan touches a new external
system or fails its `local_checks`.

- **Batch by verification tier** (see decomposition-principles.md §12):
  group `loop-verified` sub-plans into autonomous runs (the loop's
  runtime gate proves correctness). Group `compile-only` / `device-manual`
  sub-plans into supervised / human-paired runs — the loop can ship
  the code but cannot confirm it works, so a human + device pass is
  required before trusting the result.

## Final success criteria (manual / out-of-band)

> **`shipped` ≠ verified** (see decomposition-principles.md §11). A
> sub-plan marked `shipped` by the loop is **commit-only and local** —
> it does not mean pushed, CI-green, or verified in the cloud. And the
> sub-plan `local_checks` only ran if the loop was launched with
> `-RunLocalChecks`; without that flag the loop advanced on the worker's
> self-report. Before trusting this batch, a human must:

- [ ] Confirm the loop ran with `-RunLocalChecks` (else re-run the gates manually)
- [ ] Verify the changed behaviour (run the e2e / smoke against a live env)
- [ ] Push the commits
- [ ] CI passes (cloud re-run, green) on integrated branch
- [ ] <feature-specific gate>
- [ ] <human review checkpoint>

## Progress log

| Date | Action | By |
|---|---|---|
| YYYY-MM-DD | created | /ilk-plan |
