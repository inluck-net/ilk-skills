Plan toolkit improvements from the shared improvement backlog.

This is the self-improvement source adapter — it reads open candidates
emitted by `/ilk-feedback` postmortems, formats them as a task description,
and delegates to the `/ilk-plan` core workflow on the ilk-skills repo.

> **Result**: the `/ilk-plan` core writes the resulting master as
> `status: draft` with `supervised_only: false` (the flag is retired —
> decomposition-principles.md §13). A human releases it (`draft` → `queued`);
> the loop runs the self-modifying batch in the selfmod worktree and merges it
> back into the toolkit clone.

Follow these steps in order.

## 1. Load conventions

Read in parallel:
- `<skill-root>/ilk-loop/SKILL.md` (workflow conventions)
- `<skill-root>/ilk-self-improve/SKILL.md` (this adapter's boundary)

## 2. Verify project context

- Walk up from cwd to find a project root (`.git` or `docs/`).
- If `docs/plans/` doesn't exist, scaffold it (see SKILL workflow #2).

## 3. Run build_task.py

```bash
python3 "<skill-root>/ilk-self-improve/scripts/build_task.py" --dry-run
```

If the output says "Nothing to improve", tell the user and STOP.

Otherwise, capture the task description output — this becomes the input
to step 4.

## 4. Delegate to /ilk-plan core workflow

Now follow `/ilk-plan` workflow steps 4-7 (the universal planning
workflow):

- Step 4: Read existing plans (collision avoidance).
- Step 5: **Propose grouping (USER APPROVAL REQUIRED)**.
- Step 6: Write the plan files.
- Step 7: Commit and push.

Do not skip user approval. Iterate on grouping until the user confirms.

## 5. Final report

End your turn with:

1. Summary: "Read N open candidates from the improvement backlog, grouped
   into M sub-plans, plans pushed."
2. Note that the resulting master is `draft` — the user releases it
   (`draft` → `queued`) when ready.
3. The output of
   `python "<skill-root>/ilk-loop/scripts/loop_status.py"`.

## Boundary rules

- **Never skip the user-approval gate** in step 4.
- **Never auto-apply toolkit changes** — the adapter only PLANS.
- **If the backlog is empty**, say so and stop — don't invent work.
