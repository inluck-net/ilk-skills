# RSI steering: how Chad gives RSI goals, requirements and direction

Decided 2026-10-03 (Chad + ilk-skills-30, owner session). Status: **accepted, not built**.
Target home in the repo: `docs/architecture/rsi-steering-design.md`. Commit it there at the
first window with no live ilk-skills runner: a dirty tree breaks a running verify, and a
commit lands inside that batch's base..head range.

Build as **batch j**, planned by the owner session after **g** (daily digest) and **h**
(idle-window auto-planner) ship. j builds on both.

## Problem

Today RSI has one input: defect-shaped rows in
`~/.ilk-data/ilk-skills-improvements/candidates.json` (gap + proposed_fix). Batch h's
auto-planner v1 plans only `source=triage` rows. Requirements reach the loop only through
a human `/ilk-plan`, the inbox or Lark. Chad has no way to say "build X" or "focus on Y"
and have RSI carry it out.

## Decision: three kinds of input, each with its own authority and store

| kind | what Chad writes | what RSI does | store |
|---|---|---|---|
| **Goal**: standing, measurable | a metric, a target, an optional deadline | measures it daily, puts the gap in the digest, plans batches until the target is met | `docs/rsi/direction.yml` in the ilk-skills repo |
| **Steer**: direction, expires | weights and constraints, each with `until:` | changes how the planner ranks everything else; never a task itself | `docs/rsi/direction.yml` |
| **Requirement**: one-off build | an outcome plus how to check it | specs it, plans it, builds it; one escalation if ambiguous | GitHub issues on `inluck-net/ilk-skills` labelled `rsi:req` |
| progress (machine state) | (none) | daily metric readings, goal ↔ batch links, retry counts | `~/.ilk-data` (per host) |

### Why these stores

- **direction.yml in the repo:** small, rarely changed, read on every scheduler tick, so
  it must work offline and the same way every time. Versioned with the code that reads
  it. Inside the protected kernel, so RSI cannot edit it: the same rail that stops a
  worker removing its own gate.
- **Requirements in GitHub issues:** they arrive any time, from any host or a phone. One
  issue per requirement, with open/closed as the state. Same model as gh-resolve's
  ADR 0103 (issues = backlog, files = evidence record). An importer projects them into
  the local backlog, so the planner never calls GitHub.
- **Not everything in issues:** a network call in the scheduler tick adds failure modes
  (auth, rate limits, the ssh `PATH` that lacks `/opt/homebrew/bin`). An issue is
  editable by any holder of the token; a kernel file is not. ilk-skills already shows the
  drift, with 36 open issues and 98 open backlog rows unsynced.
- **Not everything in docs/:** every requirement would need a commit, and rezmac never
  commits.

## Rules (enforced in code, not prose)

1. **Chad's inputs outrank RSI's own findings.** RSI may read goals, steers and
   requirements, but never edit or close them. direction.yml is in the protected
   kernel; RSI-sourced candidates rank below Chad-sourced ones unless a steer says
   otherwise.
2. **RSI only reads GitHub (v1).** No comments, labels or closes. A shipped requirement
   shows in the digest ("req #45 shipped in v0.9.150"), and Chad closes it.
   Judgment call: read-only, because RSI's outward posts are unchecked (memory
   worker-outward-posts-are-ungated-not-licensed). Wrong if closing by hand becomes a
   chore; then add one narrow write, close-on-verified-ship.
3. **Only Chad's issues count.** The importer accepts `rsi:req` issues authored by
   Chad's account and ignores the label on anyone else's.
4. **Readers fail closed.** An unparseable direction.yml, or an import whose row count
   doesn't match its issue count, stops the planner and escalates in the digest. Never
   "no goals" (memory empty-answer-must-be-unconstructible-without-looking).
5. **A requirement without an acceptance check is refused,** not guessed: one
   escalation in the digest, never a question round.
6. **A steer without `until:` is rejected when the file is read.**
7. **Multi-batch requirements become a roadmap:** a chain of masters sharing one goal.
   Progress is the goal's metric, not a count of shipped batches.
8. **The digest is the steering wheel.** Every item RSI did or plans carries a one-line
   "why" (which goal, requirement or defect). Chad steers by editing direction.yml (one
   line, or any session commits it) or by opening an `rsi:req` issue. `/ilk-want
   goal|req|steer "..."` writes to the right store.

## Build order inside j (judgment call)

Goals first, then steers, then requirements. Basis: goals alone would have caught the
2026-10-03 verify-speed problem at the first daily measurement, and requirements need
the most new machinery (spec research). Wrong if Chad mostly wants feature work rather
than standing quality targets; then requirements go first.

## First goal to register when j ships

`verify_p50_minutes <= 5` on every project. Baseline on 2026-10-03: ilk-skills 29.7
min/verify (17 verifies since 2026-09-29), gh-resolve 24.7. The measurement method: join
`*-verify` slugs to `local_checks` in each project's `.ilk-loop.log` (the script in
session scratchpad `vcost.py`, to be committed as a goal probe). Batch i
(`verification-is-continuous`) is the work aimed at it.

## Dependencies and open points

- Needs g (digest renders goal gaps and "why" lines) and h (planner reads the backlog,
  ranks, plans).
- Needs a target field on candidate rows (adapter-2) for requirements filed against
  other repos (gh-resolve). gh-resolve's export contract is agreed: owner session ↔
  gh-resolve-44, 2026-10-03.
- h v1 plans only `source=triage`. j widens it to `source in {chad-goal, chad-req}`, then
  to the ranked open backlog under steers.
