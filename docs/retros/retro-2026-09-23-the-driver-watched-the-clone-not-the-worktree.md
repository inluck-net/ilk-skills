# Retro 2026-09-23 — the driver watched the clone, not the worktree

Companion to gh-resolve's `docs/design/retrospectives/2026-09-23c-the-first-smooth-run-and-what-it-took.md`
(gh-resolve `60c4699`), which lists gaps I1-I7 as ilk-skills'. This is the
ilk-skills half. All run evidence was read on rezmac on 2026-09-23 (CST) from
`~/.ilk-data/projects/users-chad-projects-keyreply-kira-cloudflare-resolver-857a9e9/`,
except I6, which is on chad-mbp. Code citations are at ilk-skills `57b654b`.
rezmac runs `d4878f8`; the three commits in between are docs and registry only.

**Headline.** #6392's fix was correct and its gates were green, in the tree
where the work was done. The driver never looked at that tree. It captured
heads, scanned trailers, wrote the ledger and ran the gate against the
resolver **clone** (`dev` @ `50bc6c2`, a WIP commit from 2026-09-22 that does
not contain `d425c4971`). The work had been committed to a sibling
**worktree**. The gate then ran a ~9-minute suite under the 120s default and
timed out. The audit reported that as "gate record unreadable" and parked the
one master that had nothing to do with it.

Two of the reported gaps need correcting before they get planned:

- **I1 is not what fired today.** The violation text is
  `gate record unreadable (no results field, all_passed=false)`. That is the
  gate arm, not the step-commit and trailer arm. gh-resolve #28 is real, but
  both it and today's park come from one root: the driver observes the wrong
  tree (R1 below). Trailer stripping is only why the fallback path was taken.
- **I3 was two runs, not one audit.** At 15:27:04 the audit reverted
  `shipped → in-progress`. At 15:35 quarantine moved it to `in-progress → blocked`,
  in a *second* run (`20260923-153146`) that re-dispatched #6392's master. That
  second run was possible only because the park had landed on #6396 (R4).

## What happened (rezmac, CST)

| time | event | source |
|---|---|---|
| 15:06:25 | run `20260923-150625` starts. `Project: …/kira-cloudflare-resolver`, `Repos found: 1` (the clone). heads-before `50bc6c2` | launcher log :4-7, `heads-before-1.tmp` |
| 15:08:13-15:10:37 | worker runs both gates **in the worktree** `resolver-6392-b7f176ef`: "All gates green" | launcher log :147-170 |
| 15:10:47-15:11:05 | commits `d425c4971` (fix) + pointer bump **in the worktree**, marks work sub-plan `shipped` | :171-205 |
| 15:11:21-15:20:26 | worker's batch-verification suite in the worktree: 1416 files pass, **~9 min wall** | :210-252 |
| 15:21:29 | iteration ends `success`, 903s, **`new commits: 0`** (heads-after is still `50bc6c2`) | :353-355, `heads-after-1.tmp` |
| 15:21-15:27 | no trailers and no ledger rows, so the driver falls back to step 0 of the active sub-plan and runs `bun run test:non-ui:convex` **in the clone**: `timeout after 120s`. B2 confirm re-run: same | :356-359, `.ilk-loop.log` iteration-1 `local_checks[0].error` |
| 15:27:04 | `[ship-integrity VIOLATION] … gate record unreadable`; work sub-plan reverted to in-progress, `current_step 1 -> 0`; **`MASTER-issue-6396` parked** `queued → blocked`; reconcile flips `MASTER-issue-6392` back to `queued` | :360-371 |
| 15:31:46 | run `20260923-153146` dispatches **`MASTER-issue-6392`**, the master that owned the violation | 153146 log :33-38 |
| 15:35:22 | 214s, `new commits: 0`; same gate, same timeout; `auto-quarantined after 2 failures` → **`blocked`**; `Loop ended: blocked-no-runnable` | 153146 log :192-203 |
| 15:39:36 | reap opens PR #6405 from the worktree branch (gh-resolve side) | gh-resolve retro |
| 15:41:30 | `park_master.py --unpark` on #6396 by hand; run `20260923-154201` at 15:42:02 | gh-resolve retro; `last-exit.json` |

State now: `MASTER-issue-6392` `queued`, its work sub-plan `blocked` 1/1, its
batch-verification `shipped` (`unproven`), PR open. Plan state and published
state disagree, and nothing in ilk-skills will reconcile them.

## Root causes

### R1 — every evidence channel points at `--project-path`, and the work is elsewhere (was I1)

