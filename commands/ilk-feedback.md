Generate a postmortem for the most recent ilk-loop run on this project,
then surface a recommended next step (resume / bump param / investigate
first).

Log discovery is external-first: `collect.py` reads from
`~/.ilk-data/projects/<key>/logs/` (canonical), then falls back to
legacy `<skill-root>/ilk-loop/logs/` for older runs. A preserved
archive at `~/.ilk-data/projects/<key>/logs/archive/<run-id>/` is
also checked when the original log path no longer exists (common in
self-hosting projects where the skill repo modified its own paths).

Invoke the `ilk-feedback` skill — its `SKILL.md` has the full workflow.
Do NOT re-implement the logic here. Specifically:

1. Resolve the project (cwd walk-up via `ilk_paths.find_project_root()`,
   or `-ProjectName` / `-ProjectPath` if the user passed one).
2. Run `python "<skill-root>/ilk-feedback/scripts/collect.py"`
   with the resolved project. The script writes
   `~/.ilk-data/projects/<key>/runtime/launcher/postmortems/<run-id>.md`
   and prints a one-paragraph summary.
3. Read the generated markdown file. Render in chat:
   - **Classification** (one of the 8 taxonomy labels) + one-sentence
     "what happened"
   - Key metrics: iters used, elapsed time, new commits total, transient
     error count
   - **Recommended params** for next launch
   - **Tail of the last problematic iter** (≤40 lines) so the user can
     eyeball the actual error
4. Offer a 3-way choice via `AskUserQuestion`:
   - **Resume now** with the recommended params (call `launch.ps1`)
   - **Investigate the tail** (open the iter log file; do NOT auto-launch)
   - **I'll handle it** (end turn, report path)

If the project has no postmortem-eligible run (no JSONL records, no
`~/.ilk-data/projects/<key>/runtime/launcher/last-launch.json`), say so and stop — do not invent.

## Resolve-ack: un-park the scheduler when you fixed the blocker

When the classification is a **blacklist class** (`stuck-no-progress`,
`api-blocked`, `budget-exhausted`, `local-checks-stuck`,
`dependency-unreachable`) the scheduler parks the project for a 60-minute
backoff. If — **in this same session** — you took a concrete resolving action
(fixed the env/config, added a missing worker MCP, reconciled plan/git, raised
the budget, etc.), write a **resolve-ack** so the scheduler can dispatch it
immediately instead of waiting out the backoff:

```bash
# macOS / Linux
python3 "<skill-root>/ilk-watchdog/scripts/blacklist_status.py" ack --project "<project-data-dir>"
```

```powershell
# Windows
python "<skill-root>\ilk-watchdog\scripts\blacklist_status.py" ack --project "<project-data-dir>"
```

(`<project-data-dir>` is `…/projects/<key>`, the parent of `external_runtime_dir`
from `ilk_paths.py`. The same thing `/ilk-resume` does.) The blacklist decision
then returns `resolved-by-ack` because the ack's `cleared_at` is after the
failing run's `generated_at`.

**Gate this on an actual fix.** Never write the ack just because a postmortem
was generated — only when the blocker is genuinely resolved this session.
Otherwise leave it parked (the backoff or a later `/ilk-resume` applies) — see
also decomposition-principles §"Degrade-to-default over block".
