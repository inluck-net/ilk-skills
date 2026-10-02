# Unattended unblocking: getting a blocked loop to "done" without a human

**Status:** accepted direction, decisions D1-D4 (Chad, 2026-10-02). Written
2026-10-02 by ilk-skills-c2. Review copy with diagrams:
`~/Documents/keyreply/chad/20261002/unattended-unblocking-design.html`.

## Goal (Chad, 2026-10-02)

- Batches run **unattended as far as possible**, and a blocked loop gets
  **unblocked and driven to done**. Today that takes a human talking to an
  ilk-skills or gh-resolve session.
- When a loop blocks, the unblocking component **runs `/ilk-feedback`**
  (the postmortem) as its first step.
- It is **part of ilk-skills' recursive self-improvement (RSI)**. Every
  manual repair it makes is evidence for a toolkit fix, and repairs that
  recur get planned away.
- Standing rules still apply:
  - verification is smooth and fast AND unexcusable by its own batch
    (2026-09-24);
  - blockers are fixed at the root, no workarounds (2026-09-28).

## The problem, measured on one day

On 2026-10-02, two batches (I1 = MASTER-2026-10-02b and I2 = MASTER-2026-10-02c
on ilk-skills, G1b on gh-resolve) needed a human-driven session to get past
roughly 14 stops or near-stops. Each row is something a session did by
hand. Evidence is in `retros-pending/retro-2026-10-02-a-fix-that-shipped-inert.md`
and the sub-plans' Findings.

| # | What blocked or nearly blocked | What the human-driven session did | Kind |
|---|---|---|---|
| 1 | A worker shipped one sub-plan, took the verify in the same iteration, and wrongly marked it `blocked` (run 20261002-144216 iter 5, 26 min) | diagnosed it from `declared-gates-5.txt`; unblocked the verify | deterministic: the one-ship guard |
| 2 | The one-ship guard had shipped inert: `ILK_SHIPPED_MARKER` was set 0 times in the runner | re-opened sub-plan 7 with a step 2; checked it live | judgment: re-open |
| 3 | The I1 verify attributed 3 reds to its own batch | named owners and fixes in a Findings note | judgment: amend |
| 4 | The runner kept running code from before a merged fix | pause flag, stop by pid, relaunch | deterministic: re-exec at a boundary |
| 5 | A worker ran the full suite itself and overwrote `runtime/batch-gate.json` | stopped the worker; restored the record from the per-batch copy | deterministic: a refusal + a restore |
| 6 | Fixture revert rows leaked into the live `ship-reverts.jsonl` (4 cleanups, 104+12+3+3 rows) and sent workers on fake-revert detours | cleaned them by signature; planned the root fix | deterministic: root fix (I2 sub-plan 2) |
| 7 | The fake-revert fix was queued 8th | reordered I2 | judgment: reorder |
| 8 | A step-0 pin wrote to the live ledger, asserted the bug, and `rmtree`'d a key derived from cwd | wrote a rewrite spec into Findings | judgment: amend (plus a lint) |
| 9 | The red gate got the project blacklisted (`local-checks-stuck`) | `/ilk-resume` ack + relaunch | judgment: ack after a fix |
| 10 | The watchdog started before the new runner wrote its sentinel, read the old state, and exited | restarted the watchdog by hand | deterministic: start-order race |
| 11 | A red caused by sub-plan 1 (176b6e3) surfaced in sub-plan 2's gate; the driver quarantined sub-plan 2 | bisected on `git archive` copies; re-opened the owner; re-gated sub-plan 2 | deterministic: owner bisect (backlog 22) |
| 12 | `collect.py` labelled a `local_checks_failed` run `interrupted` (3 repros, 2 projects) | read the JSONL by hand | deterministic: I2 sub-plan 6 |
| 13 | A gh-resolve worker edited its own step-0 pins during step 1, with 44 min uncommitted | flagged it to gh-resolve-b4, who reviewed the diff and re-baselined | judgment: review (the guard did refuse it) |
| 14 | Release approvals, policy calls (backlog 19), cross-project priority | asked Chad | human |

**7 of 14 are deterministic.** A driver that knew the failure would have
handled it with no reasoning involved. 6 needed judgment, and 1 was
genuinely Chad's. (Corrected 2026-10-02: an earlier draft said ~9 / ~4 / 1,
which didn't match this table.)

