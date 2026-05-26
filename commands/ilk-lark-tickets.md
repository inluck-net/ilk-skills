Plan a new batch of work for the ilk loop, sourced from `可执行` tickets
in this project's configured Lark Bitable.

This is the Lark-specific input adapter — it pulls triaged-but-unplanned
tickets, formats them as a task description, and delegates to the
`/ilk-plan` core workflow. Then it does a Lark-specific post-step:
update each ticket with its `关联 plan` URL and transition to `计划中`.

> **Prerequisite**: this command depends on a separate `lark-tickets`
> skill (the Bitable adapter) being installed at
> `<skill-root>/lark-tickets/`. That skill is **not** part of
> `ilk-skills`. Skip this command and use plain `/ilk-plan` if you
> don't have a Lark Bitable to read from.

Follow these steps in order. Do NOT skip the user-approval gate.

## 1. Load conventions

Read in parallel:
- `<skill-root>/ilk-loop/SKILL.md` (workflow #6: "Generate plans
  from Lark tickets")
- `<skill-root>/lark-tickets/SKILL.md` (CLI usage, ticket fields)

## 2. Verify project context

- Walk up from cwd to find `.lark-project` (this resolves which Bitable
  to query). If not found, tell the user to either `cd` into a Lark-aware
  project or run `/ilk-plan` directly with a free-text description.
- Walk up from cwd to find a project root (`.git` or `docs/`).
- If `docs/plans/` doesn't exist, scaffold it (see SKILL workflow #2).

## 3. Pull the ticket batch

```powershell
python <skill-root>/lark-tickets/scripts/cli.py list --status 可执行 --limit 100
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
# Save to docs/plans/_fetch_tickets.py (delete after use)
import json, subprocess, sys
from pathlib import Path

CLI = os.path.expanduser(r"<skill-root>/lark-tickets/scripts/cli.py")
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

This becomes the input to step 6 below.

## 6. Delegate to /ilk-plan core workflow

Now follow `/ilk-plan` workflow steps 4-7 (the universal planning
workflow):

- Step 4: Read existing plans (collision avoidance).
- Step 5: **Propose grouping (USER APPROVAL REQUIRED)**.
- Step 6: Write the plan files. The `tickets:` field of each sub-plan's
  front-matter MUST contain the `T-YYYY-NNNN` ids that landed in it.
- Step 7: Commit and push.

Do not skip user approval. Iterate on grouping until the user confirms.

## 7. Lark post-step: update tickets

After the plan files are committed AND pushed, update every ticket in
the batch:

For each `(ticket_id, record_id)`:
1. Determine which sub-plan file the ticket landed in by reading each
   sub-plan's `tickets:` front-matter list.
2. Compute the Gitee blob URL:
   ```
   <gitee_blob_base>/docs/plans/<sub-plan-filename>
   ```
   Where `<gitee_blob_base>` is derived from `git remote get-url origin`
   (strip trailing `.git`, append `/blob/<branch>`).
3. Update the ticket via `cli.py`.

For batches of 10+ tickets, write a one-off helper:

```python
# Save to docs/plans/_update_tickets.py (delete after use)
import subprocess, sys
CLI = os.path.expanduser(r"<skill-root>/lark-tickets/scripts/cli.py")
GITEE_BASE = "https://gitee.com/<org>/<repo>/blob/<branch>/docs/plans"

ROUTING = [
    # (ticket_id, record_id, sub-plan-filename) tuples
]

failures = []
for tid, rid, plan in ROUTING:
    url = f"{GITEE_BASE}/{plan}"
    p = subprocess.run(
        [sys.executable, CLI, "update", rid,
         "--field", f"关联 plan={url}",
         "--field", "状态=计划中"],
        capture_output=True, text=True, encoding="utf-8")
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
- Delete the temporary helper scripts (`_fetch_tickets.py`,
  `_tickets_dump.json`, `_update_tickets.py`) — they should not be
  committed.

## 9. Final report

End your turn with:

1. Summary: "Pulled N 可执行 tickets, grouped into M sub-plans, plans
   pushed, all N tickets updated in Lark to 计划中."
2. The output of
   `python "<skill-root>/ilk-loop/scripts/loop_status.py"`.
3. "Ready to execute. Open a fresh chat and type `/ilk`."

## Boundary rules

- **Never skip the user-approval gate** in step 6 (grouping is subjective).
- **Never update Lark tickets before plans are pushed** — the Gitee URLs
  must resolve when the user clicks them from Lark.
- **Always clean up helper scripts** in step 8.
- **If push fails**: do NOT update Lark; tell the user, stop.
