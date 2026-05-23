Resume the ilk loop on the project rooted above this chat's cwd.

Follow these steps in order. Do NOT skip any.

## 1. Status check (always first)

Run:

```powershell
python "$HOME\.cursor\skills\ilk-loop\scripts\loop_status.py"
```

The script walks up from cwd to find `docs/plans/MASTER-*.md` and reports
each sub-plan's status. Read its output carefully.

- **Exit code 0** → all sub-plans are `shipped`. Tell the user
  "All sub-plans shipped — nothing to do." and STOP. Do not load any
  plan files.
- **Exit code 1** → there is a next pending sub-plan. The script printed
  its filename and full path. Continue to step 2.
- **Exit code 2** → no plans dir found. Tell the user to `cd` into a
  project that has `docs/plans/MASTER-*.md`. STOP.

## 2. Load the loop convention

Read `~/.cursor/skills/ilk-loop/SKILL.md` so you know the front-matter
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
   `[plan:<slug>#step-<N>]`.
3. Bump the sub-plan's `current_step` in front-matter, save, commit:
   `chore(plans): bump <slug> current_step to <N+1>`.
4. If a step uncovers a new bug: file a new ticket via the lark-tickets
   skill, add a one-line note under "Out of scope" in the current sub-plan.
   Do NOT silently expand the plan.

### Running tests / long commands during a step

**Always follow the `long-running-commands` skill** when invoking
`python manage.py test`, `pytest`, builds, migrations, or any command
expected to take more than ~10s.

Specifically:

1. **Read `~/.cursor/skills/long-running-commands/SKILL.md` first** if you
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

## 6. Boundary: stop and hand back

Stop and hand back to the human when ANY of these is true:

- The sub-plan's `current_step` reaches `estimated_steps`. Then:
  1. Set `status: shipped` and update `last_updated` in front-matter.
  2. For every Lark ticket in the sub-plan's `tickets:` list, transition
     to `待验证` and write the relevant commit short-hashes into the
     `关联 commit` field — use the lark-tickets skill.
  3. Commit: `chore(plans): <slug> shipped [plan:<slug>#ship]`.
- Context starts feeling heavy (many large file reads, repeated re-reads).
- A step blocks on something only the human can do (external auth,
  manual review, ambiguous requirement).
- An unexpected new bug surfaces and you've filed a ticket for it.

## 7. Final report

Before ending your turn, run `loop_status.py` again and paste its output
in your last message so the human sees up-to-date state.

If the loop is now fully shipped, congratulate the user.
If not, tell them they can start a fresh chat and type `/ilk` again to
continue.
