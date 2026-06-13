Un-park a scheduler-blacklisted project by writing a resolve-ack — for when a
blacklist-classified stop (stuck-no-progress / api-blocked / budget-exhausted /
local-checks-stuck / dependency-unreachable) has been **fixed** and you want the
scheduler to dispatch it again without waiting out the 60-minute backoff.

Use when the user says "resume ilk", "un-park", "unblock ilk", `/ilk-resume`,
"清除 ilk 黑名单", or after fixing the blocker a postmortem identified.

This command **only writes the resolve-ack** (it does NOT launch a loop). The
scheduler picks the project up on its next poll; or run `/ilk-run` to dispatch
immediately.

How it works: the scheduler's blacklist decision (shared
`blacklist_status.py`, consumed by both `scheduler.ps1` and `scheduler.sh`)
treats a project as **not** blacklisted when a resolve-ack exists with
`cleared_at >= the failing postmortem's generated_at` — i.e. the human vouches
the fix lands *after* the run that failed. A stale ack (older than the failure)
does not clear it.

`<skill-root>` below means the installed skills base directory —
`~/.claude/skills/`, `~/.cursor/skills/`, or `~/.codex/skills/` depending on the
host agent. On Windows use `python`, not `python3`.

## 1. Resolve the project data dir

Resolve the project with `ilk_paths.py` (owned by `ilk-loop`):

```bash
# macOS / Linux
python3 "<skill-root>/ilk-loop/scripts/ilk_paths.py" --start .
```

```powershell
# Windows
python "<skill-root>\ilk-loop\scripts\ilk_paths.py" --start .
```

From the JSON, take `external_runtime_dir` (…/projects/<key>/runtime) and use its
**parent** — the project data dir `…/projects/<key>` — as `--project` below. If
`project_root` is `null`, tell the user to `cd` into a project root and STOP.

## 2. Write the resolve-ack

```bash
# macOS / Linux
python3 "<skill-root>/ilk-watchdog/scripts/blacklist_status.py" ack --project "<project-data-dir>"
```

```powershell
# Windows
python "<skill-root>\ilk-watchdog\scripts\blacklist_status.py" ack --project "<project-data-dir>"
```

This writes `<project-data-dir>/runtime/launcher/blacklist-cleared.json` with
`cleared_at = now` (atomic, BOM-free). Because `now` is after the failing
postmortem's `generated_at`, the next blacklist check returns `blacklisted: false`.

Optionally confirm:

```bash
python3 "<skill-root>/ilk-watchdog/scripts/blacklist_status.py" check --project "<project-data-dir>"
# -> {"blacklisted": false, "reason": "resolved-by-ack", ...}
```

## 3. Tell the user what happens next

- The scheduler will dispatch the project on its **next poll** (default every
  5 min) — provided its master is `queued`/`active` and not `supervised_only`.
- To dispatch **now**, run `/ilk-run` (it launches directly and bypasses the
  blacklist anyway).

## Boundary

- This command does **not** launch, stop, or modify any loop process — it only
  writes the ack sentinel. Redirect launch/stop to `/ilk-run` / `/ilk-stop`.
- Only write the ack when the blocker is genuinely fixed. The ack is a human
  vouching that the fix lands after the failing run; writing it on an unfixed
  project just sends the loop back into the same wall.