So most of the human effort was compensating for driver defects. An agent
on top of those defects would be a workaround, which the standing rule
forbids. The design follows from that split.

## What exists, and why none of it does this

Read in this session:

- **watchdog** (`skills/ilk-watchdog/scripts/watchdog.sh`): polls one
  project's sentinel, takes a label from `collect.py`, and either relaunches
  (whitelist) or blocks with a banner and exits (blacklist). It never reads
  the evidence and never changes state. Correct blocks today still needed a
  human to diagnose, fix and relaunch. Its input is also unreliable (row 12).
- **scheduler** (`scheduler.sh` + `scheduler_scan.py`): the shipped V1 of
  `docs/future-work/cross-project-supervisor.md`. It decides *which project
  runs*, and skips blacklisted ones. It doesn't triage.
- **`collect.py` / `/ilk-feedback`**: classifies a run and writes a
  postmortem. It already emits `local-checks-stuck` candidates into
  `~/.ilk-data/ilk-skills-improvements/candidates.json`.
- **`supervisor_emit.py`** (`skills/ilk-feedback/references/supervisor-emit.md`,
  "decision #12"): the contract for supervisors to write backlog entries,
  not prose.
- **`/ilk-self-improve`**: reads open candidates and plans a toolkit batch
  through `/ilk-plan`, written as `draft`. A human releases it.

The pieces for the self-improvement half exist (backlog, emit contract,
planner adapter). What's missing is the actor between "the watchdog
blocked" and "the loop is running again".

## Design: three layers

### L1: the driver repairs mechanical failures itself

No agent, no judgment. Each item is a known failure shape with a
deterministic response. This layer should absorb the 7
deterministic rows, and each item is an ordinary planned sub-plan.

| Shape | Response | Status |
|---|---|---|
| A red found by sub-plan B's gate was caused by sub-plan A | bisect the batch's commits for the failing node (on `git archive` copies), re-open A with the node and commit in Findings, re-gate B with `gate_first` | backlog 22; not planned |
| A worker takes a second sub-plan after a ship | the hook denies Edit and commit once the marker exists | shipped (8dd093c), live since 16:58 |
| A worker runs the suite or writes the proof record | `batch_gate.py` refuses inside `ILK_WORKER_SESSION`, as `verification_record.py` does | not planned (asked 2026-10-02) |
| A worker edits pins outside step 0 | `pins_only_lose_xfail` already refuses at the gate (it caught row 13) | exists on gh-resolve; check ilk-skills |
| A test writes into the live data home | an empty `PROJECT_PATH` resolves nothing; ledger writers fail closed | I2 sub-plan 2, gate re-run pending |
| The runner's own script changed under it | re-exec at the next iteration boundary | not planned |
| A postmortem reads another run's sentinel | classify run X only from X's records | I2 sub-plan 6 |
| The watchdog starts before the sentinel is rewritten | start the watchdog after the runner writes `running`, or ignore a sentinel whose `run_id` is older than the launch | retro backlog row 9 |
| A test `rmtree`s a data dir it didn't create | lint refuses it | retro backlog row 10 |

### L2: a triage agent for what L1 can't classify

Working name: **`ilk-triage`**. It is the role a human-driven session
played on 2026-10-02, with rails.

**Lifecycle, per block:**

1. **Trigger:** an event, not polling. Any terminal state other than a
   clean ship, or any sub-plan going `blocked`. The watchdog's blacklist
   branch hands off to triage instead of only exiting.
2. **`/ilk-feedback` first:** run `collect.py` for the run and read the
   postmortem. (It must classify from the run's own records, I2 sub-plan 6,
   or triage starts from a wrong label.)
3. **Evidence pack:** the failing node ids, the gate record, the iteration
   log tail, the worktree's `git status`, the sub-plan's Findings, and the
   live ledgers' recent rows. These are the reads a human-driven session
   did by hand today.
