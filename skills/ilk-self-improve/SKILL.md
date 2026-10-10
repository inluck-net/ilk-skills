---
name: ilk-self-improve
description: >-
  Plan toolkit improvements from the shared improvement backlog. Reads open
  candidates emitted by /ilk-feedback, formats a task description, and
  delegates to /ilk-plan on the ilk-skills repo. The resulting master is
  written as draft; a human releases it. Triggers:
  "/ilk-self-improve", "improve ilk", "self-improve ilk", "ilk 自我改进".
---

# ilk-self-improve — backlog → plan adapter

A source adapter for the ilk loop, analogous to `/ilk-lark`. It
pulls from the **improvement backlog** (populated by `/ilk-feedback` when a
postmortem finding is a toolkit/process gap) and hands a formatted task
description to `/ilk-plan`.

## When to use

- The user says "/ilk-self-improve", "improve ilk", "self-improve ilk",
  or "ilk 自我改进".
- After a `/ilk-feedback` postmortem surfaced toolkit gaps and the user
  wants to plan fixes.

## Boundary

This skill is a **planner**, not an executor. It produces a plan; a human
releases it (`draft` → `queued`). The loop runs the batch in the selfmod
worktree because it edits the toolkit itself. It does NOT auto-apply changes.

## Priority

A smoothness regression (a pipeline that was smooth and now stalls, relaunches
onto the same red, ships a red step, or cannot release) goes ahead of every
backlog row. See `docs/standards/operating-principles.md` section 1.

## Workflow

1. Run `build_task.py` to read open candidates from the improvement backlog.
2. If the backlog is empty, report "nothing to improve" and stop.
3. Otherwise, hand the task description to `/ilk-plan` on the ilk-skills repo.
4. `/ilk-plan` writes the resulting master as `status: draft` with
   `supervised_only: false` (retired 2026-09-20 — decomposition-principles.md
   §13) and auto-registers the project.

## Key files

- `scripts/build_task.py` — reads backlog, emits task description.
- `scripts/autoplan.py` — unattended auto-planner (see below).
- `scripts/autoplan_rails.py` — kernel guard, ranking, screening, master checks.
- Backlog store: `~/.ilk-data/ilk-skills-improvements/candidates.json`
  (managed by `ilk-feedback/scripts/improvement_backlog.py`).

## Unattended mode

`autoplan.py` runs the auto-planner without a session.  When the
ilk-skills queue has had no runnable master for N consecutive scheduler
cycles, it starts one detached `claude -p` session on the planner home
(below) for the top-ranked candidate.

Only `open` candidates from an admitted source are eligible
(`autoplan_rails.ELIGIBLE_SOURCES` for the toolkit). Any candidate whose
text names a kernel path is escalated, never planned (`autoplan_rails.py`
`rank` / `screen_candidate`).

### Other projects (track B)

`scripts/autoplan_projects.py` is **not kernel**. It declares which projects
besides the toolkit autoplan may plan for (`discover_extra`), and how their
rows load (`load_entries`). An improvement batch may extend it; gh-resolve's
export (`autoplan.backlog` in its `.ilk-launch.json`) is the first intended
user. Today it returns no projects.

The rails stay in the kernel (`autoplan.py`, `autoplan_rails.py`):

- The toolkit is always found first, by the kernel.
- An extra project's repo is resolved from its own data dir. Its safety
  kernel is the `autoplan.kernel_file` named in its own `.ilk-launch.json`,
  inside its repo, and loads fail closed: no readable kernel means the
  project is skipped and audited. Candidates and masters are screened
  against that kernel.
- Attempts, blocks and `planned` marks for an extra project go to autoplan's
  overlay (`<data root>/autoplan/projects/<key>/overlay.json`), never into
  the project's own backlog.
- Auto-planned masters get `priority: -1` (`AUTO_PLANNED_PRIORITY`), below
  every consumer master, including those with a null priority.

### Planner home

`plan()` refuses a session whose init event reports a GLM or MiMo model
(`refused`, reason `model <name>`).  The manager home stays GLM because
verification runs there, so the scheduler's tick does not inherit it.
The tick's home is `$ILK_AUTOPLAN_HOME`, else `~/.claude-triage` when
that directory exists, else the scheduler's environment unchanged.  A
host with no triage home therefore behaves as before.  Before relying on
a host, run `CLAUDE_MANAGER_HOME=<home> autoplan.py probe` and check that
the model is allowed.

RSI runs on the development Mac only while it is being proven end to end.
Moving it to a long-running host needs three things: a planner home with
an allowed model, the backlog (it lives in each host's own `~/.ilk-data`),
and `autoplan.enabled` true on that host only.

### Enablement

`.ilk-launch.json` `autoplan.enabled: true` in the toolkit repo,
mirroring `ship.release_train`.  The auto-planner finds the toolkit
project as the one data dir whose resolved repo has that flag and
contains `commands/ilk-plan.md`.

### Kill switch

`~/.ilk-data/autoplan.disabled` stops it on a host without a code
change.

### Paused state

Two consecutive planning attempts that end `draft` write
`<data root>/autoplan/paused.json`.  The lane stays paused until the
file is removed.

### Commands

- `autoplan.py tick [--dry-run] [--json]` — called once per scheduler
  cycle; never waits on claude.
- `autoplan.py plan --candidate ID --project-key K --run-id R` — the
  detached planning part (spawned by `tick`).  It is also the **manual
  trigger**: run it from the toolkit clone, with
  `CLAUDE_MANAGER_HOME=<planner home>` and `--run-id manual-<timestamp>`,
  to test the planning session without waiting for the idle window
  (60 min after the last master edit, plus 6 idle ticks).  It goes
  through the same `plan()` pipeline: forced draft, `check_master`, lint,
  preflight and `draft_only`.  It skips the idle gate and ranking, so use
  the candidate `rank` would choose.  A failed run spends one of the
  candidate's two `autoplan_attempts`.  Do not edit or commit in the
  clone while a session runs: `plan()` compares `git status` before and
  after, and any change reads `clone-modified`, a critical escalation.
- `autoplan.py probe [--json]` — test the manager home (owner session's
  live check after ship).

### Draft-to-queued decision

The auto-planner (code) owns the `draft` to `queued` flip.  It runs
`plan_lint` and `plan_preflight` and flips to `queued` only if both
pass.  This overrides step 8b of `/ilk-plan`.

While `.ilk-launch.json` `autoplan.draft_only` is true, a clean draft
stays `draft` (audit `dry-period-drafted`) for the owner to review; set
to false, a clean draft is queued and the scheduler runs it.

## See also

- `/ilk-feedback` — emits upstream candidates into the backlog.
- `/ilk-plan` — the planning core this adapter delegates to.
- `skills/ilk-feedback/scripts/improvement_backlog.py` — backlog schema + API.
