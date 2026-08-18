Run the verify → fix → release → deploy sequence for the active batch.

Use when the user says "/ilk-ship", "ship the batch", "release this
batch", or "deploy".

This is the **release gate** for an ilk-loop batch. It runs five
phases in order: Audit → Verify → Fix → Release → Deploy. Phase 0
is non-skippable — a batch whose own sub-plans are unproven must not
be released.

**`shipped` is commit-only and local.** It is not pushed, not
CI-verified, not deployed. `/ilk-ship` is the step that turns local
commits into a tagged release.

---

## 1. Resolve project context

```bash
python3 "<skill-root>/ilk-loop/scripts/ilk_paths.py" --start .
```

Extract `project_root` and `project_key`. If `project_root` is null,
tell the user to `cd` into a project root and STOP.

## 2. Phase 0 — Audit (hard stop)

**Every `shipped` sub-plan must be proven before release.**

```bash
# For each shipped sub-plan in the active master:
python3 "<skill-root>/ilk-loop/scripts/ship_audit.py" \
  --subplan "<path-to-subplan.md>"
```

Exit 0 = proven. Exit 1 = unproven.

**If ANY sub-plan is unproven:**

1. Print the list of unproven sub-plans with their reasons (missing
   steps, red gate).
2. **Refuse to advance to Phase 3.** Do not proceed.
3. Tell the user what is missing and suggest `/ilk` to fix it.

This is a hard stop, not a warning. A documented hard stop that is not
asserted is the 2026-08-14 failure with better prose — 6 of 9 sub-plans
reached `shipped` without a passing final gate, and the tag was cut
anyway.

## 3. Phase 1 — Verify

**Select the gate tier and run it.**

### 3a. Load ship config

```bash
python3 "<skill-root>/skills/ilk-ship/scripts/ship_config.py" \
  --validate --project "$PROJECT_ROOT"
```

If no `ship:` block exists, degrade to the documented default (see
`skills/ilk-ship/SKILL.md` → "Missing ship: block"). The default
never hard-blocks — it says which default it used.

### 3b. Select tier

Read the changed paths from the active batch and call
`gate_scope.select_tier(changed_paths, consumer_result,
contract_governed_set)`.

The tier table:

| Tier | Trigger | Gate scope |
|---|---|---|
| 0 | docs/changelog only, no code | content assertions, no suite |
| 1 | zero resolved consumers | that module's own tests |
| 2 | N resolved consumers | tests covering those consumers |
| 3 | contract-governed file OR path/schema OR oracle failed | whole suite |

### 3c. Run the gate

Run the selected gate scope. Apply both floors:

1. **Collection** (`--collect-only`) — catches the class that voids
   every other result. Runs at whatever scope was selected, including
   tier 0.
2. **Baseline-compare** — node-id diff against the last tag via
   `baseline_diff.run_baseline_diff`. Comparison by node id, not count.

Subtract already-run commands from the complement (but floors are
never subtracted).

### 3d. Evaluate

If Phase 1 attributes regressions to this change → enter Phase 2.

If zero attributed regressions → proceed to Phase 3.

## 4. Phase 2 — Fix

Fix **only** what Phase 1 attributed. Fixing inherited failures inside
a release is how a release grows unbounded.

After fixing → loop back to Phase 1 for re-verification.

## 5. Phase 3 — Release

1. Write a CHANGELOG row.
2. Create the tag (but do not push — pushing is an explicit operator
   step).

## 6. Phase 4 — Deploy

For each host in the `ship:` block's `hosts` list:

1. Run `install.sh --apply` on the host.
2. Run the post-deploy hook (if configured).

Report per host. An unreachable host is `unreachable`, not `ok` —
never report success for a host you did not reach.

### Full-suite escape

The `ILK_ALLOW_FULL_SUITE=1` environment variable is the sanctioned
way to run a whole suite past the `no-full-suite.sh` hook
(`no-full-suite.sh:75-76`). Use it when a deliberate full-suite gate
is needed. An undocumented escape hatch gets rediscovered by
improvisation.

## Summary

After all phases complete, print:

```
ilk-ship complete:
  Batch:    <master plan slug>
  Phases:   0:audit 1:verify 2:fix 3:release 4:deploy
  Tag:      <tag>
  Hosts:    <per-host status>
  Proof:    <number> sub-plans audited, all proven
```

If Phase 4 had any unreachable hosts, list them explicitly.
