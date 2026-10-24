Plan a new batch of work for an ilk2 project, sourced from `可执行` tickets
in this project's configured Lark Bitable.

This is the Lark-specific input adapter — it pulls triaged-but-unplanned
tickets, formats them as a task description, and hands them to
`/ilk2-plan`. Then it does a Lark-specific post-step: update each ticket
with a `关联 plan` value that names the **ilk2 batch id**, and transition
to `计划中`.

This is the **ilk2-native** Lark planner, built beside `/ilk-lark` until
the switch (docs/07 §7.4.2). Ticket handling is unchanged — the
`ilk-lark-tickets` adapter still reads and writes the Bitable. What
changes is the planning and delivery: the tickets go to `/ilk2-plan`,
drafts land under `~/.ilk2/plans/`, and delivery is `ilk2 submit`. No V1
paths or scripts, nothing scaffolded or committed in the project repo.

> **Renamed** from `/ilk-lark-tickets` to avoid the Claude Code
> skill/command slash-name clash; the Bitable skill is still
> `ilk-lark-tickets`.

> **Prerequisite**: this command depends on a separate `ilk-lark-tickets`
> skill (the Bitable adapter) being installed at
> `<skill-root>/ilk-lark-tickets/`. That skill is **not** part of
> `ilk-skills`. Skip this command and use plain `/ilk2-plan` if you
> don't have a Lark Bitable to read from.

Follow these steps in order. Do NOT skip the user-approval gate — it lives
in `/ilk2-plan` step 5, so hand off without `--yes`.

## Paths

`<skill-root>` is this clone's `skills/` directory — the one beside the
`commands/` directory that holds this file's real path. It is never V1's
`~/.ilk/current/skills`.

Resolve that real path through whichever agent's link exists, first hit
wins: `~/.claude/commands/ilk2-lark.md`, `~/.codex/commands/ilk2-lark.md`,
`~/.cursor/commands/ilk2-lark.md`. With none of them installed the block
exits non-zero, naming the paths it tried.

```bash
SKILL_ROOT=""
for _p in \
  "$HOME/.claude/commands/ilk2-lark.md" \
  "$HOME/.codex/commands/ilk2-lark.md" \
  "$HOME/.cursor/commands/ilk2-lark.md"
do
  [ -e "$_p" ] || continue
  SKILL_ROOT="$(cd "$(dirname "$(realpath "$_p")")/../skills" && pwd)"
  if [ -n "$SKILL_ROOT" ]; then break; fi
done
if [ -z "$SKILL_ROOT" ]; then
  echo "error: cannot resolve SKILL_ROOT for ilk2-lark; tried:" >&2
  echo "  $HOME/.claude/commands/ilk2-lark.md" >&2
  echo "  $HOME/.codex/commands/ilk2-lark.md" >&2
  echo "  $HOME/.cursor/commands/ilk2-lark.md" >&2
  exit 1
fi
```

## 1. Load conventions

Read in parallel:
- `<skill-root>/ilk-lark-tickets/SKILL.md` (CLI usage, ticket fields)
- `/ilk2-plan` (`commands/ilk2-plan.md`) — the planner this command hands
  off to. It loads the planning conventions itself and owns project
  lookup, the drafts dir, grouping, QC and delivery.

## 2. Verify project context

- Walk up from cwd to find `.lark-project` (this resolves which Bitable
  to query). If not found, tell the user to either `cd` into a Lark-aware
  project or run `/ilk2-plan` directly with a free-text description.
- **Plans live OUTSIDE the project repo**, so the project's git history
  stays clean of plan artifacts. `/ilk2-plan` looks the project up in
  ilk2's registry and drafts under `~/.ilk2/plans/<project>/<date>/`.
  Do NOT scaffold or write an in-tree plans folder, and do NOT commit
  anything to the project repo.

  > **Why this matters** — an older version of this command scaffolded an
  > in-tree plans folder and committed plans there; the loop never reads
  > that path, so it produced a divergent double-write (in-tree copy +
  > external copy in different schemas) and polluted the project's git
  > history. Write only where `/ilk2-plan` writes.

## 3. Pull the ticket batch

```bash
python <skill-root>/ilk-lark-tickets/scripts/cli.py list --status 可执行 --limit 100
```

If the result is empty, tell the user "No 可执行 tickets to plan." and STOP.

If non-empty:
- Cache the list as `(ticket_id, record_id, title)` tuples — you will need
  this for step 7.
- Briefly summarise to the user: "Found N 可执行 tickets. Fetching full
  content..."

## 4. Fetch full ticket content

For each ticket in the batch, fetch full content. For batches of >5
tickets, write a one-off helper script to parallelise:

```python
# Save to a scratch/tmp dir (NOT the repo, NOT the drafts dir under
# ~/.ilk2/plans/) and delete after use
import json, os, subprocess, sys
from pathlib import Path

CLI = os.path.expanduser(r"<skill-root>/ilk-lark-tickets/scripts/cli.py")
RECORDS = [
    # paste (ticket_id, record_id) tuples from step 3
]

def fetch(rid):
    p = subprocess.run([sys.executable, CLI, "show", rid],
                       capture_output=True, text=True, encoding="utf-8")
    return json.loads(p.stdout) if p.returncode == 0 else {"error": p.stderr}

out = {tid: {"record_id": rid, "data": fetch(rid)} for tid, rid in RECORDS}
Path(__file__).with_name("_tickets_dump.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
```

Run with the project's `.venv` activated (per the project's python rules).

## 5. Format as task description

