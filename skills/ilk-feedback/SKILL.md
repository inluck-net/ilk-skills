---
name: ilk-feedback
description: >-
  Postmortem for the most recent ilk-loop run. Reads the JSONL summary
  + per-iteration logs `run_ilk_loop_claude.ps1` writes, classifies the
  outcome (8 taxonomy labels), recommends next-launch params, saves
  the report under `~/.ilk-data/projects/<key>/runtime/launcher/postmortems/`. Triggers:
  "/ilk-feedback", "postmortem", "debrief", "what went wrong", "why
  did ilk stop", "复盘", "ilk 反馈", "ilk 怎么停了", or after a ilk
  window exits. Works across Cursor, Claude Code, and Codex.
metadata:
  preferred_model: haiku
---

# ilk-feedback — postmortem skill for ilk-loop runs

A read-only triage skill that turns the structured logs already produced by
`run_ilk_loop_claude.ps1` into:

1. A **classification** of how the run ended (one of 8 taxonomy labels).
2. **Parameter recommendations** for the next launch (consumed by
   `ilk-launcher` Step 1.5).
3. A **markdown report** persisted to disk for trend analysis.
4. A **chat summary** with a 3-way choice — (a) resume / (b) bump-then-resume
   / (c) fix code first.

## When to use

- The user asks for a postmortem, debrief, or "what went wrong" about a
  recent ilk run. Common phrasings (English + 中文):
  - English: `postmortem`, `postmortem on <project>`, `debrief`,
    `debrief the last run`, `ilk debrief`, `do a postmortem`,
    `why did ilk stop`, `why did <project> stop`, `what went wrong`,
    `what went wrong with ilk`, `what happened on the last run`,
    `feedback on the last run`, `/ilk-feedback`
  - 中文：`复盘`、`复盘一下`、`复盘 ilk`、`ilk 反馈`、
    `为什么 ilk 中断`、`ilk 怎么停了`
  - Codex: "postmortem the last run", "why did the loop stop",
    "ilk feedback"
- A ilk window just exited (clean or otherwise) and the user wants
  feedback before re-launching.
- The user wants the agent to consider history before launching ilk
  again — `ilk-launcher` Step 1.5 invokes this implicitly by reading
  postmortem frontmatter.

## Architecture

`<skill-root>` below means the installed skills base directory —
`~/.cursor/skills/` (Cursor), `~/.claude/skills/` (Claude Code), or
`~/.codex/skills/` (Codex) — depending on the host agent.

```
<skill-root>/ilk-feedback/
  SKILL.md
  scripts/
    collect.py            ← reads JSONL + per-iter logs, writes report

~/.ilk-data/projects/<key>/runtime/launcher/postmortems/
  <run-id>.md             ← one report per run, frontmatter-headed for cheap reading
```

A postmortem report header looks like:

```yaml
---
project: es_api
run_id: 20260523-110800
classification: timeout-bound
recommended_max_iterations: 30
recommended_iteration_timeout_min: 45
iterations: 12
iterations_max: 30
new_commits_total: 8
total_elapsed_sec: 12240
generated_at: 2026-05-23T14:32:00
---
```

The `recommended_*` fields are what `ilk-launcher` Step 1.5 reads.

## Taxonomy

| Label | Trigger condition | What it means |
|---|---|---|
| `clean-success` | `loop_status.py` exit 0 after run | All sub-plans shipped. |
| `max-iter-bound` | iteration count == MaxIterations AND not all-shipped | Ran out of iterations. Bump `MaxIterations` or break sub-plan smaller. |
| `timeout-bound` | last iter's `stop_reason="timeout"` | An iter hit `IterationTimeoutMin`. Bump timeout. |
| `api-flaky` | exit_code != 0 in ≥30% of iters BUT progress made | Endpoint unstable but loop survived. Watch; consider model/endpoint switch. |
| `api-blocked` | last iter's `stop_reason="no-progress"` AND ≥2 exit_code != 0 in last 3 iters | API errors stalled the loop. Triage endpoint/credentials. |
| `stuck-no-progress` | last iter's `stop_reason="no-progress"` AND exit_code mostly 0 | Agent stuck — sub-plan ambiguity, prompt issue, or hit a real bug. Read the tail. |
| `budget-exhausted` | last iter's `stop_reason="budget-exhausted"` | `--max-budget-usd` cap. Raise it or accept the cap. |
| `interrupted` | last record's `stop_reason=null` AND not `clean-success` AND iter count < MaxIterations | Window was killed externally (chad ran `stop.ps1` or closed window). |
| `local-checks-stuck` | last iter's `local_checks` had ≥1 fail AND ≥3 of last 5 iters had failing checks (and fail iters > pass iters) | Agent kept committing but sub-plan `local_checks` kept failing — AC may be wrong/over-specified, step too coarse, or a real bug. Read the failing check output before relaunching. Only fires when loop ran with `-RunLocalChecks`. |

## Standard workflow

### W1. Generate postmortem for most recent run

```powershell
python "$HOME\.cursor\skills\ilk-feedback\scripts\collect.py" -ProjectName es_api
# or by path
python "$HOME\.cursor\skills\ilk-feedback\scripts\collect.py" -ProjectPath C:\path\to\your\project
# or cwd walk-up (same resolution as launcher)
python "$HOME\.cursor\skills\ilk-feedback\scripts\collect.py"
```

Equivalent bash invocation:

