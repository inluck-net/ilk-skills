# Retrospective — a hard stop disguised as a timeout, 2026-09-20

**Status:** open — observations recorded; the batch's final step was
completed by an operator-driven session improvising around the machinery.
Improvement candidates (G1-G4) deferred to operator prioritization; the
operator has separately asked for the verification-sub-plan template to
carry the time savings (item 2 of 2026-09-20 17:3x).

**Project affected:** inluck-net/ilk-skills, batch
`selfmod-merge-visibility` (MASTER-2026-09-20), sub-plan
`selfmod-merge-visibility-verify` (SP4, batch verification). Every claim
below was verified against run logs, records, and
`verification_record.py` in the session that observed it.

## 1. What happened

SP4's step 0 ran five times between 15:01 and 17:30 and wrote the same
stub every time — `suite_failed: unmeasured`, `## At-base rerun
_(suite did not finish)_` — while manual `pytest` runs from the same
sessions completed in ~3.5 min (HEAD, 94 failed) and ~6 min (base, 93
failed). Four headless loop iterations and one operator cold-start
session burned 30-45 min each re-deriving the same wall.

| Attempt | Ended | What it did |
|---|---|---|
| run 150122 (headless, mimo) | 15:31 `local_checks_failed` | ran both suites manually; died at the 30-min iteration bound before finalizing |
| run 155640 (headless, mimo) | 16:26 `local_checks_failed` | ran suite + built `/tmp/ilk-base-verify`; died mid-record |
| planner-patched retry | 15:56 onward | base-sha recipe fixed (was resolving to the `#ship` marker — inside-batch base); suite-timeout 900→1800 |
| operator cold session | 45+ min | measured both trees, improvised `baseline_red` surgery (17:18), re-invoked the tool — stub again at 17:26 |

## 2. The mechanism (all cites read in-session)

1. **The base tree carries ~90 pre-existing reds** — Windows-only tests
   on macOS, Python 3.9 typing collection errors across
   `skills/ilk-loop/scripts` tests, Lark fixture gaps; families are
   documented in `.ilk-launch.json` `ship.baseline_red` `reason` fields.
   Invisible to daily work because every loop gate is change-scoped; the
   batch verification is the first full-scope run since the debt grew.
2. **`AT_BASE_CAP = 50` is a designed human-escalation stop**
   (`verification_record.py:479-483`): uncovered failing node ids above
   50 raise `ValueError("…a batch failing this widely needs a human, not
   an at-base rerun")`. The cap fired every attempt — correctly.
3. **The stop surfaces as the stub string** `_(suite did not finish)_`,
   byte-identical to a transient timeout, and the record keeps
   `suite_failed: unmeasured`. Nothing in the emitted record or the
   runner's stop reason distinguishes "designed hard stop, escalate"
   from "did not finish, retry" — so the loop retried, and the operator
   session diagnosed from scratch.
4. **Amplifiers**, measured: per-test `--timeout=60` with hang-style
   reds (the at-base manual pass took ~6 min for this reason); the
   at-base phase runs **one pytest process per node id**
   (`:466-472`'s declared-exemption design exists precisely to keep that
   set small — the coverage gap defeated it); the 30-minute headless
   iteration bound against a step that needs ~10 min of suite time plus
   attribution work.

## 3. What was right

- The cap refused to auto-process 90 failures and demanded a human —
  the anti-forgery spine (measurement, not narrative) held everywhere:
  the stub honestly reported `unmeasured` instead of inventing zero.
- The matcher is not the bug: `_in_baseline_red` (:577-589) matches
  substring-both-ways, so file-level declarations cover their tests.
  The gap is coverage, not shape.

## 4. Improvement candidates (deferred)

- **G1 — name hard stops in-band.** A `ValueError` designed as
  "needs a human" should reach the record and the sentinel as its own
  classification (e.g. `at-base-cap-exceeded`), never as
  `did not finish`. Same family as F6 (2026-09-18 retro): headless
  iterations cannot act on a signal they cannot distinguish.
- **G2 — green the base.** ~90 pre-existing reds are the multiplier
  behind every verification cost today: the at-base pass, the
  `baseline_red` surgery, the cap itself. A dedicated batch (fix or
  platform-mark) repays itself on every future verification, both hosts.
- **G3 — size the per-test timeout to the suite.** `--timeout=60` is a
  hang-absorber; the `--durations` output exists to pick a real ceiling.
  Config change, measured before applied.
- **G4 — codify baseline_red growth in the template.** Every at-base
  failure that is NOT attributed should append to `baseline_red` as part
  of the step (with reason), so each batch's verification cheapens the
  next. The operator has asked for the template change; do it with G1-G3
  context, not alone.
