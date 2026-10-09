File a backlog row for an ilk-skills toolkit defect, with the evidence
a row needs to be admitted.

## When to use which channel

- **ilk-skills toolkit defect** → this command (`/ilk-file`).
- **gh-resolve-pipeline defect** → `/gh-resolve-file` (GitHub issue).
- **Personal follow-up task** → `~/Documents/handoffs/_inbox.md`.

## Invocation

```bash
python3 "<skill-root>/ilk-feedback/scripts/improvement_backlog.py" add \
  --title "short, specific title" \
  --gap "what is missing or broken" \
  --kind toolkit \
  --proposed-fix "how to fix it" \
  --file "path/to/file.py" \
  --line 42 \
  --run-id "20261009-123456" \
  --project "project-name" \
  --source "feedback" \
  --relation "overlaps=<row-id>" \
  --no-fix-yet "reason a bug has no proposed fix yet"
```

`<skill-root>` resolves the same way `commands/ilk-plan.md` resolves it.

## Refusal reasons (exit 2 — nothing written)

The CLI refuses a row when:

| Code | Meaning |
|---|---|
| `title-empty` | `--title` is blank or whitespace |
| `title-placeholder` | title ends with ` at -` (a placeholder) |
| `title-too-short` | title has fewer than 4 words |
| `no-evidence-anchor` | neither `--file` nor `--run-id` was given |
| `no-proposed-fix` | kind is `bug`/`escaped-bug` and neither `--proposed-fix` nor `--no-fix-yet` was given |

When the CLI exits 2 it prints the reason(s) and writes nothing.

## Evidence standard

A row is a claim about the present. It must carry:

1. **What was measured.** A file:line read in this session, a run id, a log
   timestamp, or a commit hash.
2. **Where.** The path, the log file, the run directory.
3. **What would falsify the proposed fix.** The test, the grep, or the
   observable state that would disprove the claim if the fix is wrong.

### A negative carries its denominator

Before filing "X never starts / is never offered / is blacklisted", tally the
decision log over the window and quote the count of the expected decision and
of what happened instead.

For dispatch and train rows, the decision log is `~/.ilk-data/logs/scheduler.log`:

```bash
grep <key> ~/.ilk-data/logs/scheduler.log | awk '{print $3}' | sort | uniq -c
```

See `docs/retros/retro-2026-10-09-a-refused-train-read-as-a-train-never-offered.md`
for an example of a row that was disproved by this tally (277 `skip-permits` in
31 h, read as "no train starts").

## Overlaps

The CLI lists open rows that name the same file. If one is the same defect,
re-file with `--relation overlaps=<id>`, or do not file.

## Quote an id only after the CLI returns it

Never send a row id to a peer in the same tool batch that files it.