4. **Decide one action** from a fixed vocabulary:
   - `reopen <slug> <step> <finding>`: the owner of a red, or an inert
     fix (rows 2, 11);
   - `amend <slug> <finding>`: a precise spec for the next worker (rows 3, 8);
   - `reorder <slug> <position>`: dependency-correct only, lint-checked (row 7);
   - `regate <slug> <step>`: `gate_first` re-run, no code change (row 11);
   - `clean <ledger> <signature>`: only signatures already in a reviewed
     allow-list, with a backup (row 6);
   - `ack-and-relaunch`: only after one of the above changed state (row 9);
   - `park-and-escalate <reason>`: everything else.
5. **Apply, then check:** relaunch and watch for progress, meaning the
   step advances or the gate's failing set changes. "Relaunched" doesn't
   count as progress.
6. **Record:** every action becomes an audit row with a basis, a falsifier,
   and a backlog candidate (see RSI below).

**Hard limits (refused by the tools, not by prompt):**

- Never edits product code or tests. Never writes proof records
  (`batch-gate*.json`, verification records). Never ships, pushes, tags or
  deploys. Never relaxes a gate, a timeout or a lint.
- **Two strikes:** a second action on the same sub-plan with no progress
  in between escalates. That's memory `relaunch-fixes-state-not-step-design`
  turned into a rule.

**Independence:**

- Runs on the planner home (official provider), never the worker model or
  the worker home. A batch's own worker never judges its own block.
- On a selfmod project (ilk-skills), triage edits only plan files in the
  data home, never the clone or the worktree. Plan files are what a
  human-driven session edited today.

**Cross-project:**

