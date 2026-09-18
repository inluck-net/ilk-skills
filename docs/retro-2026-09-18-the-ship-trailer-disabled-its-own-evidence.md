# Retrospective — the ship trailer disabled its own evidence, 2026-09-18

**Status:** open — fix batch grouping proposed (5 sub-plans incl. verification),
not yet authored. See §6.

**Project affected:** keyreply/kira-cloudflare (shared remote). Same defect
family measured on gh-resolve the same day (26 of 149 `last-exit.json` records
read `ship_integrity_violation`, 17 on or after 2026-09-16 — quoted at
`ship_integrity.py:308-313`).

## 1. What happened

Between 14:40 and 18:06 the scheduler dispatched kira-cloudflare **13 times;
every run was exactly one iteration**. Each iteration re-verified green gates,
re-shipped the same sub-plans, and had the ships reverted by the ship-integrity
audit. `chore(plans): pv3-flow-revert shipped` was committed **5 times**
(15:24, 16:09, 17:09, 17:14, 17:56). One iteration (17:47) ended on a question
— *"Want me to activate the pv3-p0 master and verify those?"* — in a headless
run, exit 0, no stop reason.

Cost: ~2.6 h of runner wall time across the 13 runs; per-iteration cost
observed for 2 of 13 runs ($0.62, $2.10). The work itself was real —
pv3-status-session, pv3-import-integrity, pv3-authz, pv3-dispatch all landed
between the churn.

Earlier the same day, a separate gate defect cost a productive run: at 12:41 a
`local_checks` command with nested quotes
(`bash -o pipefail -c "! grep -q 'toLowerCase() === .true.' …"`) died with
`syntax error near unexpected token '('` → `local_checks_failed` ended an
8-iteration run, followed by a 100-minute dispatch gap.

## 2. The mechanism — the loop's own ship act manufactured the evidence that invalidated the ship

All read this session:

1. The worker ships a sub-plan; the ship commit carries
   `[plan:<slug>#ship]`, written unconditionally by the ship path.
2. The ship-integrity audit runs minutes later.
   `_slug_has_any_trailer` (`ship_audit.py:99-111`) matches **any** trailer,
   including `#ship`, and its True verdict disables the ledger union for the
   whole slug (`ship_audit.py:270`).
3. On a shared remote the trailer policy strips `#step-N`, so the ship-proof
   ledger is the **only** per-step evidence. With the union disabled, the audit
   reports the early steps as missing — `missing commit for steps 0, 1, 2, 3`
   — reverts the sub-plan to `in-progress`, and the run ends
   `ship_integrity_violation` (`run_ilk_loop_claude.sh:3302-3305`).
4. The violation path reconciles the master back to `queued`
   (`run_ilk_loop_claude.sh:3306-3320`); the scheduler promotes and
   re-dispatches on its next poll. The steps' commits live in earlier runs, so
   no future run can ever satisfy the audit. **Permanent cycle.**

Experiment (this session, real kira data, 65 ledger records): blinding the
discriminator to `#ship` → pv3-authz missing `[0,1,2]→[]`, pv3-dispatch
`[0,1,2,3]→[]`. pv3-flow-revert stayed broken because it carries real
`#step-N` trailers for steps 4,5 — the per-**slug** all-or-nothing switch
cannot express "trust trailers where they exist, ledger where they don't".

### Regression lineage

| date | commit | change |
|---|---|---|
| 08-29 | `c1a208f` | ledger union born ("a step is proven by its ledger record when no trailer exists") |
| 09-08 14:25 | `24e997b` | per-slug trailer guard added (fixing a false-**pass**, batch 2026-09-08c: a ledger record had papered over a never-done step) |
| 09-16 | root-resolution fix (`ship_integrity.py:205-235`) | made the check actually fire instead of silently skipping ("18 skips vs 18 fires") — the false-positive wave starts here |

Both prior mass-damage incidents in this function are recorded in its own
comments: 69/150 sub-plans reverted 2026-08-20 (over-broad enforcement);
2026-09-08c false-pass. A third unreviewed same-day edit was deliberately
**not** hot-patched for this incident — the fix goes through a gated batch.

## 3. Why 13 runs happened before anyone noticed — the observability chain never saw the violation

- **D1 — the terminal stop reason never reaches the JSONL.** The iteration
  record is written (`run_ilk_loop_claude.sh:3189-3216`) **before** the
  ship-integrity block (`:3302-3305`) sets the reason, so `ship_integrity_violation`
  lands only in `last-exit.json` and stdout. Proof: run `20260918-175308`'s
  completion record (18:05:57, 3 commits, exit 0) has no `stop_reason` field
  while its sentinel reads `ship_integrity_violation`; the 12 earlier
  one-iteration runs likewise.
- **D2 — no postmortem for the run that needed one most.** Run `175308` (ended
  in violation at 18:06) has no postmortem — `postmortems/` tops out at
  `20260918-174757.md`. Every earlier stop got one. The gap's emitter mechanism
  is not yet established (first task of the fix).
