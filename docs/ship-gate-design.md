# The ship gate (2026-08-19, v0.9.66–v0.9.67)

Fresh-session orientation to `skills/ilk-ship/` — the release gate that turns a
batch of local `shipped` commits into a tagged, deployed release. Written after
the gate's first two real uses, both of which found something the design did not
anticipate. Figures here were measured on 2026-08-19; where a number is an
estimate it says so.

## The defect it exists for

`/resolver-ship` §1a rested on this premise:

> the loop only advances past a step when its `local_checks` pass — so a ship
> commit on a sub-plan whose gate is the whole suite **is** a passing whole-suite
> run

That premise was **false**, for two independent reasons.

**The gate fires after the advance.** `local_checks` run post-iteration
(`run_ilk_loop_claude.sh:1752`, only when `total_new > 0`), by which point the
agent has already committed and bumped `current_step`. A failing gate halts the
*loop*; it cannot un-advance the step. The agent owns `current_step` — the driver
only reads it (`loop_status.py:334`, `status_all.py:225`) — so nothing rolls it
back. A red-first step-0 gate therefore cannot block the step it guards.

**The correction was unreachable.** `test_ship_integrity()` carried three defects,
the first masking the other two:

1. `si_out=$(…) || true` then `si_exit=$?` reset the status to 0, so the violation
   branch never ran. Now `si_out=$(…) || si_exit=$?`
   (`run_ilk_loop_claude.sh:1233`), with the outcome passed as the scalar
   `--gate-passed {true,false,unknown}` rather than a hand-built `--gate-json`.
2. `grep -oP '^plan:'` — `-P` is GNU-only; BSD grep exits 2, the slug comes back
   empty, the gate result is lost. Now routed through Python.
3. `sed -i` without an explicit backup suffix — a no-op revert on BSD sed. Also
   Python now.

Consequence for anyone reading history: **`shipped` never meant "gated"**, and a
ship commit is not proof. Use `ship_audit.py`, which checks per-step commits.

## Architecture

Five phases (`commands/ilk-ship.md` is the operator-facing sequence,
`skills/ilk-ship/SKILL.md` the reference):

| Phase | Does | Engine |
|---|---|---|
| 0 Audit | every `shipped` sub-plan must be **proven** | `ilk-loop/scripts/ship_audit.py` |
| 1 Verify | select gate tier, run it, apply both floors | `gate_scope.py`, `baseline_diff.py` |
| 2 Fix | fix **only** what Phase 1 attributed | — |
| 3 Release | CHANGELOG row + annotated tag (no push) | — |
| 4 Deploy | `install.sh --apply` per declared host | `ship_config.py` |

Phase 0 is a hard stop by design, and it earned that on first use: it audited its
own batch at **3 proven / 4 unproven** and refused to advance. Four sub-plans had
reached `shipped` with no commit for their final step. They were re-opened, redone,
and re-audited at 7/7. The gate was not overridden.

Two **floors** are never subtracted by complement-subtraction:

1. **Collection** (`--collect-only`) — catches the class that voids every other
   result. Cheap; always run it.
2. **Baseline-compare** — node-id diff against the last tag, never by count.
   Keyed on **(tag, suite_invocation)**, so a scoped run will not compare against a
   whole-suite baseline; it returns `could_not_compare` instead of a misleading
   zero. Correct, but it means a scoped Phase 1 produces **no regression
   attribution at all**.

Baselines live in `<project_root>/.ilk-baselines/<tag>__<hash>.json`, and that
directory is **gitignored on purpose** — see below.

## What measurement showed, and what is still open

Replaying `select_tier` over v0.9.57..v0.9.67 with a *healthy* oracle:
**tier 3 in 8 of 10 releases; tier 0 and tier 2 never selected.** The ladder
behaves as a binary that says "run everything", because the dominant trigger is the
contract-governed set — and `collect.py`, `status_all.py`, `loop_status.py`,
`run_ilk_loop_claude.sh` are precisely the files toolkit batches touch.

**Fixed (v0.9.68).** The release process poisoned its own next gate decision.
`store_baseline` commits a `.json` under `.ilk-baselines/`; `.json` is a
path/schema extension; so last release's artifact forced the next release to tier 3
regardless of content — measured as tier 3 for a docs-only diff that was tier 0
without the artifact. Two changes: the directory is gitignored (a baseline is also
**host-specific** — the same v0.9.66 tag measured 746 passed/15 skipped on
chad-mbp and 745/16 on rezmac, so it was never shared state), and
`_is_path_or_schema_change` exempts `gate_scope.TOOL_ARTIFACT_DIRS`. Honest
qualification: this did **not** move the 8-of-10 figure, because those selections
were contract-governed, not artifact-driven.

**Open — tier 3 has no cost ceiling.** `TierDecision` carries `tier`, `reason`,
`consumer_count` and **no cost field**, despite the originating AC requiring
measured per-tier costs ("no estimated figures — the MASTER's 'seconds' estimate
was wrong by ~50x"). Nothing bounds the selected gate, so tier 3 here means 1846
tests with no budget and no cheaper substitute. When a whole-suite run is
disallowed, Phase 1 simply cannot complete: on the v0.9.67 release the tier-3 gate
was **not run**, and the release shipped on scoped evidence plus the collection
floor, stated plainly in the tag. A ceiling should compare the selected tier's
measured cost against a budget in the `ship:` block and **state** any downgrade —
a silent narrowing is the failure this skill exists to prevent.

**Open — triggers are whole-diff, not per-file.** One contract-governed file among
24 widens the gate for all 1846 tests. Per-file scoping is the change that would
move the 8-of-10 figure, but it interacts with the sentinel case behind AC-4 (a
path change at 2 call sites broke 12 fixtures across 7 files), so it needs care
rather than a quick narrowing.

**Open — the oracle's API shape invites a silent error.** `select_tier` takes a
*list* of changed paths; `resolve_consumers(module_name, project_root)` resolves
*one* module. Nothing says which module governs a multi-file diff, and passing the
list where a name is expected makes the oracle report `FAILED`, which degrades to
tier 3 — so the mistake looks exactly like a legitimate decision.

## How to continue in a fresh session

- Never read `shipped` as proof. Run `ship_audit.py --subplan <path>` per
  sub-plan; exit 0 = proven.
- `loop_status` prints `SHIP PROOF MISSING: N sub-plans shipped without proof` and
  names them. That line is the feature working, not a fault.
- Re-opening a sub-plan means editing frontmatter: `status: shipped` →
  `in-progress`, plus rolling `current_step` back past any phantom advance.
  Runnable statuses are exactly `{pending, in-progress}`
  (`plan_status.py:_RUNNABLE_SUBPLAN_STATUSES`). There is no un-ship helper.
- Re-opened sub-plans keep their earlier premature `#ship` commit; history is not
  rewritten, so `git log --grep=#ship` shows two. Never count ship-commit presence.

## See also

- `skills/ilk-ship/SKILL.md` — phase reference and the tier table.
- `commands/ilk-ship.md` — the operator sequence.
- `skills/ilk-loop/references/decomposition-principles.md` §8 — the gate
  anti-patterns, including the red-first step-0 rule this work produced.
- `docs/loop-runtime-hardening.md` — broader runtime hardening notes.
