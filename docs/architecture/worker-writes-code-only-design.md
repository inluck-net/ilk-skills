# The worker writes code only: design note

**Status:** proposed 2026-10-10 by the RSI owner session (ilk-skills-6f).
It needs Chad's decision (section 7). It extends invariant I3 of
`loop-state-and-ownership-design.md` (accepted 2026-09-28) and does not
replace that document.

## 1. Why: where 30 days of defects came from

Chad asked whether ilk's design is wrong and whether a third-party
orchestrator should replace it. To answer, every incident filed from Sep 10 to
Oct 10 was classified by root cause:

- the **27 retros** in `docs/retros/` dated in that window, all of them,
  classified by hand;
- the **240 backlog rows** first seen in that window, out of 346 in
  `~/.ilk-data/ilk-skills-improvements/candidates.json`. Two agents
  classified them from each row's title and gap; a spot-check of 8 agreed
  with all 8.

| Root cause | Retros (27) | Rows filed by a person (115) | Auto-filed rows (125) |
|---|---|---|---|
| **Policing the worker**: ship proof, ledgers, verification records, red attribution, gate correctness, integrity reverts | **11** | **33** | **75** |
| Core loop: dispatch, timeouts, run lifecycle, state machine | 7 | 31 | 7 |
| The toolkit modifying itself (selfmod, live clone is the deploy) | 4 | 25 | 11 |
| Owning the tool: release train, deploy, two hosts | 2 | 17 | 6 |
| Model and provider | 3 | 5 | 0 |
| Consumer's own defects | 0 | 0 | 20 |
| Observability | 0 | 4 | 6 |

Caveats:
- Auto-filed rows (`local-checks-stuck`, repeated red gates) were classified
  by symptom, not by a traced cause. That is why they are kept in a separate
  column.
- 87 of the 115 person-filed rows came in the last week, after RSI owner
  sessions started filing. That shows filing intensity, not a rising defect
  rate.
- Classifier output: `class-a.json` / `class-b.json` in the session
  scratchpad. Not kept.

**Reading.** Policing the worker is the largest cause in every source. The
rows say why:
- "a sub-plan's `shipped` status survives a red gate";
- "a trailerless step commit passes its step gate, then ship_integrity
  refuses the sub-plan forever";
- "a worker edits and commits the live clone through the skill-root symlink";
- a worker forged and re-signed its own verification record (memory
  `worker-forged-verification-record`, 2026-09-30);
- workers shipped by editing frontmatter, with 0 `ship_transition` calls.

All five have one shape. **The worker can write the control plane, so every
control-plane fact needs a check that runs afterwards.**

## 2. What the worker writes today

Read 2026-10-10 at `main` 972c202c:

| The worker writes | Where it is required | What polices it |
|---|---|---|
| step commits carrying `[plan:<slug>#step-<N>]` | `commands/ilk.md:75-79`; `skills/ilk-loop/SKILL.md:201` | ship audit, ship-proof ledger, trailer checks |
| `current_step` +1, in its own commit | `commands/ilk.md:80-83`; `SKILL.md:211-214` | `PRE_ITER_ALL_STEPS` snapshot and rollback; the one-step pin `test_one_turn_one_step.py` |
| `last_updated` and `## Findings` in its sub-plan | `commands/ilk.md:84-90` | the `plan-amended` fingerprint; `local_checks` restore → `ship_integrity_violation` |
| anything else its tools reach | no rule | 5 PreToolUse hooks in `~/.claude-worker/settings.json`: `no-full-suite`, `no-live-clone-edit`, `no-foreign-plan-edit`, `no-duplicate-read`, `no-sealed-read` |

The driver already has a compare-and-set step advance,
`advance_subplan_current_step` (`run_ilk_loop_claude.sh:2577`). It runs only on
the `gate_first` fast path. For every other step, the pointer the worker
writes is the one that is gated and recorded (memory
`worker-step-bump-is-load-bearing`).

The accepted I3 says "workers never write status". It enforces that by
**diffing and reverting afterwards**. Its single transition API is not built
yet: 0 `def transition` in `skills/ilk-loop/scripts/*.py`. The checks that run
afterwards are where the policing rows come from.

## 3. Proposal

**The worker's only output is a change to the project's tracked files, plus a
findings note. The driver performs every control-plane write.**