- **D1+D2 consequence:** ilk-feedback classified the 17:47 run
  `shipped-unverified` with per-iteration stop reason "—". The
  postmortem→blacklist→backoff chain never engaged for violation runs at all.
- **D3 — headless worker ended an iteration on a question** (17:51:25, run
  `174757`, `iter-01.log`), exit 0, no classification. §17.4 forbids
  `AskUserQuestion` in headless sub-plans but nothing governs a text question
  at turn end, and a documented default existed (promote the queued master).
- **D4 — `plan_lint` does not syntax-check `local_checks` commands.** The 12:41
  quoting failure (§1) was authorable and lint-clean. A `bash -n` pass at plan
  time catches it for near-zero cost.

Minor observations, not filed as defects: nothing dedupes re-shipping an
already-shipped sub-plan (a 17:50 Edit failed with "old_string and new_string
are exactly the same" yet ship commits continued); the scheduler's
no-progress signature (`scheduler.sh:519-542`, a hash of plan
`status`+`current_step`) counted the *productive* 17:53 run as no-progress
because the audit reverted the states it hashes — correct per design, but the
cycle was erasing the scheduler's own progress signal too.

## 4. Recovery — the audit only runs inside the loop

kira needed **no toolkit fix** to land: the ship-integrity pass executes only
in the runner at iteration end, never on hand edits or pushes. Both masters
were parked (`queued → blocked` with `parked_reason`, `park_master.py`),
verified absent from `scheduler_scan`, and the batch finished **outside the
loop** by hand: gates re-confirmed targeted-only, the three sub-plans and both
masters set `shipped` in `~/.ilk-data` (leaving `parked_*` as the audit
trail), zero new repo commits, no history rewrite. Verified at session close:
all five plan files `shipped`; `feat/patient-verification-spreadsheet-app`
clean and in sync with origin. The 5 duplicate ship commits remain in history
deliberately — cleanup is the operator's call only. Human steps still owed
before merge: diff-read and the live voice-exchange check on a deployed
environment.

## 5. Fix table

| # | Change | Where | Closes |
|---|---|---|---|
| **F1** | `_slug_has_any_trailer` matches only `#step-N` — `#ship` is written unconditionally by the ship path and carries no step evidence | `ship_audit.py:99-111` | §2 |
| **F2** | Per-**record** ledger trust: a record attributes its `[step_from, step_to)` steps only when **no step in that range** carries a step-trailer — record-granular 09-08c guard; that record's range spanned trailer'd steps and stays rejected | `ship_audit.py:270-283` | §2 (flow-revert class) |
| **F3** | On `ship_integrity_violation` the runner parks the master (`blocked` + `parked_reason` naming run_id and violating slugs) instead of reconciling to `queued`; `.sh` + `.ps1` parity | `run_ilk_loop_claude.sh:3302-3320`, `.ps1` | §2 step 4 |
| **F4** | Emit a run-level final JSONL record after enforcement carrying the terminal `stop_reason` | `run_ilk_loop_claude.sh` record path | D1 |
| **F5** | Establish why run `175308` got no postmortem; make violation stops emit one | postmortem emitter (TBD) | D2 |
| **F6** | Prompt-body rule: a headless iteration never ends on a question — take the documented default and record a Findings note; plus a named stop classification for "awaiting operator input" | `/ilk` command body, stop classifier | D3 |
| **F7** | `plan_lint` runs `bash -n -c` over every declared `local_checks` command at plan time | `plan_lint.py` | D4 |

Proposed batch (`/ilk-plan` grouping approved-pending at session close):
`the-ship-trailer-is-not-step-evidence` (F1+F2), `a-violation-parks-the-batch`
(F3), `the-exit-record-names-its-cause` (F4+F5), `ship-evidence-verify`
(batch verification). F6/F7 route via `/ilk-self-improve`. Master held
`status: draft` (Tier 2); `supervised_only: false` per the mechanical scope
test (`plan_lint.py:4159-4166` — runner scripts are not in the infra set).
Run procedure: stop the scheduler first — it executes these very scripts.

ACs recorded: today's three kira audit shapes (fixtures captured in-repo, live
replay optional and never gating) audit clean; the 09-08c shape still flags;
the three real readers (ship_integrity CLI, ship_audit main, loop_status
verdict) agree; a violation run leaves the master `blocked`; a clean ship
parks nothing.

## 6. Follow-ups

- [ ] Author the fix batch (grouping approved-pending; files not yet written)
- [ ] F6, F7 filed to `/ilk-self-improve` backlog
- [ ] Post-toolkit-fix: gh-resolve's historical 26/149 violation records
      self-resolve on next audit — spot-check one
- [ ] Operator decisions outstanding: history cleanup of the 5 duplicate ship
      commits (declined for now); kira diff-read + live voice check before
      PR #5611 re-review

## 7. What was left in a good state

- kira-cloudflare: all five pv3 plan files `shipped`; branch clean, in sync
  with origin; loop parked-as-shipped; nothing dispatchable
- Scheduler left running (PID 32257) — nothing runnable for kira; ilk-skills
  has no active master
- No ilk-skills code was modified this session; this retro is the only repo
  artifact
