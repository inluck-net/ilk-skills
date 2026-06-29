Plan a new batch of work for the ilk loop, sourced from `可执行` tickets
in this project's configured Lark Bitable.

This is the Lark-specific input adapter — it pulls triaged-but-unplanned
tickets, formats them as a task description, and delegates to the
`/ilk-plan` core workflow. Then it does a Lark-specific post-step:
update each ticket with its `关联 plan` URL and transition to `计划中`.

> **Renamed** from `/ilk-lark-tickets` to avoid the Claude Code
> skill/command slash-name clash; the Bitable skill is still
> `ilk-lark-tickets`.

> **Prerequisite**: this command depends on a separate `ilk-lark-tickets`
> skill (the Bitable adapter) being installed at
> `<skill-root>/ilk-lark-tickets/`. That skill is **not** part of
> `ilk-skills`. Skip this command and use plain `/ilk-plan` if you
> don't have a Lark Bitable to read from.

Follow these steps in order. Do NOT skip the user-approval gate.

## 1. Load conventions

Read in parallel:
- `<skill-root>/ilk-loop/SKILL.md` (workflow #6: "Generate plans
  from Lark tickets")
- `<skill-root>/ilk-lark-tickets/SKILL.md` (CLI usage, ticket fields)

## 2. Verify project context and resolve the plans dir

- Walk up from cwd to find `.lark-project` (this resolves which Bitable
  to query). If not found, tell the user to either `cd` into a Lark-aware
  project or run `/ilk-plan` directly with a free-text description.
- **Resolve where this project's plans actually live** — exactly as
  `/ilk-plan` step 2 does. Plans live OUTSIDE the project repo (under
  `~/.ilk-data/`) so the project's git history stays clean of skill
  artifacts; do NOT scaffold or write an in-tree `docs/plans/`.

  ```powershell
  python "<skill-root>/ilk-loop/scripts/ilk_paths.py" --start .
  ```

  Capture `project_root`, `project_key`, and `external_plans_dir` from the
  JSON. **All plan files in step 6 are written to `external_plans_dir`**
  (`~/.ilk-data/projects/<key>/plans/`), and `web_base` for the Lark
  link-back (step 7) is derived from `git remote get-url origin`. Bootstrap
  `external_plans_dir` (copy `ilk-loop/templates/README.md` into it) if it
  doesn't exist yet.

> **Why this matters** — `/ilk-lark` delegates planning to `/ilk-plan`,
> which writes to the external dir and does NOT commit to the project repo.
> An older version of this command scaffolded an in-tree `docs/plans/` and
> committed plans there; the loop never reads that path, so it produced a
> divergent double-write (in-tree copy + external copy in different schemas)
> and polluted the project's git history. Always resolve via `ilk_paths.py`.

## 3. Pull the ticket batch

```powershell
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
# Save to a scratch/tmp dir (NOT the repo, NOT docs/plans/) and delete after use
import json, subprocess, sys
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

This becomes the input to step 6 below.

## 6. Delegate to /ilk-plan core workflow

Now follow `/ilk-plan` workflow steps 4-8 (the universal planning
workflow):

- Step 4: Read existing plans (collision avoidance).
- Step 5: **Propose grouping (USER APPROVAL REQUIRED)**.
- Step 6: Write the plan files **to `external_plans_dir`** (resolved in
  step 2) — NOT to an in-tree `docs/plans/`. The `tickets:` field of each
  sub-plan's front-matter MUST contain the `T-YYYY-NNNN` ids that landed in
  it. Match the on-disk master/sub-plan schema already present in
  `external_plans_dir` (read an existing file there first).
- Step 7: Run the `/ilk-plan` QC passes.
- Step 8: Persist — there is **no project-repo commit** (plans live
  external). Register the project and release the master `draft → queued`.

Do not skip user approval. Iterate on grouping until the user confirms.

## 7. Lark post-step: update tickets

After the plans are persisted (step 6 / `/ilk-plan` step 8 — external dir,
master released to `queued`), update every ticket in the batch.

Plans now live OUTSIDE the repo, so there is **no in-repo blob URL** to link
to. Instead, link `关联 plan` to a **commit-search URL keyed on the sub-plan's
`[plan:<slug>]` commit trailer** — deterministic from the slug, browsable, and
it resolves to the real commits as the loop lands them (empty until then). No
in-tree file required.

For each `(ticket_id, record_id)`:
1. Determine which sub-plan the ticket landed in by reading each sub-plan's
   `tickets:` front-matter list; note its `plan:` slug.
2. Derive `web_base` from `git remote get-url origin` (strip trailing `.git`;
   normalize `git@host:org/repo` → `https://host/org/repo`).
3. Compute the commit-search URL by host:
   - GitHub: `<web_base>/search?q=%5Bplan%3A<slug>%5D&type=commits`
   - Gitee:  `<web_base>/commits/<branch>` (Gitee lacks reliable message
     search — link the branch commits page and rely on the `[plan:<slug>]`
     trailer being greppable).
4. Update the ticket via `cli.py`: set `关联 plan=<url>` and `状态=计划中`.

For batches of 10+ tickets, write a one-off helper **in a scratch/tmp dir
(never the repo)**, using `--fields-json` so non-ASCII values bypass argv:

```python
# scratch dir only — delete after use
import json, subprocess, sys, tempfile, os
CLI = os.path.expanduser(r"<skill-root>/ilk-lark-tickets/scripts/cli.py")

ROUTING = [
    # (ticket_id, record_id, plan_url) tuples
]

failures = []
for tid, rid, url in ROUTING:
    fj = os.path.join(tempfile.gettempdir(), f"_lark_{tid}.json")
    with open(fj, "w", encoding="utf-8") as f:
        json.dump({"关联 plan": url, "状态": "计划中"}, f, ensure_ascii=False)
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
  not land in the repo or `external_plans_dir`.

## 9. Final report

End your turn with:

1. Summary: "Pulled N 可执行 tickets, grouped into M sub-plans, plans
   pushed, all N tickets updated in Lark to 计划中."
2. The output of
   `python "<skill-root>/ilk-loop/scripts/loop_status.py"`.
3. "Ready to execute. Open a fresh chat and type `/ilk`."

## Boundary rules

- **Never skip the user-approval gate** in step 6 (grouping is subjective).
- **Plans live external — never commit them to the project repo.** Resolve
  `external_plans_dir` via `ilk_paths.py` (step 2) and write there. Do not
  scaffold or commit an in-tree `docs/plans/`.
- **Never update Lark tickets before the plans are persisted** (step 6 /
  `/ilk-plan` step 8 complete, master `queued`).
- **The `关联 plan` link is a `[plan:<slug>]` commit-search URL**, not an
  in-repo file URL — it resolves as the loop lands commits.
- **Always clean up helper scripts** (scratch/tmp only) in step 8.