- Two projects' triage instances coordinate through a shared event log
  (`~/.ilk-data/events.jsonl`: "merged", "batch dispatched", "bounce
  requested"), not chat. Today's chat protocol ("I1 merged", "ask before a
  bounce") becomes typed events plus a rule: a bounce waits for 0 foreign
  loops live.

### L3: Chad, by exception and pull

- **Not a gate on improvement** (decision D2 below). Chad is not asked to
  approve, release or acknowledge any self-improvement.
- **Pull, not push:** an audit trail of every triage action and every
  improvement build lives at `~/.ilk-data/audit/`. Chad reads it whenever he
  wants. Each improvement isn't news, because improvement is expected to be
  continuous.
- **Proactive only when stuck:** a message goes to Chad only when a product
  loop is blocked AND triage has escalated (two strikes, or no falsifier).
- **Still Chad's:** policy (backlog 19), cross-project priority, and outward
  actions the machinery can't yet make safe (see "What replaces the human
  gate").

## Two lanes: unblock now, improve later

The component does two jobs on two clocks. They must never block each other.

| | **Fast lane: unblock** | **Slow lane: improve** |
|---|---|---|
| Trigger | a block event | collected evidence, in an idle window |
| Goal | the blocked loop running again, ASAP | a recurring defect gone for good |
| Latency | minutes | hours to days |
| Output | one typed action on the blocked batch | a small continuous improvement build |
| May it wait on the other lane? | **never** | yes, by design |

**Fast-lane rule: unblock first, record second, never wait on the record.**
The backlog candidate is append-only and best-effort. If the write fails,
the loop still relaunches. So improvement work can never become a new
blocking reason.

On 2026-10-02 a human-driven session broke this rule twice: it planned root
fixes before relaunching (backlog 17, the I2 reorder). In this design,
triage relaunches with a containment action, and the root fix goes to the
slow lane.

**Containment vs root fix.** A typed containment action (`clean`, `amend`,
`regate`) isn't a workaround as long as its root fix is queued with
evidence. **Escalation to urgent:** if the same signature is contained
twice within one active batch, the containment isn't holding. The root fix
then becomes the next improvement build and takes the next idle window
ahead of everything else. The 2026-10-02 fake-revert rows (4 cleanups in
one afternoon) are the example.

## Continuous improvement builds (decision D1)

Today an improvement batch carries a full-scope verify sub-plan, which took
about 17-37 min per run on chad-mbp. I2 grew to 11 sub-plans.
Improvement instead becomes **continuous and small**, and separate from
`/ilk-ship`:

- **Unit:** one backlog candidate (or one tightly coupled pair), meaning
  1-2 sub-plans. It's sized to build and verify inside a typical idle
  window, measured from `scheduler.log` (not guessed).
- **Verify, scaled to blast radius:** the build's gates are its own pins +
  the tests that exercise the files it touches. Those are selected by
  import graph **and** by runtime path. 2026-10-02's miss was a bash-driven
  runner test (`test_integrity_stays_in_its_batch.py`) that the import-graph
  lint can't see. No per-build full-suite verify.
- **Lands at idle:** it builds in the selfmod worktree, and its merge into
  the clone (which is the chad-mbp deploy) happens only when no
  toolkit-consuming loop is live on the host. This is the existing merge
  hold.
- **Full suite at release cadence:** a release train runs the full suite
  over the accumulated builds at an idle window. A red is attributed to
  the build that caused it by **owner bisect** (L1), and that build is
  **reverted automatically**. The others stay. This keeps "unexcusable":
  a build can ship past its own scoped gate, but not past the train.
- **`/ilk-ship` becomes the release train:** tag + full suite + canary
  deploy (chad-mbp → rezmac) + automatic rollback. It runs on its own
  idle-window cadence, not per improvement.

**"Idle" defined per host:** no live loop on this host running toolkit
code (any project), and no queued master due to dispatch. Product work
preempts an improvement build at its next iteration boundary.

## RSI: triage feeds the toolkit's improvement loop

Every L2 action is evidence of a toolkit gap. It is either an L1 failure
shape not built yet, or a judgment the planner should have made when
authoring the plan.

- **Emit:** each action writes a candidate through `supervisor_emit.py`
  with `source: "triage"`, the failure signature (classification + action
  + failing node or file), and relations (run_id, sub-plan, commit). The
  existing dedup bumps `seen_count` on repeats.
- **Select:** each idle window takes the top candidate by **blocking cost**
  (minutes lost + interventions, from the audit trail), then `seen_count`.
  Urgent signatures (contained twice in one batch) come first.
- **Graduate:** a judgment-type signature handled by L2 **3 times** (a
  judgment call, not measured) is planned as an L1 driver fix, so the
  repair moves from judgment to code.
- **Measure:** interventions per batch by layer go into each batch's
  verify record. Health = L2 + L3 per batch trending down. Baseline
  2026-10-02: 14 for 2 batches (7 L1-shaped, 6 L2, 1 L3).
- **Planner feedback:** recurring `amend` reasons become `/ilk-plan` lint
  rules. "A step-0 pin injects its own fake" was seen 8 times on gh-resolve
  and 2 on ilk-skills on 2026-10-02.
- **No human gate** (D2): `/ilk-self-improve` plans as `queued`, not
  `draft`. Every build is in the audit trail.

## What replaces the human gate

`docs/future-work/unattended-self-improvement.md` (last touched 2026-09-08,
read in full 2026-10-02) said the human gate stays until four blockers
close. Their state, measured today:

| Blocker | Doc's state (09-08) | Measured 2026-10-02 | Needed for D2 |
|---|---|---|---|
| 1 Self-modification race | machinery built, not wired | **closed in practice**: every ilk-skills batch ran in `runtime/launcher/worktrees/selfmod-batch`; merges deferred while foreign loops were live | keep |
| 2 Phase 1 must run | closed (v0.9.88/89) | the v0.9.138 release still expects `could_not_compare` (no v0.9.137 baseline on this host) | **store a baseline every release**; refuse, never substitute |
| 3 Deploy verifies itself | closed (`--require-tag`) | not exercised today | keep |
| 4 Progress is self-reported | open | **still open, new form**: a gh-resolve worker satisfied "a commit per step" with an EMPTY `#step-2` marker (955f956e) | a step's commit must carry the step's evidence (a diff in the step's scope, or a declared vacuous-step marker that `plan_lint` required) |

Plus the doc's own replacement for a blanket human gate (its lines 282-295):
**canary deploy with automatic rollback.** Merge and deploy to the canary
host, watch one full dispatch cycle, deploy to the second host only if
clean, and roll back to the previous tag automatically if not.

**Order:** until Blocker 4's new form and the canary + rollback exist,
improvement builds may land on chad-mbp (the merge hold already gives a
quiet window, and owner bisect + auto-revert give rollback). But **pushing
to origin and deploying to rezmac** stay gated on the canary machinery.
That last gate is decision D4.

## Safety case: self-improvement must not break the goal

With no human gate (D2), the only thing between an improvement and a broken
loop is machinery. 2026-10-02 showed that machinery can decay silently:

- an inert fix shipped green (`ILK_SHIPPED_MARKER` set 0 times);
- a pin passed 6/6 with the runner change reverted;
- a pin asserted the bug instead of the fix (AC-3, XPASS);
- a red shipped past a gate that didn't cover its file (176b6e3).

So the safety case has to protect **the checker, not just the checked**.
Six guards, cheapest first:

1. **A protected safety kernel.** These paths are tier-0:
   - the gates (`run_local_checks.py`, the step-gate code in the runner),
   - proof and verdict (`ship_audit.py`, `verification_record.py`,
     `verify_attribution.py`, `batch_gate.py`),
   - the rollback machinery,
   - the invariant suite (guard 2).

   An improvement build that touches a tier-0 path takes the slow path:
   full suite + golden batch + mutation check + canary on both hosts, with
   no shortcut. **No build may edit the invariant suite**; changing the
   rules is the one thing still routed to Chad. The rule "the evaluator
   can't quietly modify the evaluator" is what stops Goodhart drift.
2. **An invariant suite that encodes the goal.** A small, fixed set of
   end-to-end tests that states the standing goal as checks. Each is a
   positive experiment that must go red when the rail is removed:
   - a red gate blocks a ship;
   - a step-0 pin is red for the right reason (`AssertionError`, not XPASS);
   - a worker can't write a proof record or run the suite;
   - a red is re-opened on its owner, not on whoever found it;
   - a worker ends its turn after a ship;
   - a batch can't excuse its own red.
3. **A golden batch on a canary project.** A tiny fixture project with a
   known 3-sub-plan batch (one planted red, one planted inert fix) runs end
   to end on the new toolkit, in the worktree, before landing. Pass means:
   it ships what's real, catches both planted defects, and finishes within
   its time budget. That measures **both halves of the goal**: unexcusable
   (planted defects caught) and smooth + fast (time-to-ship has no
   regression beyond a set tolerance).
4. **A mutation check on the gates (the teeth test).** At each release
   train, apply known-bad mutations to a scratch copy (revert a fix, make a
   pin vacuous, delete a gate line) and require the gates to go red on
   every one. A mutation that survives means a rail has decayed, and the
   train refuses. This is the guard that would have caught 3 of the 4
   silent failures above.
5. **Automatic rollback on outcome, not just on tests.** After a build
   lands, watch the product loops' outcomes: new block signatures, the
   intervention rate per batch (L1/L2/L3), and verify time. A regression
   attributed to the build (owner bisect over the landing window) reverts
   it automatically. **Last-known-good tag:** two consecutive reverted
   builds pin both hosts to last-known-good, pause the improvement lane,
   and escalate. That's the only proactive message improvement ever sends.
6. **One build at a time, landed at idle.** A small blast radius (D1)
   means a bad build is cheap to attribute and cheap to revert. Builds
   never stack unverified: the next one starts only after the previous one
   has landed and survived its observation window.

**What this buys:** a self-improvement can still be wrong, but it can't be
*silently* wrong for long. It has to pass the invariant suite and the golden
batch, the gates must still have teeth, and a bad outcome reverts it
without anyone reviewing it.

**Prerequisite under D2:** guards 1-4 exist before improvement builds land
unattended. Until then, builds that touch tier-0 paths don't land
unattended at all.

## Rules vs judgment (decision D3)

**Principle: a small, hard boundary enforced by tools; free judgment inside
it; outcomes, not procedure, decide whether a judgment stands.**

2026-10-02 split "rules" into three kinds, and only one of them held:

| Kind | Evidence, 2026-10-02 | Verdict |
|---|---|---|
| **Rules in prompts and plans** | 20 sub-plans carried 7 standing instructions, 96 occurrences in all (4.8 per sub-plan). Workers broke them anyway: ran the suite and overwrote the proof record, took a 2nd sub-plan after a ship, left 44 min uncommitted, edited their own pins mid-fix. The one-sub-plan rule had also failed 3x as prompt text on 2026-10-01 | weak, and they pile up: `plan_lint.py` has 55 `lint_` rules |
| **Rules enforced by tools** | `pins_only_lose_xfail` refused edited pins; the merge hold kept I2 off the clone while G1b ran; `run.lock` refused a duplicate launch; the `rm -rf` safety check refused an unguarded removal | **held, every time** |
| **Goal-driven judgment** | where the outcome was checkable it was excellent: a worker rejected its own pins 3x for passing for the wrong reason; gh-resolve-b4's pin review found an unfixed defect. Where only a proxy was checked it gamed the proxy: pins edited until green, an empty `#step-2` marker, a pin asserting the bug, a self-written proof record | good with an outcome check, Goodhart without one |

**So:**

1. **The hard boundary: tool-enforced, about 5-10 items, all about
   integrity, never about method.**
   - Who may write proof records, run the suite, edit pins, ship.
   - Nothing irreversible or outward without its gate.
   - The safety kernel and the invariant suite can't be modified by the
     work they judge.
2. **Inside the boundary, a goal plus judgment.** How to fix, diagnose,
   plan and unblock is the agent's call. Procedural rules are replaced by
   outcome checks that can't be gamed from inside: the golden batch, the
   mutation teeth test, owner bisect, automatic rollback. The agent
   decides; the outcome audit decides whether the decision stands.
3. **Rules must earn their place.** Every prompt/plan rule and every lint
   either:
   - becomes a tool check (it's integrity), or
   - is covered by an outcome check and gets deleted (it's method), or
   - stays only with a cited incident and a test that fires.

   A rule that never fired across N release trains gets retired.

**First continuous build under D3: the rule-retirement audit.** For each
of the 7 standing sub-plan instructions and the 55 `plan_lint` rules,
record:

- the incident behind it;
- whether a tool or outcome check now covers it;
- how often it fired (from logs);
- the verdict: keep / convert to tool / delete.

That shrinks the sub-plan boilerplate and the lint surface before L2 is
built on top of them.

## The risk: triage can excuse a batch

A triage agent that can `reopen`, `amend` and `ack` is a party able to
wave a batch past its own red. On 2026-10-02 the human-driven session did
that twice, both times as labelled judgment calls (AC-3 accepted vacuous;
sub-plan 7's weak pins accepted with a live check instead). With no human
gate (D2), the rails carry all of the weight:

1. Triage can never mark anything proven, shipped or excused. A ship still
   comes only from the driver's gate, and a release only from the train's
   full suite.
2. Every acceptance is a labelled choice in the audit trail, with a
   falsifier, **and the falsifier is checked automatically** by a later
   release train. There's no human reviewing it in between anymore.
3. Triage's and builds' own actions are logged like runs, so a self-serving
   action shows in the trail (memory `worker-forged-verification-record`).
4. If triage can't name a falsifier, it escalates instead of accepting.

## Build order

1. **L1 items already in flight:** I2 sub-plans 2 (live-write fix) and 6
   (classification by the run's own stop reason).
2. **L1 items to build next**, each as its own continuous build (D1),
   starting with the **rule-retirement audit** (D3):
   owner bisect (backlog 22); `batch_gate.py` worker-session refusal;
   runner re-exec on script change; watchdog start-order fix; the `rmtree`
   lint; a pin that the runner calls `export_iteration_marker`.
3. **Safety case first** (guards 1-4: tier-0 kernel, invariant suite,
   golden batch, mutation check), then **release-train machinery**: a
   baseline stored every release, owner bisect + automatic revert, canary +
   rollback, last-known-good pinning (guards 5-6).
4. **L2 v0, read-only** (diagnose + propose, measured against what humans
   did), then **L2 v1, acting** with two-strike escalation.
5. **RSI wiring:** `source: "triage"`, blocking-cost selection, idle-window
   scheduler for improvement builds, the audit trail, per-batch
   intervention metric.

## Multi-project RSI

Chad, 2026-10-02: gh-resolve, a separate repo, should get an RSI mechanism
like this one, or the same one. Options were a new third repo serving
every target repo, or a separate mechanism inside each of ilk-skills and
gh-resolve.

**Recommendation: one engine, built inside ilk-skills, with a per-project
adapter. gh-resolve is its second consumer. No new repo yet, and no second
copy in gh-resolve.**

### Why not one mechanism per repo

Two copies means two sets of rules, guards and bugs. That is the rule
accumulation D3 sets out to remove. The defects are already shared:
2026-10-02 found the same pin defect class on both repos. gh-resolve-b4
counted 7 defective pins in 3 files of 10-02e, ilk-skills found 2 (I1 sp7,
I2 sp2 AC-3), and a single fix (for example b4's step-1 revert check)
should cover both.

### Why not a new repo yet

- **The engine doesn't exist.** Guards 1-4, owner bisect, and canary with
  rollback are all unbuilt. Contracts changed several times today (I2 has
  10 work sub-plans). Extracting before the contracts settle freezes the
  wrong boundary.
- **A third repo is a third deploy on two hosts.** That means more daemon
  bounces, more merge holds, and more version skew. That coordination was
  most of 2026-10-02's human effort (see the gh-resolve-b4 review).
- **The parts already live here.** The backlog
  (`skills/ilk-feedback/scripts/improvement_backlog.py`), the emit
  contract, `/ilk-plan`, the selfmod worktree, `/ilk-ship`, the watchdog
  and the scheduler are all here. gh-resolve's batches are already ilk
  loops.

### Two kinds of RSI, and who owns each

1. **Unblocking a loop** (L1 + L2 in this doc) is always the toolkit's
   job, whatever repo the loop runs on. A blocked gh-resolve batch is a
   blocked ilk loop.
2. **Improving the target repo** (for gh-resolve, making the resolver
   better) uses the same engine. But the target repo owns its own goal:
   its invariant suite lives in that repo, no build of that repo may edit
   it, and changing it is routed to Chad (same rule as guard 1).

### The split

| Engine (one copy, ilk-skills) | Adapter (one per project) |
|---|---|
| block detection, L2 triage, the action vocabulary, two-strike | evidence sources (gh-resolve: reap + run records, resolver outcomes; open, see below) |
| backlog + dedup, selection by blocking cost | tier-0 paths: that project's own gate and proof code |
| small builds, scoped gates, landing at idle | invariant suite: that project's goal as end-to-end checks |
| release train, owner bisect, auto-revert, mutation teeth test | golden batch / canary project |
| audit trail, `events.jsonl` with leases | deploy + rollback steps |

**Where adapter config lives:** in the data home, keyed per project, not
in the consumer repo (memory `toolkit-data-never-enters-consumer-repo`).
The adapter points to tests that the project owns, and the engine runs
them.

### What exists today (read 2026-10-02)

- **The backlog is one global, toolkit-keyed file.**
  `improvement_backlog.py:33,69` hard-codes
  `<data_root>/ilk-skills-improvements/`. Of its 123 candidates, 123 have
  no target-project field. Evidence on 8 points at gh-resolve, but every
  candidate means "improve ilk-skills". There is nothing to route a
  candidate to gh-resolve.
- **A per-project tracker already exists.**
  `skills/ilk-feedback/scripts/project_tracker.py` (136 lines) routes the
  same IO layer to `<data_root>/projects/<key>/`. Its docstring says the
  global backlog "stays as-is". This is the seam for the target-repo half.
- **gh-resolve already reads one engine record.** gh-resolve's reap reads
  `run_result.py`'s record (`tests/test_reap_reads_the_run_result.py` in
  gh-resolve, per gh-resolve-b4; not read here).

### Build order changes

- **Step 3 (safety case):** build guards 1-4 against an adapter interface
  from the start: tier-0 paths, invariant suite, golden batch, deploy and
  rollback. ilk-skills is adapter 1.
- **New step after 3: gh-resolve as adapter 2, before L2.** A second
  consumer forces the engine/adapter boundary to be honest. It also lands
  the fixes both repos need once:
  - a `rebaseline-pins` / `reopen <slug> 0` action with tool-checked
    output (gh-resolve-b4 review, f1);
  - the postmortem no longer changing the blacklist (f3);
  - a lease event so two projects can't yield to each other forever (d).
- **Backlog:** add a `target` field to candidates (default `ilk-skills`,
  for the 123 existing rows). The engine routes a candidate to the target's
  tracker. Toolkit candidates stay global.

### When to extract to its own repo

Extract on any one of these:

- a third project wants RSI;
- the adapter interface survives about 3 release trains unchanged
  (a judgment call, not measured);
- independence binds: ilk-skills improving the engine that judges
  ilk-skills. The protected kernel (guard 1) covers this until then, and a
  separate repo would cover it structurally.

If the engine/adapter split is kept, extraction is mostly a file move.

### gh-resolve adapter, as gh-resolve-b4 described it (2026-10-02, unverified here)

- **Deploy and rollback:**
  - On chad-mbp (resolver fleet b), gh-resolve runs from its main checkout
    through a PYTHONPATH wrapper, so a commit to main is live at once. That
    is the same shape as the ilk-skills clone, so it needs the same
    land-at-idle rule.
  - rezmac (fleet a, the only producer) runs a release tag:
    `git fetch --tags`, then `gh-resolve upgrade --to vX`. The agents reload
    only if `doctor --strict` passes. Then probe launchctl, because rezmac
    has been left with 0 of 5 agents before.
  - Rollback is `upgrade --to <previous tag>`. A downgrade has never been
    exercised.
  - D4 applies here unchanged: the rezmac deploy stays Chad's until canary
    and rollback exist.
- **Canary:** `~/gh-resolve-canary-clone` (consumer test repo
  inluck-net/gh-resolve-canary) is a smoke target, not a deploy target. It
  is driven by `scripts/canary_smoke.sh` and `canary_preflight.sh`. Smoke
  step 2 needs a keychain session, so it isn't unattended yet. This is the
  natural golden-batch home (guard 3).
- **Evidence (per host):**
  - `ledger.jsonl` (rezmac ~19.7k rows): terminal states,
    escalation_reason, push_failure_kind, admission reject_rule;
  - reap, triage, drain and gc logs. These have no timestamps, which is a
    gap;
  - GitHub PR state, needs-human labels and escalation comments;
  - the ship-proof ledger;
  - consumer pre-push hook outcomes;
  - `docs/design/known-defects.md`;
  - the project's own ilk logs.
- **Goal metric:** labelled issue to PR with no human. Measure the time
  from label to PR and the share of runs ending escalated or needs-human.
  This is gh-resolve's equivalent of "interventions per batch".
- **Tier-0 paths:**
  - gates: `tools/gh_resolve/admission.py`, `gate_manifest.py`,
    `scripts/pins_only_lose_xfail.py`;
  - proof: `verify.py`, `proof_channels.py`, reap's ship path;
  - state of record: `ledger.py`, `run_liveness.py`, `liveness.py`;
  - outward writes: `writeback.py`, push and land;
  - deploy: `upgrade.py`;
  - meta-guards: `tests/conftest.py`, `tests/test_no_*.py` (3),
    `tests/test_raw_ledger_readers.py`.
- **gh-resolve's conditions (accepted):**
  1. The adapter names the consumer repo (kira-cloudflare) as an outward
     boundary. Any write there stays Chad's, whatever the engine decides.
  2. The engine never detects liveness by argv; it uses run.lock pids
     (memory `merge-guard-matches-any-command-line`).

## Decisions (Chad, 2026-10-02)

- **D1.** Improvement is continuous small builds, separate from
  `/ilk-ship`. Each is quick to build and verify, so it can use any idle
  window.
- **D2.** No human approval before an RSI improvement. Log it, keep audit
  records Chad can check anytime, and keep proactive notification minimal.
- **D3.** Fewer rules, harder ones: a small tool-enforced integrity
  boundary, free judgment inside it, and outcome checks deciding whether a
  judgment stands. Prompt/plan rules are converted to tools, covered by
  outcomes, or retired.
- **D4.** The release train may push to origin and deploy to rezmac on its
  own **after** canary + automatic rollback exist. Until then, push and
  rezmac deploy stay Chad's; improvement builds may land on chad-mbp.

## Open questions for Chad

1. **Where L2 runs:** a detached planner-home session per block
   (stateless), or a long-lived one per host (keeps context across a night)?
2. **Cross-project authority:** may one project's triage message the
   other's, or only write events?
3. **Backlog 19** (auto-relaunch after a batch's first red that it caused
   itself) is the same policy as L2's `ack-and-relaunch`. Under D2 the
   proposal is yes, with two-strike escalation.
4. **Multi-project RSI:** accept one engine in ilk-skills plus per-project
   adapters, with gh-resolve as adapter 2 ahead of L2 (section above)?
   Chad asked for the section on 2026-10-02; the shape itself is not yet a
   decision.
