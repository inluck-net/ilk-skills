# Retro 2026-09-21 — switching the worker model took five manual steps and burned three minutes of the wrong account

The operator asked to try glm-5.3 as the loop worker model on 2026-09-20
evening, after measurements showed mimo-v2.5-pro loop iterations at 64-96%
model time (~8.8s/turn, $3.70-8.74/iteration, 146k-219k input tokens; the
window-fit verify iteration spent 1644s inferring and 73s running tools).
What followed was a five-step manual sequence with two wrong turns — the
operator's own conclusion: "we need some command or skill that helps switch
the model of worker for ilk-skills components smoothly."

## Timeline (all measured/observed this session)

| Time | Step | Outcome |
|---|---|---|
| ~23:45 | `.ilk-launch.json` `worker_engine: claude-manager` set | **Defeated**: the scheduler dispatches with an explicit `--engine claude-worker` (scheduler.sh:1192), and CLI beats config in `resolve_engine` (launch.sh:355) |
| ~23:55 | operator redirect: keep the claude-worker identity, change the home's env | correct mechanism |
| ~23:58 | hand-mirrored the manager home's glm env block into `~/.claude-worker/settings.json` (backup written by hand) | works for new sessions |
| ~00:00 | killed the stale mimo runner | **Watchdog respawned without the engine flag** → `DEFAULT_ENGINE="claude"` (launch.sh:24) → the PRIMARY account: `model=claude-opus-5[1m]` for ~3 minutes of official quota before a second kill |
| ~00:05 | stopped watchdog → killed runner → waited for the scheduler's own dispatch (which carries the engine flag) | correct |

The handover's "dormant mystery" — something rewrote the worker settings to
glm at 14:47 on 2026-09-20, no agent identified — is now legible as an
earlier manual attempt at exactly this switch, presumably reverted. Manual
settings surgery on the worker home is something people DO; it should be
something the toolkit PROVIDES.

## The hazards, each verified in-session

1. **Engine-flag precedence is invisible.** A project-level
   `worker_engine` is silently overridden by the scheduler's CLI flag. Two
   vocabularies, no warning when both exist.
2. **A relaunch without an engine lands on the primary account.**
   `DEFAULT_ENGINE="claude"` (launch.sh:24) means any spawn path that
   forgets the flag — the watchdog's relaunch being the observed one — runs
   loops on the official Anthropic quota. This is the exact hazard the
   engine system was built to prevent (the engine comment at launch.sh:33-35
   says so in words).
3. **Switching has stop-ordering invariants.** Kill the runner before the
   watchdog and the watchdog undoes you (possibly on the wrong account);
   the watchdog must stop first. Nothing documents this.
4. **Verification is manual.** The operator confirmed the switch by opening
   a bare claude-worker session and reading the model line — the correct
   check, done by hand.
5. **Homes are plural.** Slot 1 is `~/.claude-worker`, slot i≥2 is
   `~/.claude-worker-<i>` (scheduler.sh:656-665). Tonight only slot 1
   existed; a switch command must sweep every home that exists or the
   second slot silently keeps the old model.
6. **Metadata goes stale silently.** `tools/claude-worker/role-registry.json`
   still says the coder role is mimo; nothing checks it against the live
   settings after a switch.

## Prevention — `ilk-worker-model` (the command this retro exists for)

A sibling to `ilk-worker-mcp` under `tools/claude-worker/`, plus the two
defect fixes that make switching safe:

- **`ilk-worker-model show`** — the live model per home (main + every
  slot), read from each home's settings env, cross-checked against the
  role registry (mismatch = loud).
- **`ilk-worker-model use <role-or-model>`** — the smooth switch:
  1. refuse or drain: detect live loops; if the operator passes
     `--now`, stop watchdog FIRST, then runners (tonight's invariant,
     encoded);
  2. rewrite the env block in every existing worker home (main + slots),
     sourcing values from the role registry;
  3. timestamped backup per home; `ilk-worker-model restore` is one
     command;
  4. verify: spawn a probe session with the worker home and assert the
     reported model equals the target — the operator's manual check,
     automated; refuse to report success otherwise;
  5. print the engine-precedence facts (what the scheduler will dispatch,
     what a watchdog relaunch would get).
- **Fix the respawn hazard**: the watchdog's relaunch must carry the
  engine of the run it replaces (or `DEFAULT_ENGINE` becomes
  `claude-worker`, making the forgot-the-flag failure land on a worker
  home, never the primary account). Belt and suspenders: both.
- **Registry honesty**: after a switch, either update the role registry's
  model field or emit the mismatch loudly (`show` cross-check covers the
  interim).

## Follow-through

Planned as the next batch (`/ilk-plan`, queued behind the running work).
The glm-5.3 A/B itself proceeds independently: worker env switched at
23:58, first glm canary = window-fit-verify, metrics compared per-iteration
against the mimo baseline above.
