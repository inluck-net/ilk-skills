# Built-in agent fan-out vs. the ilk plan-loop

Host agents (Claude Code, Cursor, Codex) ship a built-in way to spawn
helper agents — Claude Code's `Agent` / `Workflow` tooling, sub-agents,
and in-session orchestration. This repo's `/ilk-plan` → `/ilk-run`
toolkit is a *different kind* of orchestration. They are complementary,
not competing: they sit at different layers and optimise for different
properties.

This doc explains the distinction so you can pick the right one (and so
the routing rule in [Auto-use-ilk-plan routing](../README.md#auto-use-ilk-plan-routing)
makes sense).

## The one-line distinction

> **Built-in fan-out** = parallel *breadth* inside one session —
> RAM-bound, ephemeral, autonomous-to-completion.
>
> **ilk plan-loop** = durable *depth* across sessions —
> disk-bound, resumable, gated and optionally supervised.

## Built-in agent fan-out

Sub-agents spawned **within a single live host session**.

- **Lifetime = the session.** Agents run inside the current
  conversation's process. If the session ends, the chat closes, or the
  machine sleeps, the work is gone. There is no disk-backed resume.
- **Orchestrator is the model, in-context.** The main loop holds the
  plan in context (or in a `Workflow` script) and dispatches agents.
  Coordination is in-memory.
- **Parallelism is the point.** Fan out N readers / finders / verifiers
  concurrently, gather the results, synthesize. Real wall-clock speedup
  for work that decomposes cleanly.
- **No human-in-the-loop checkpoints.** A `Workflow` can stage
  pipeline → verify, but it runs to completion autonomously in one shot.
  There is no "stop, let CI run, let a human review, resume tomorrow."
- **Best for:** comprehensive search / review / research, multi-
  perspective verification, anything that fits in one sitting and
  benefits from many parallel contexts.

## The ilk plan-loop (`/ilk-plan` → `/ilk-run`)

A **disk-as-bus, resumable, cross-session execution system**.

- **Lifetime = unbounded.** State lives on disk as a MASTER plan +
  gated sub-plans under `~/.ilk-data/projects/<key>/`. Each step is
  recoverable from files, so a run survives interruption, chat-session
  boundaries, crashes, timeouts, and overnight gaps. A watchdog
  auto-relaunches it.
- **Orchestrator is the filesystem + scheduler.** `/ilk-plan`
  decomposes a task into a MASTER with sequenced sub-plans (status
  vocab, `depends_on`, per-step `local_checks`). `/ilk-run` drives the
  loop with a watchdog; `/ilk-schedule` drains many projects' queues
  from one supervisor.
- **Gated verification is the point.** Steps carry machine-checkable
  `local_checks`, ship gates, and `draft` / `supervised_only` flags. The
  loop pauses on failures, **blocks loudly** on blacklist reasons, and
  ships only when gates pass.
- **Sequential / dependency-driven, not fan-out.** The value is
  durability and gating across many steps over time — not concurrent
  speedup within one step. (Parallelism is available a layer up, via
  concurrent worktrees, each its own `project_key`.)
- **Best for:** large multi-module work, unattended / overnight runs,
  anything needing checkpoints between steps or survival across
  interruptions.

## Side-by-side

| Dimension | Built-in fan-out | ilk plan-loop |
|---|---|---|
| Unit of work | sub-agent in current session | step in a MASTER + sub-plans |
| State lives in | session memory / a `Workflow` script | files under `~/.ilk-data/` |
| Survives session end | no | yes |
| Survives crash / timeout / reboot | no | yes (watchdog resumes) |
| Coordination bus | in-memory, model-driven | filesystem, scheduler-driven |
| Primary axis | parallel breadth (speed) | durable depth (continuity) |
| Verification | optional verify stage, in-shot | per-step `local_checks` + ship gates |
| Human checkpoints | none (runs to completion) | gates, `draft`, `supervised_only` |
| Concurrency model | many agents at once | one step at a time (parallel via worktrees) |
| Cost profile | one session's tokens, fast | many sessions over time; cheap-worker engine available |
| Best for | search / review / research in one sitting | multi-step, unattended, gated, resumable work |

## How they relate

They compose. The routing heuristic decides which to reach for:

- Work that **finishes now in one session** → direct-implement or
  built-in agents / `Workflow`.
- Work that **won't finish, must survive interruption, runs unattended,
  or needs gated checkpoints** → route to `/ilk-plan`.

You can even use built-in fan-out *inside* an ilk step (e.g. a sub-plan
step that spawns parallel finders). But ilk itself never relies on
in-memory parallelism for its durability guarantees — that is precisely
why it uses files as the bus.

See [Auto-use-ilk-plan routing](../README.md#auto-use-ilk-plan-routing)
for how the choice is automated, and the
[`ilk-loop` skill](../skills/ilk-loop/SKILL.md) for the resume / gating
mechanics.