Build a structured markdown task description, one section per ticket:

```markdown
### T-YYYY-NNNN — <title>
- Type: <bug | 新功能 | 体验优化>
- Priority: <P0 | P1 | P2 | P3>  (urgency: <客户标的紧急度>)
- Modules: <comma-separated>
- Original: "<原文描述>"
- Understanding: <AI 理解>
```

This becomes the task description for step 6 below.

## 6. Hand off to /ilk2-plan

Hand the step-5 task description to `/ilk2-plan`. It:

- looks the project up in ilk2's registry by repo root, and stops with
  `ilk2 project add` if it is not registered (this command never
  registers one);
- drafts a MASTER plus one file per sub-plan under
  `~/.ilk2/plans/<project>/<date>/`. The `tickets:` field of each
  sub-plan's front-matter MUST contain the `T-YYYY-NNNN` ids that landed
  in it. Match the on-disk master/sub-plan schema already present in that
  drafts dir (read an existing file there first);
- runs its step-5 grouping approval — **do not pass `--yes`**;
- runs its step-7 QC, then delivers with `ilk2 submit --check` and
  `ilk2 submit --project <project> …`, which prints the batch id. That
  submit passes `--notify-to concord:<agent id>` under the same rule as
  commands/ilk2-plan.md step 8b.

There is **no project-repo commit** — the drafts live outside the repo and
`ilk2 submit` is the delivery. Do not skip user approval. Iterate on
grouping until the user confirms.

## 7. Lark post-step: update tickets

After the batch is submitted (step 6 / `/ilk2-plan` step 8b — `ilk2
submit` has printed the batch id), update every ticket in the batch.

The `关联 plan` value **names the ilk2 batch id** — that is what names the
work in ilk2, and there is no in-repo file URL to link to. Optionally
append the sub-plan's `[plan:<slug>]` commit-search URL, which resolves to
the real commits as the loop lands them (empty until then).

For each `(ticket_id, record_id)`:
1. Determine which sub-plan the ticket landed in by reading each sub-plan's
   `tickets:` front-matter list; note its `plan:` slug.
2. Build the `关联 plan` value: `ilk2 batch <batch-id>`, and, when a
   browsable link is wanted, the commit-search URL for that slug:
   - derive `web_base` from `git remote get-url origin` (strip trailing
     `.git`; normalize `git@host:org/repo` → `https://host/org/repo`);
   - GitHub: `<web_base>/search?q=%5Bplan%3A<slug>%5D&type=commits`
   - Gitee: `<web_base>/commits/<branch>` (Gitee lacks reliable message
     search — link the branch commits page and rely on the
     `[plan:<slug>]` trailer being greppable).
3. Update the ticket via `cli.py update <record_id>`: set
   `关联 plan=<value>` and `状态=计划中`.

For batches of 10+ tickets, write a one-off helper **in a scratch/tmp dir
(never the repo)**, using `--fields-json` so non-ASCII values bypass argv:

```python
# scratch dir only — delete after use
import json, os, subprocess, sys, tempfile
CLI = os.path.expanduser(r"<skill-root>/ilk-lark-tickets/scripts/cli.py")

ROUTING = [
    # (ticket_id, record_id, plan_value) tuples
]

failures = []
for tid, rid, val in ROUTING:
    fj = os.path.join(tempfile.gettempdir(), f"_lark_{tid}.json")
    with open(fj, "w", encoding="utf-8") as f:
        json.dump({"关联 plan": val, "状态": "计划中"}, f, ensure_ascii=False)
    p = subprocess.run(
        [sys.executable, CLI, "update", rid, "--fields-json", fj],
        capture_output=True, text=True, encoding="utf-8")
    os.remove(fj)
    if p.returncode != 0:
        failures.append((tid, p.stderr.strip()))
    print(f"{'ok ' if p.returncode == 0 else 'FAIL'}: {tid}")

if failures:
    print(f"\n{len(failures)} failed:")
    for tid, msg in failures:
        print(f"  {tid}: {msg}")
sys.exit(1 if failures else 0)
```

## 8. Verify and clean up

- Run `cli.py list --status 计划中 --limit 100` and confirm the count
  matches the batch size.
- Delete any temporary helper scripts from the scratch/tmp dir — they must
  not land in the repo or in the `~/.ilk2/plans/` drafts dir.

## 9. Final report

End your turn with:

1. Summary: "Pulled N 可执行 tickets, grouped into M sub-plans, submitted
   as ilk2 batch `<id>`, all N tickets updated in Lark to 计划中."
2. The output of:

   ```bash
   ilk2 status --batch <id>
   ```

   so the user sees the batch and its plans' pending state.
3. "Ready to execute. ilk2's supervisor picks the batch up — watch it with
   `ilk2 status --batch <id>`."

## Boundary rules

- **Never skip the user-approval gate** in `/ilk2-plan` step 5 (grouping
  is subjective). Hand off without `--yes`.
- **Plans live outside the repo — never commit them to the project repo.**
  `/ilk2-plan` writes to `~/.ilk2/plans/<project>/<date>/`. Do not
  scaffold or commit an in-tree plans folder.
- **Never update Lark tickets before the batch is submitted** (step 6 /
  `/ilk2-plan` step 8b complete — `ilk2 submit` printed the batch id).
- **The `关联 plan` value names the ilk2 batch id** (optionally with a
  `[plan:<slug>]` commit-search URL) — never an in-repo file URL.
- **Always clean up helper scripts** (scratch/tmp only) in step 8.