gh-resolve launches the loop with `--project-path` set to the resolver clone,
and the sub-plan tells the worker to work in
`…/kira-cloudflare-resolver-worktrees/resolver-6392-b7f176ef`. The driver has
only one notion of "the repo", which is `REPOS` derived from `--project-path`:

- heads diff → `total_new=0`, so `write_ship_proof_records` is never called
  (`run_ilk_loop_claude.sh:3441`, guarded `total_new -gt 0`). Its commit range
  also iterates `REPOS` only (`:1284-1288`). **0 ledger files** exist under the
  key's `runtime/`: 3 entries (`launcher`, `steer`, `watchdog`), no `ship-proof.jsonl`.
- trailer scan → nothing, so the gate target comes from the fallback (`:3519-3534`).
- gate cwd → `invoke_local_checks "$PROJECT_PATH" …` (`:3548`, and the B2
  re-run at `:3579`).

This is also gh-resolve #28's root. #28 reads it as "the audit converts no
evidence channel into a violation". That is true. But the channel is missing
because the driver looks in a tree the work never touches, not because the
trailer policy strips trailers. The ledger *is* the designed channel for
trailerless shared remotes, and it cannot be written for a worktree the driver
does not watch.

The driver already has a seam of exactly this shape for selfmod:
`selfmod_effective_repo` (`:3508`) maps a repo to its worktree for the trailer
scan. It is selfmod-only and covers one of the four channels.

### R2 — a ~9-minute gate under a 120s default

The work sub-plan's first `local_checks` entry is `bun run test:non-ui:convex`
with **no `timeout:`**. The second declares `timeout: 600`. `run_one` defaults
to 120s (`run_local_checks.py:581,583`). The worker's own run of the same suite
took 15:11:21 → 15:20:26. **In the right tree, this gate would still have failed.**
The plan was generated by gh-resolve, and the per-step gate re-runs the whole
convex suite that the batch-verification sub-plan runs anyway.

### R3 — the violation reason hides the actual failure

`ship_integrity.py:537-538` turns `--gate-passed false` into
`{"all_passed": False}` with no `results`. `evaluate_ship` then reaches its
"unreadable" branch, so the log said `gate record unreadable (no results
field…)`. The gate record in `.ilk-loop.log` had said
`error: timeout after 120s` six minutes earlier. The scalar hand-off exists to
avoid shell quote-mangling (`:517`), and it discards the one field that would
have made this a one-glance diagnosis.

### R4 — the park goes to "the single parkable master", not the violating one (was I2)

The driver calls `park_master.py --plans-dir … --reason …` with **no `--master`**
(`run_ilk_loop_claude.sh:3866-3868`), and with `2>/dev/null || true`.
`park_master.py` then picks "the single parkable one", meaning status
`queued|active` (`park_master.py:47,145-148,161-168`). At 15:27:04 #6392's own
master was not parkable. The reconcile that flips it back to `queued` runs
*after* the park (`:3869-3882`). So the only parkable master was #6396's, and
it was parked. Had both been parkable, `park_master` would have refused with
"several masters match", and the driver would have swallowed that refusal.
Either way the violating master stays dispatchable, which is what let run
`153146` repeat the failure. Separately, `_violating_slugs` is every slug in
this iteration's gate results (`:3857-3862`), not the violating ones. It is
the same set here, but by coincidence.

This is an ilk-skills bug for **any** plans dir holding more than one master.
It is not specific to gh-resolve's layout.

### R5 — `MalformedConfig` is the third arm nobody reads (was I4)

`load_ship_config` returns `ShipConfig | NotConfigured | MalformedConfig`
(`ship_config.py:61`). Of its 5 non-test callers:

- `ship_audit.py:336-338` and `batch_gate.py:727,742` test for `NotConfigured`
  only, then dereference `.ship`, which crashes (`AttributeError`). It is not
  in `ship_audit`'s `except (ImportError, FileNotFoundError, KeyError)`
  (`:341`).
- `plan_lint.py:4877` reads `not NotConfigured` as "suite declared", so a
  malformed config passes lint as configured.
- `doctor.py:833` and `run_local_checks.py:573` test for `ShipConfig`
  positively, so they are safe.

It is the family in memory `empty-answer-must-be-unconstructible-without-looking`,
a reader that turns input it cannot parse into a default.

### R6 — the verify step edits a config it never re-loads, and pass/fail per node id hides new offenders (was I5)

- **Schema.** `batch-verification-subplan.md:133-135` says to write a `node_id`.
  gh-resolve `23fa13e` wrote `file` + `name` instead, and no step afterwards
  loads the config. `ship_config.py:147-149` would have named the key
  immediately. The failure surfaced one stage later as R5's crash.
