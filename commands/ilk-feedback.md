Generate a postmortem for the most recent ilk-loop run on this project,
then surface a recommended next step (resume / bump param / investigate
first).

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
