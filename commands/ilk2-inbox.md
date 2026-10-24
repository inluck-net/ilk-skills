Plan a new batch of work for an ilk2 project, sourced from eligible `pending`
entries in the cross-project handoffs inbox (`~/Documents/handoffs/_inbox.md`).

This is the **cross-project fan-out** — the primary consumption mode. It
pulls all eligible pending entries, groups them by resolved project, and
for each project hands that group to `/ilk2-plan` as a task description.
Then it writes back status + the **ilk2 batch id** into the inbox entries.

This is the **ilk2-native** inbox planner, built beside `/ilk-inbox` until
the switch (docs/07 §7.4.2). Ticket handling is unchanged — the
`ilk-inbox-tickets` adapter still reads and writes the inbox. What changes
is the planning and delivery: the tickets go to `/ilk2-plan`, drafts land
under `~/.ilk2/plans/`, and delivery is `ilk2 submit`. No V1 paths or
scripts, no in-tree plans folder, no commit-and-push.

The per-project pull (cd into a repo, no `--all`) is the filtered special
case — see "Per-project special case" at the end.

> **Prerequisite**: this command depends on a separate `ilk-inbox-tickets`
> skill (the inbox adapter) being installed at
> `<skill-root>/ilk-inbox-tickets/`. That skill is **not** part of
> `ilk-loop`. Skip this command and use plain `/ilk2-plan` if you don't
> have an inbox to read from.

Follow these steps in order. Do NOT skip the user-approval gate — it lives
in `/ilk2-plan` step 5, so hand off without `--yes`.

## Paths

`<skill-root>` is this clone's `skills/` directory — the one beside the
`commands/` directory that holds this file's real path. It is never V1's
`~/.ilk/current/skills`.

Resolve that real path through whichever agent's link exists, first hit
wins: `~/.claude/commands/ilk2-inbox.md`, `~/.codex/commands/ilk2-inbox.md`,
`~/.cursor/commands/ilk2-inbox.md`. With none of them installed the block
exits non-zero, naming the paths it tried.

```bash
SKILL_ROOT=""
for _p in \
  "$HOME/.claude/commands/ilk2-inbox.md" \
  "$HOME/.codex/commands/ilk2-inbox.md" \
  "$HOME/.cursor/commands/ilk2-inbox.md"
do
  [ -e "$_p" ] || continue
  SKILL_ROOT="$(cd "$(dirname "$(realpath "$_p")")/../skills" && pwd)"
  if [ -n "$SKILL_ROOT" ]; then break; fi
done
if [ -z "$SKILL_ROOT" ]; then
  echo "error: cannot resolve SKILL_ROOT for ilk2-inbox; tried:" >&2
  echo "  $HOME/.claude/commands/ilk2-inbox.md" >&2
  echo "  $HOME/.codex/commands/ilk2-inbox.md" >&2
  echo "  $HOME/.cursor/commands/ilk2-inbox.md" >&2
  exit 1
fi
```

## 1. Load conventions

Read in parallel:
- `<skill-root>/ilk-inbox-tickets/SKILL.md` (CLI usage, entry fields,
  eligibility predicate, project registry)
- `/ilk2-plan` (`commands/ilk2-plan.md`) — the planner this command hands
  off to. It loads the planning conventions itself and owns project
  lookup, the drafts dir, grouping, QC and delivery.

## 2. Verify project context

- Walk up from cwd to find a project root (`.git` or `docs/`).
- **No plans are written here.** `/ilk2-plan` resolves the project in
  ilk2's registry and drafts under `~/.ilk2/plans/<project>/<date>/`.
  This command never scaffolds a plans folder and never writes plan files.

## 3. Registry precheck — resolve project mappings

Run the needs-mapping report:

```bash
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py resolve
```

- If there are unresolved entries, tell the user which `**Project**:`
  strings need mapping in the inbox skill's project registry (path and
  `--registry` override: `ilk-inbox-tickets` SKILL.md).
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

This becomes the task description for step 7 below.

## 7. Per-project: hand off to /ilk2-plan

For **each project group**, `cd` into that project repo first — every
repo-relative operation targets that repo, and `/ilk2-plan` resolves the
project from this repo root. Then invoke `/ilk2-plan` with that group's
step-6 task description. `/ilk2-plan` will:

- look the project up in ilk2's registry by repo root, and stop with
  `ilk2 project add` if it is not registered (this command never
  registers one);
- draft a MASTER plus one file per sub-plan under
  `~/.ilk2/plans/<project>/<date>/`, with each sub-plan's `tickets:`
  front-matter listing the inbox entry slugs that landed in it;
- run its step-5 grouping approval — **do not pass `--yes`**;
- run its step-7 QC, then deliver with `ilk2 submit --check` and
  `ilk2 submit --project <project> …`, which prints the batch id. That
  submit passes `--notify-to concord:<agent id>` under the same rule as
  commands/ilk2-plan.md step 8b.

Keep that batch id. After the batch is submitted, update the inbox entries
that landed in it:

```bash
# For each entry slug that landed in this project's batch:
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py update <slug> \
  --status in-progress \
  --plan "ilk2 batch <batch-id>"
```

This writes back `**Status**: in-progress` and `**Plan**: ilk2 batch
<batch-id>` into the inbox entry. The batch id is what names the work in
ilk2 — there is no repo path to link to, because the drafts live outside
every repo.

## 8. Final report

End your turn with:

1. Summary: "Pulled N eligible inbox entries across M projects, grouped
   into K sub-plans, submitted as M ilk2 batches, all N entries updated
   to in-progress with their batch ids."
2. For each batch id, the output of:

   ```bash
   ilk2 status --batch <id>
   ```

   so the user sees the batch and its plans' pending state.
3. "Ready to execute. ilk2's supervisor picks the batches up — watch them
   with `ilk2 status --batch <id>`."

## Boundary rules

- **Never skip the user-approval gate** in `/ilk2-plan` step 5 (grouping
  is subjective). Hand off without `--yes`.
- **Never update inbox entries before the batch is submitted** — the batch
  id must exist when the user reads the inbox.
- **Always run `resolve` first** (step 3) — unmapped projects crash the
  fan-out.
- **If submission fails for any project**: do NOT update that project's
  inbox entries; tell the user, continue with other projects if possible.
- **Each project is independent** — a failure in one project's planning
  does not block others.
- **Never write plan files from this command** — drafts live under
  `~/.ilk2/plans/` and are written by `/ilk2-plan`. This command never
  commits or pushes anything.

## Per-project special case

When the user is inside a specific project repo and wants only that
project's entries (no `--all`):

1. `cd` into the target repo (or confirm cwd is inside one).
2. Run `cli.py list` (auto-detects project from cwd via the registry).
3. Skip the cross-project grouping — format + hand off to `/ilk2-plan`
   for just that project's entries.
4. Everything else (the grouping gate, the update naming the batch id,
   the report) is the same.
