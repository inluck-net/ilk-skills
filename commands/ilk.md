Resume the ilk loop on the project rooted above this chat's cwd.

Follow these steps in order. Do NOT skip any.

## 1. Status check (always first)

Run:

```bash
# macOS / Linux
python3 "<skill-root>/ilk-loop/scripts/loop_status.py"
```

```powershell
# Windows — use python, not python3; or run ilk-status.ps1
python "<skill-root>\ilk-loop\scripts\loop_status.py"
```

The script resolves the active plans dir (external under `~/.ilk-data`
preferred; legacy in-tree as fallback) and reports each sub-plan's
status. Read its output carefully.

- **Exit code 0** → all sub-plans are `shipped`. Tell the user
  "All sub-plans shipped — nothing to do." and STOP. Do not load any
  plan files.
- **Exit code 1** → there is a next pending sub-plan. The script printed
  its filename and full path. Continue to step 2.
- **Exit code 2** → no plans dir found. Tell the user to `cd` into a
  project that has either `.ilk-meta.json` + external plans, or a
  `.git` repo + plans. STOP.

If the output includes a `Repo:` line under "Next", this is a
**meta-project**: each sub-plan targets one member repo and you must
`cd` into that repo (or use `git -C <member-path>`) for all staging,
commits, and pushes in step 5. See SKILL.md → "Meta-projects".

## 2. Load the loop convention

Read `<skill-root>/ilk-loop/SKILL.md` so you know the front-matter
spec, state machine, commit message conventions, and lark-tickets
integration.

## 3. Load context for the next pending sub-plan

Read in this order, all in one batch of parallel tool calls:

1. The MASTER plan (path printed by `loop_status.py` is in `docs/plans/`).
2. The next pending sub-plan (full path printed by `loop_status.py`).
3. Any reference docs explicitly listed in the sub-plan's
   "Reference reading" section.

## 4. Open the chat with a single header line

Your very first user-visible message in this chat MUST be exactly one
line, in this format:

```
Next: <sub-plan-filename> step <N> of <M> — <one-line summary of step>. Starting work...
```

This lets the human glance at the chat header and know what's about to
happen, without scrolling.

## 5. Execute the next step

Execute exactly one step from the sub-plan (the one at `current_step`).
You MAY execute several consecutive steps in the same chat IF you have
clear context capacity AND the steps are tightly related — use judgement.

For each step:

1. Do the work (read code, edit, run tests).
2. Commit with the convention from the sub-plan, message must contain
   `[plan:<slug>#step-<N>]`. **In meta projects, this commit must
   land in the member repo named by the sub-plan's `repo:`
   frontmatter** — `cd <umbrella>/<member>` (or `git -C <member-path>`)
   before staging.
3. Bump the sub-plan's `current_step` in front-matter, save. The
   sub-plan file lives in the external plans dir
   (`~/.ilk-data/projects/<key>/plans/`) — that file edit is NOT a
   commit in any member repo (plans live outside SCM).
4. If a step uncovers a new bug: file a new ticket via the lark-tickets
   skill, add a one-line note under "Out of scope" in the current sub-plan.
   Do NOT silently expand the plan.

### Running tests / long commands during a step

**Always follow the `long-running-commands` skill** when invoking
`python manage.py test`, `pytest`, builds, migrations, or any command
expected to take more than ~10s.

Specifically:

1. **Read `<skill-root>/long-running-commands/SKILL.md` first** if you
   haven't already in this session.
2. **Always run tests with the mandatory flags** from that skill:
   - Django: `--verbosity=2 --noinput --keepdb`
   - Pytest: `--timeout=60 --timeout-method=thread` (install
     `pytest-timeout` once if missing)
3. **Always background long commands** with `block_until_ms: 0`, then poll
   via `Await` with a `pattern` that matches both success and failure
   (e.g. `"(Ran \\d+ tests in|FAILED|ERROR:)"`) plus a hard
   `block_until_ms` upper bound (3 min for unit tests, 10 min for
   integration).
4. **If a command hangs** (no footer + no output change for ≥ 60s + pid
   still alive): apply the hang-detection / kill / diagnose flow from the
   skill. Tree-kill (`taskkill /F /T /PID <pid>`) for test runners. Do NOT
   blindly retry — read the diagnosis table in the skill, fix the cause,
   then re-run.
5. **If a hang repeats** after one diagnosis-and-fix cycle: stop, report
   to the user with the last 30 lines of output and the diagnosis,
   bump the step's notes (NOT the plan) and hand control back. Do not
   loop indefinitely.

## 6. Before setting `status: blocked` — escalation checklist

A blocked status halts the entire loop pending human intervention.
That is expensive: the watchdog stops auto-resuming, the user gets
paged, and any later sub-plans that didn't depend on this one are
also frozen. Before flipping the switch, work through this checklist:

1. **Project preflight / primer.** Look for these files at the project
   root (or under `docs/loop/`) — many "I don't know how to do this"
   blockers are answered there:
   - `docs/loop/PRIMER.md` — project-side loop primer (test accounts,
     seed commands, environment, protected routes)
   - `docs/loop/fixtures-registry.{yml,yaml,json}` — machine-readable
     fixture index; sub-plan `data_prereqs` keys often reference it
   - `AGENTS.md` / `CLAUDE.md` — top-level agent docs
   - If any of these exist and were not in this sub-plan's "Reference
     reading" list, read them now. Many "blocked" turns out to be
     "the agent didn't know the project has a seed command".

2. **MASTER cross-cutting invariants.** Re-read the active MASTER's
   `cross_cutting_invariants` frontmatter. If any invariant has an
   `assert.command` (e.g. `bash docs/loop/preflight.sh`), run it. The
   common pattern: a project preflight script that ensures seed has
   run, MCP servers are connected, and test accounts can log in.
   Running this often unblocks a stuck step.

3. **Try once with the surfaced context.** If the preflight pass +
   primer read filled in what you were missing, attempt the step
   again BEFORE marking blocked.

Only set `status: blocked` when ALL of the following are true:
- The blocker is a real decision / external action only a human can
  resolve (design choice, missing credential not in any docs, broken
  external service, design conflict with implementation, etc.)
- You've explicitly checked the three sources above
- You've written what you tried in the sub-plan's "Findings" section

The `blocked` note in the sub-plan should name what kind of input
unblocks it ("need design decision on X" not "couldn't do step 3").

## 7. Boundary: stop and hand back

Stop and hand back to the human when ANY of these is true:

- The sub-plan's `current_step` reaches `estimated_steps`. Then:
  1. Set `status: shipped` and update `last_updated` in front-matter.
  2. For every Lark ticket in the sub-plan's `tickets:` list, transition
     to `待验证` and write the relevant commit short-hashes into the
     `关联 commit` field — use the lark-tickets skill.
  3. Commit: `chore(plans): <slug> shipped [plan:<slug>#ship]`.
- Context starts feeling heavy (many large file reads, repeated re-reads).
- A step legitimately blocks per the section-6 checklist (you walked
  through the three escalation sources and the blocker is genuinely a
  human-only decision).
- An unexpected new bug surfaces and you've filed a ticket for it.

## 8. Final report

Before ending your turn, run `loop_status.py` again and paste its output
in your last message so the human sees up-to-date state.

If the loop is now fully shipped, congratulate the user.
If not, tell them they can start a fresh chat and type `/ilk` again to
continue.
