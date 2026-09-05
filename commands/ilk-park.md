Take a batch out of the scheduler's queue — for when a master should stop
being dispatched until a human says otherwise, and you want the reason on the
record. The inverse of the queue side of `/ilk-resume`.

Use when the user says "park this batch", "cancel the batch", "stop the loop
picking this up", `/ilk-park`, "停掉这个批次", or after finding that a running
batch is redundant, superseded, or wrong.

This command **only rewrites the master's status** — it does NOT kill a
running loop, and it does NOT touch the blacklist. To stop a loop that is
running right now, park FIRST, then `/ilk-stop`; stopping without parking just
lets the scheduler re-dispatch on its next poll.

## Park is durable; the blacklist is not

The scheduler dispatches a project only when one of its masters is `queued` or
`active` (`plan_status._RUNNABLE_STATUSES`, applied in `scheduler_scan` pass
1). Setting the status to `blocked` removes it from the scan entirely.

A blacklist does **not** do this. A blacklist-classified stop writes a
postmortem and buys a **60-minute backoff**
(`blacklist_status.is_blacklisted` → `within-backoff`), then dispatch resumes.
Measured 2026-09-05: a duplicate resolver run was killed three times and came
back each time, because each kill only bought an hour; the park held across
the expiry. If the answer to "when should this run again?" is "not until I say
so", park it — do not rely on killing it.

`<skill-root>` below means the installed skills base directory —
`~/.claude/skills/`, `~/.cursor/skills/`, or `~/.codex/skills/` depending on
the host agent. On Windows use `python`, not `python3`.

## 1. See what is there

```bash
python3 "<skill-root>/ilk-loop/scripts/park_master.py" --project . --status
```

Lists every master with its status and any existing `parked_at` /
`parked_reason`. Run this first when the project has more than one master.

## 2. Park it

```bash
python3 "<skill-root>/ilk-loop/scripts/park_master.py" \
  --project . --reason "duplicate of PR #4622"
```

Add `--master MASTER-....md` when several are parkable — the command refuses
to guess and lists the candidates. Add `--dry-run` to see the decision without
writing.

The reason is recorded in the master's frontmatter as `parked_reason`, with
`parked_at`. **Always pass one.** A bare `status: blocked` cannot be told
apart from a stall the loop inflicted on itself, and that ambiguity is what
turns a five-second question into an investigation later.

## 3. Confirm the scheduler dropped it

```bash
python3 "<skill-root>/ilk-watchdog/scripts/scheduler_scan.py"
```

The project must be absent from the output. In `~/.ilk-data/logs/scheduler.log`
the next poll should stop naming the key at all — a `skip-blacklist:` line
still names it, and means the backoff is masking it rather than the park
holding.

## 4. Un-park when the work should resume

```bash
python3 "<skill-root>/ilk-loop/scripts/park_master.py" --project . --unpark
```

Returns the master to `queued` and removes the stamp. The scheduler picks it
up on its next poll.

## Boundary

- Does not launch, stop, or kill anything — redirect to `/ilk-run` and
  `/ilk-stop`.
- Does not write or clear a blacklist — that is `/ilk-resume`.
- Only acts on `queued` / `active` masters. `shipped` is done, not parked, and
  `draft` is already invisible to the scheduler.
- Parking preserves everything else: sub-plan statuses, commits in the
  worktree, and logs are untouched.