- **Masking.** The attribution table has one bit per node id: failed at base
  means not attributed (`:109-112`). `test_no_committed_identities` failed at
  base because of 1 offending file and at HEAD because of 5. It was recorded
  "failed at base, not attributed" (gh-resolve `cdf319d` message). Rule 1
  makes this wider on purpose: a *file-level* `node_id` with substring
  matching both ways (`:134-135`) exempts every test in that file, including
  tests the batch adds.

### R7 — pre-loop exits leave `state: running` behind (was I6)

The three iteration-0 exits (`all-shipped`, `shipped-unproven`,
`blocked-no-runnable`, `run_ilk_loop_claude.sh:3158-3190`) each `return 0`
without writing a terminal sentinel. The EXIT-trap fallback `finalize_sentinel`
bails at `:1396` (`[[ -z "${runtime_dir:-}" ]] && return 0`), because
`runtime_dir` is local to `main` and the trap fires after `main` returns. Its
own comment (`:1393-1395`) names "the quick all-shipped exit path" as the case
this guard exists for, so the fallback is a designed no-op for exactly the
paths that depend on it.

Measured on chad-mbp: **6 of 11** sentinels read `running` for a dead pid, and
all 6 are iteration-0 exits: 5 `shipped-unproven` (gh-resolve `20260923-122824`
pid 20381, gh-triage, ilk-pocket, todo-triage, kira-cloudflare) and 1
`already-shipped` (ilk-skills `20260923-122339`). On rezmac, 16 of 242 read
`running` and 15 of those pids are dead. I did not classify those 15.

### I7 — 180s macOS gate cap

Fixed in `f5f3d56` and present on rezmac (`d4878f8`). Not today's timeout:
today's was the 120s per-check default (R2), not the 180s cap.

## What held

- **The revert derived its pointer from the gate record**, as designed:
  `current_step 1 -> 0 (gate for step 0 was red)`. Its inputs were wrong. The
  mechanism was not.
- **B2 confirmation and quarantine** did what they are for, given what they
  were shown. Two timeouts meant two failures, which crossed the threshold.
- **`park_master.py --unpark`** restored #6396 in one call, and the scheduler
  picked it up in 35s. Design §9 row 4 ("no un-park tool exists") is out of
  date.

## Where the fixes land — the G6 question

gh-resolve asked for a choice: either ilk scopes the park and the revert to the
master that owns the violating slug, or gh-resolve moves to a per-run project
key.

**Choice: ilk-skills takes both halves, and gh-resolve keeps the shared
project key.** Basis:

1. R4 is an ilk-skills bug in any multi-master plans dir, so it gets fixed
   whatever gh-resolve does.
2. Scoping the park alone does not make #6392 pass. The gate would still run
   in the clone (R1) and still time out (R2). A per-run project key with
   `--project-path=<worktree>` would fix R1 as a side effect, but the read
   side has no seam to resolve a worktree's key (memory
   `plans-dir-keying-has-no-read-side-seam`), and rezmac still carries 16+
   `…resolver-worktrees-resolv…` keys from Aug 29-Sep 21 when that layout was
   in use.
3. Instead, ilk-skills generalises the selfmod seam: a master can declare its
   **work tree**, and heads, ledger, trailer scan and gate cwd all resolve
   through it.

This is wrong if gh-resolve needs per-run isolation for reasons other than
these gaps, for example concurrent resolver loops. One project key means one
run lock.

**gh-resolve's side:** write the worktree path into the master's frontmatter
(field name set by the plan), and declare a `timeout:` on every
`local_checks` entry it generates. R2 is the generator's gap. Better still,
drop the per-step full-suite gate, since batch-verification runs the same
suite.

## Fixes

Planned as an ilk-skills batch via `/ilk-plan`, one sub-plan each:

1. **The park goes to the master that owns the violating slug** (R4). Pass
   `--master`, collect only the violating slugs, refuse loudly when no owner
   resolves, and never swallow `park_master`'s stderr.
2. **A declared work tree for heads, ledger, trailer scan and gate cwd** (R1),
   generalising `selfmod_effective_repo`.
3. **The violation reason carries the gate's own error** (R3).
4. **`MalformedConfig` is reported, not crashed on or passed** (R5), in all 3
   of 5 callers that mishandle it.
5. **The verify step re-loads the config it edits, and "red at base,
   differently" is not "not attributed"** (R6).
6. **Pre-loop exits write a terminal sentinel** (R7).

Not planned: raising the 120s default. Judgment call: a default that fits a
9-minute suite would hide hung gates everywhere else. The fix is a declared
timeout (gh-resolve's side). This is wrong if a large share of generated
plans legitimately omit timeouts on broad suites.
