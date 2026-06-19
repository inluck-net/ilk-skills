Bootstrap a Lark Bitable tracker for the current project — create the
base, seed the ticket schema, and write the `.lark-project` marker.

This is the **setup** command. For ongoing triage and planning, use
`/ilk-lark-tickets` instead.

> **Prerequisite**: this command depends on the `ilk-lark-tickets` skill
> being installed at `<skill-root>/ilk-lark-tickets/`. That skill is **not**
> part of `ilk-skills`. Skip this command if you don't have a Lark Bitable
> to set up.

Follow these steps in order. Do NOT skip any.

## 1. Load conventions

Read in parallel:
- `<skill-root>/ilk-lark-tickets/SKILL.md` — "Adding a new project" section
  documents the `init-project` invocation and flags.
- `<skill-root>/ilk-lark-tickets/scripts/cli.py` — the `init-project` verb.

## 2. Resolve the project name

Walk up from cwd to find `.lark-project`. If found, its contents are the
project name — use that (skip to step 3).

If not found, derive a default name from the cwd repo basename:
```powershell
$projectName = (Get-Item (git rev-parse --show-toplevel)).Name
```

Ask the user: "Use project name `$projectName`?" — allow override.
Store the chosen name for step 3.

## 3. Surface the `--folder` decision

Explain to the user:

- **No `--folder`**: the Bitable is created in the app's own space. It's
  accessible via URL and API, but not visible in the user's Feishu Drive.
  Good for fully automated / CI-driven setups.
- **`--folder <token>`**: the Bitable is created inside a Drive folder the
  user specifies. It appears in their Feishu Drive, supports form-view
  (so clients can submit tickets via a Feishu form), and is easier to
  find manually.

Ask: "Do you want the base in a Drive folder? If yes, paste the folder
token (the `fldcn...` part from the folder URL). Otherwise, skip."

Do NOT block on this — proceed with or without the folder token.

## 4. Run `init-project`

```powershell
python <skill-root>/ilk-lark-tickets/scripts/cli.py init-project `
  --project <name> `
  [--folder <token>] `
  --prefix T
```

The command is **idempotent**: re-running for the same project reuses the
existing base (never creates a duplicate). If the base is unreachable
(e.g. deleted), it refuses unless `--force-recreate` is passed.

Note: the command upserts the project entry in `~/.ilk-data/ilk-lark-tickets/config.json`,
preserving `app_id`, `app_secret`, and other projects' entries.

## 5. Verify

```powershell
python <skill-root>/ilk-lark-tickets/scripts/cli.py fields --project <name>
python <skill-root>/ilk-lark-tickets/scripts/cli.py list --project <name> --limit 5
```

Confirm both exit 0. The `fields` output should show the standard 24-field
ticket schema.

## 6. Report

Print a summary:

```
✅ Lark tracker ready for project "<name>"

   Base URL:  <bitable_url>
   Marker:    <path>/.lark-project
   Config:    ~/.ilk-data/ilk-lark-tickets/config.json
   Status:    created | reused (existing base found)

   Next steps:
   - Use /ilk-lark-tickets to triage and plan tickets from this Bitable.
   - Share the form link with clients to collect bug reports.
```

The `init-project` output includes the base URL and whether it was created
or reused — pass that through.

## 7. Cross-link

This command is the **setup** entry point. For ongoing operations:

- `/ilk-lark-tickets` — triage, plan, and link commits to tickets.
- `cli.py list` / `cli.py show` / `cli.py update` — direct CLI access.
