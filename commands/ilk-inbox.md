Plan a new batch of work for the ilk loop, sourced from eligible `pending`
entries in the cross-project handoffs inbox (`~/Documents/handoffs/_inbox.md`).

This is the **cross-project fan-out** — the primary consumption mode. It
pulls all eligible pending entries, groups them by resolved project, and
for each project generates an execution plan via `/ilk-plan`. Then it
writes back status + plan references into the inbox entries.

The per-project pull (cd into a repo, no `--all`) is the filtered special
case — see "Per-project special case" at the end.

> **Prerequisite**: this command depends on a separate `ilk-inbox-tickets`
> skill (the inbox adapter) being installed at
> `<skill-root>/ilk-inbox-tickets/`. That skill is **not** part of
> `ilk-loop`. Skip this command and use plain `/ilk-plan` if you don't
> have an inbox to read from.

Follow these steps in order. Do NOT skip the user-approval gate.

## 1. Load conventions

Read in parallel:
- `<skill-root>/ilk-loop/SKILL.md` (workflow #5: "Generate plans from a
  task description")
- `<skill-root>/ilk-inbox-tickets/SKILL.md` (CLI usage, entry fields,
  eligibility predicate, project registry)

## 2. Verify project context

- Walk up from cwd to find a project root (`.git` or `docs/`).
- If `docs/plans/` doesn't exist, scaffold it (see SKILL workflow #2).

## 3. Registry precheck — resolve project mappings

Run the needs-mapping report:

```bash
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py resolve
```

- If there are unresolved entries, tell the user which `**Project**:`
  strings need mapping in `~/.ilk-data/inbox-projects.json`.
- Ask the user to provide repo roots or mark entries as `not_plannable`.
- Update the registry and re-run `resolve` to confirm all clear.
- If the user declines to map some entries, those are silently excluded
  from planning (they remain `pending` in the inbox for later).

## 4. Pull the eligible batch

```bash
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py list --all --json
```

If the result is empty, tell the user "No eligible pending entries to
plan." and STOP.

If non-empty:
- Parse the JSON output — each key is a repo-root path, each value is a
  list of entry objects (`slug`, `date`, `fields`).
- Briefly summarise to the user: "Found N eligible entries across M
  projects. Fetching full content..."

## 5. Fetch full entry content

For each entry in the batch, fetch full content:

```bash
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py show <slug>
```

For batches of >5 entries, parallelise. Each `show` returns:
`slug`, `date`, `fields` (all parsed `**Key**:` values), `status` (parsed
state + remaining), `body` (raw block text), and optionally
`related_handoff` + `related_handoff_content`.

## 6. Format as task description

Build a structured markdown task description, one section per project
group. Within each group, one subsection per entry:

```markdown
## Project: <slug> → <repo-root>

### <entry-slug> — <date>
- Type: <task | bug | ...>
- Priority: <P0 | P1 | P2 | P3>
- Scope: <**Scope** value>
- Status: <parsed state> <remaining scope if partial>
- Body: <trimmed body or "see inbox entry">
```

If an entry has a `related_handoff` that exists, include its content as
an appendix under that entry's section.

This becomes the input to step 7 below.

## 7. Per-project: delegate to /ilk-plan core workflow

For **each project group**, follow `/ilk-plan` workflow steps 4-7 (the
universal planning workflow):

- Step 4: Read existing plans in that project's `docs/plans/` (collision
  avoidance). **`cd` into the project repo first** — all file operations
  target that repo.
- Step 5: **Propose grouping (USER APPROVAL REQUIRED)**. Show the user
  which entries land in which sub-plans for this project.
- Step 6: Write the plan files. The `tickets:` field of each sub-plan's
  front-matter contains the inbox entry slugs that landed in it.
- Step 7: Commit and push.

Do not skip user approval. Iterate on grouping until the user confirms.

After each project's plan is written and pushed, update the inbox entries:

```bash
# For each entry slug that landed in this project's plan:
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py update <slug> \
  --status in-progress \
  --plan "docs/plans/<sub-plan-filename>"
```

This writes back `**Status**: in-progress` and `**Plan**: <path>` into
the inbox entry.

## 8. Feed /ilk-schedule

After all projects are planned, tell the user:

> "All planned. Run `/ilk-schedule` to start draining the sub-plans, or
> open a fresh chat and type `/ilk` to execute the first step."

## 9. Final report

End your turn with:

1. Summary: "Pulled N eligible inbox entries across M projects, grouped
   into K sub-plans, plans pushed, all N entries updated to in-progress."
2. The output of
   `python3 "<skill-root>/ilk-loop/scripts/loop_status.py"`.
3. "Ready to execute. Open a fresh chat and type `/ilk`."

## Boundary rules

- **Never skip the user-approval gate** in step 7 (grouping is subjective).
- **Never update inbox entries before plans are pushed** — the plan paths
  must resolve when the user reads the inbox.
- **Always run `resolve` first** (step 3) — unmapped projects crash the
  fan-out.
- **If push fails for any project**: do NOT update that project's inbox
  entries; tell the user, continue with other projects if possible.
- **Each project is independent** — a failure in one project's planning
  does not block others.

## Per-project special case

When the user is inside a specific project repo and wants only that
project's entries (no `--all`):

1. `cd` into the target repo (or confirm cwd is inside one).
2. Run `cli.py list` (auto-detects project from cwd via the registry).
3. Skip the cross-project grouping — format + delegate to `/ilk-plan`
   for just that project's entries.
4. Everything else (user-approval gate, update, report) is the same.