| Today the worker | Proposed |
|---|---|
| commits each step with a trailer | leaves a working-tree diff. After the turn the **driver** commits it as `[plan:<slug>#step-<N>]`, so the trailer always exists and always names the dispatched step |
| bumps `current_step` | the **driver** advances it with the existing compare-and-set (`advance_subplan_current_step`), only after the gate is green, and by exactly one |
| writes `## Findings` in the sub-plan | writes `.ilk/findings.md` in the worktree, which is gitignored. The driver appends it to the sub-plan under `## Findings` |
| can write the plans dir, the data root and the clone | **cannot**. One PreToolUse hook denies every write outside the project worktree, and denies `git commit` / `git push`. It replaces `no-live-clone-edit` and `no-foreign-plan-edit` |
| ends the turn after one step | unchanged. The driver dispatches exactly one step, and that is the only step it commits |

What this removes structurally, rather than by a check:
- **trailerless or mis-trailered step commits**: the worker cannot commit;
- **pointer jumps** (0→3 with only step 0 gated): the worker cannot write the
  pointer;
- **frontmatter ships and forged records**: the plans dir and data root are
  outside the worker's write scope;
- **live-clone edits during selfmod**: the clone is outside the worktree.

What it does not remove: gate correctness, red attribution and verdict policy
(I5) still exist. A green gate on a bad change is still possible. Those are
the honest core of "prove the work", and they stay.

## 4. Costs and risks

- **Consumer commit hooks** (lefthook, pre-commit) now run under the driver,
  not the worker. A hook that fails must be reported back to the next turn as
  a gate result, not silently retried. kira-cloudflare runs lefthook; row
  `5f04121c` shows its post-checkout hook already acting on temp worktrees.
- **Meta projects**: a step's diff can span member repos. The driver commits
  in each member named by the sub-plan's `repo:` (`commands/ilk.md:76-79`).
- **gh-resolve contract**: gh-resolve reads step commits and trailers. Commits
  keep the same trailer, but the author changes. Ask gh-resolve before
  building, as with the toolkit snapshot (design §5).
- **A worker that needs several commits for one step** loses that. The step
  rule is already one commit per step (`SKILL.md` "Step granularity rules"),
  so this formalises it.
- **Worker prompts and templates change**: `commands/ilk.md`, `SKILL.md`, the
  verification-subplan template and `/ilk-plan` output.

## 5. Separately: freeze self-modification of the kernel and release path

The self-modification and tool-owning rows (25 + 17 of 115) come from batches
that change the code running them and releasing them. Today's release
deadlock is the latest: 10b's fix to the release path can only ship through
that same path.

**Proposal:** kernel-tier code and the release/deploy path change only through
owner releases Chad approves. RSI batches keep the non-kernel tier
(templates, autoplan sources, tools), which is where the 2026-10-10 track-B
and worker-gate-notice moves already point.

## 6. Order

Each step gets a test that fails without it, and runs on a consumer batch
before an ilk-skills batch.

1. **The write-scope hook**, in report-only mode for one batch on each host.
   This measures how often today's workers write outside the worktree,
   before anything is denied.
2. **The driver commits and advances.** The worker prompt stops asking for
   commits and bumps, and the hook denies `git commit`.
3. **Findings via `.ilk/findings.md`.** The hook then denies the plans dir and
   data root.
4. **Retire the checks made dead by 1-3**, one per sub-plan, each with
   evidence that its failure class no longer occurs: trailer audits,
   `PRE_ITER_ALL_STEPS` rollback, the `plan-amended` fingerprint for
   frontmatter, `no-live-clone-edit`, `no-foreign-plan-edit`.

**Falsifier.** After step 3, auto-filed `local-checks-stuck`, `repeated-revert`
and `ship_integrity_violation` rows should fall in a comparable 2-week window.
If they don't, the policing load comes from gate correctness, not from worker
writes, and section 3 was the wrong lever.

## 7. Decisions for Chad

1. Adopt "the worker writes code only" (sections 3-4)? It is kernel work, so
   it is an owner build, not an RSI batch.
2. Freeze self-modification of the kernel and release path (section 5)?
3. Do both before or after the remaining items of the 09-28 design (D2
   record, D3 transition API)? Recommendation: before D3. With the worker
   out of the control plane, D3's API has a single writer class, the
   driver's own components, and its "diff and revert" enforcement becomes a
   backstop rather than the main mechanism.

On replacing ilk: Orca already serves interactive multi-agent work here (this
session runs in it). It has no unattended scheduler, gated resume or ship
proof, and every unattended orchestrator needs the policing above. So the
measured problem is not one a tool switch removes.