```bash
python3 "$HOME/.cursor/skills/ilk-feedback/scripts/collect.py" --project-name es_api
# or by path
python3 "$HOME/.cursor/skills/ilk-feedback/scripts/collect.py" --project-path /path/to/your/project
# or cwd walk-up (same resolution as launcher)
python3 "$HOME/.cursor/skills/ilk-feedback/scripts/collect.py"
```

The script writes `~/.ilk-data/projects/<key>/runtime/launcher/postmortems/<run-id>.md` and
prints a 1-paragraph summary to stdout (classification + recommendations
+ report path).

### W2. Look at a specific older run

```powershell
python "$HOME\.cursor\skills\ilk-feedback\scripts\collect.py" -ProjectName es_api -RunId 20260523-110800
```

Equivalent bash invocation:

```bash
python3 "$HOME/.cursor/skills/ilk-feedback/scripts/collect.py" --project-name es_api --run-id 20260523-110800
```

Useful for re-classifying once a heuristic improves, or for chad reading
through 3-day-old runs.

## When the agent invokes this skill

1. Identify the project (cwd walk-up / -ProjectName / -ProjectPath, same
   as launcher).
2. Run `collect.py` with that project. It does all the heavy lifting.
3. Read the generated postmortem markdown file (path printed to stdout).
4. Summarise in chat:
   - Classification + 1-sentence "what happened"
   - Key metrics (iters used, elapsed, commits, transient errors)
   - Recommended params for next run
   - **Tail of last problematic iter (≤40 lines)** so the user can
     eyeball the actual error
5. Ask the user via `AskQuestion` (3 options):
   - **(a) Resume now** with the recommended params
   - **(b) Investigate the tail** (open the iter log file or dive into
     a suspicious commit before resuming)
   - **(c) I'll handle it** (no further action — user will decide
     manually)
6. Act on their answer:
   - (a) → call `ilk-launcher`'s `launch.ps1` / `launch.sh` with the
     recommended `MaxIterations` / `IterationTimeoutMin`. Report what you launched.
   - (b) → open the relevant log file in the editor; do NOT auto-launch.
   - (c) → end your turn with the report path so the user can come back.

## Boundary rules

- **Read-only with respect to the running loop.** Never modify `.ilk-loop.log`,
  PID files, or sub-plan front-matter. Only writes new files in
  `~/.ilk-data/projects/<key>/runtime/launcher/postmortems/`.
- **No L2 auto-improvement.** This skill never modifies `ilk-launcher`
  SKILL.md, `ilk-loop` SKILL.md, sub-plan templates, or any heuristic
  documentation. Trends are surfaced for humans to act on, not auto-applied.
- **Single-run scope by default.** Cross-run trend analysis is a future
  separate skill (`ilk-trends`?), gated on having ≥10 postmortems for
  a project.
- **Don't fabricate fields.** If a JSONL record is missing a field
  (e.g., older format), report "unknown" rather than guessing.

## Cross-platform notes

Both `run_ilk_loop_claude.ps1` (Windows) and `run_ilk_loop_claude.sh`
(macOS/Linux) write iteration records to the project-level JSONL log at
`~/.ilk-data/projects/<key>/logs/.ilk-loop.log` (resolved via
`last-launch.json.jsonl_log`). `collect.py` reads this file, falling
back to legacy `<skill-root>/ilk-loop/logs/.ilk-loop.log` for older runs.

Both `launch.ps1` and `launch.sh` write `last-launch.json` to the same
external launcher dir (`~/.ilk-data/projects/<key>/runtime/launcher/`).

Project-path comparison is normalized via `_normalize_path_for_compare`
(lowercase + collapse separators to forward slashes) so a postmortem
requested on macOS can still classify a run that was launched on Windows
(and vice-versa).

Invocation on each platform:

- **PowerShell:** `python "<skill-root>\ilk-feedback\scripts\collect.py"`
- **bash:** `python3 "<skill-root>/ilk-feedback/scripts/collect.py"`

## Known limitations

- **Doesn't catch quality-gate stops** (P0-3 future work in ilk-loop).
  When `Invoke-QualityGatesIfNeeded` blocks, the loop stop reason isn't
  in JSONL. Will be added when P0-3 lands.
- **Multiple runs same minute**: run_id collisions are theoretically
  possible (two launches in the same second). In practice this hasn't
  happened; if it does, `--run-id` lets you disambiguate.

## Relationship to other skills

| Concern | Owner |
|---|---|
| Writing per-iteration JSONL + text logs | `run_ilk_loop_claude.ps1` / `run_ilk_loop_claude.sh` (in `ilk-loop`) |
| Writing `last-launch.json` (run start metadata) | `ilk-launcher/launch.ps1` / `launch.sh` |
| **Reading those logs and classifying the run** | **`ilk-feedback`** (this skill) |
| Writing postmortem markdown | **`ilk-feedback`** |
| Step 1.5 of launching: read latest postmortems | `ilk-launcher` (consumes our frontmatter) |
| Modifying heuristics / SKILL.md | **Not any agent skill — humans only.** |

## See also

- `<skill-root>/ilk-launcher/SKILL.md` — Step 1.5 reads the
  frontmatter we emit.
- `<skill-root>/ilk-loop/scripts/run_ilk_loop_claude.ps1` — the
  source of truth for JSONL log format (PowerShell runner; bash
  equivalent is `run_ilk_loop_claude.sh`).
- `<vault>/ai-coding-workflow/tool-evaluations/ilk-launcher.md` —
  design rationale (incl. why "auto-improvement" is not in v0).
