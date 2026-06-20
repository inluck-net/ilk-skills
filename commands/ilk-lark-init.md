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

## 3. Surface the editability + folder decisions

**Editable base (recommended one-time setup)**:

By default, the base is app-owned and NOT editable in the Feishu web UI.
To make it editable, the operator's `open_id` is granted `full_access` on
every created base. This is stored in config via `set-operator`:

1. Find your `open_id` by running `show-members` on an existing editable project:
   ```powershell
   python <skill-root>/ilk-lark-tickets/scripts/cli.py show-members --project <an-editable-project>
   ```
   Copy the `member_id` (e.g. `ou_233c253c...`) that has `full_access`.
2. Set it once:
   ```powershell
   python <skill-root>/ilk-lark-tickets/scripts/cli.py set-operator <open_id>
   ```
3. All future `init-project` runs will grant that `open_id` `full_access`
   automatically (idempotent, non-fatal).

If `operator_openid` is not set, `init-project` prints a `WARNING` pointing
at `set-operator`.

**`--folder` (optional, organization only)**:

- **No `--folder`**: the Bitable is created in the app's own space. It's
  accessible via URL and API, but not visible in the user's Feishu Drive.
  Good for fully automated / CI-driven setups.
- **`--folder <token>`**: the Bitable is created inside a Drive folder the
  user specifies. It appears in their Feishu Drive and is easier to find
  manually. Note: the app needs write permission on the folder or creation
  fails with `1062535`. `--folder` does NOT confer editability — use
  `set-operator` for that.

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

### Updating an existing tracker (re-run to bring it up to spec)

Re-running `init-project` for a project that **already has a base** is the
supported way to **update** that tracker — the reuse path is not a no-op. It:

- re-seeds the schema, adding any **new fields** introduced since the base was created;
- ensures the **Kanban + shared Form views** exist (creates whichever is missing, skips existing);
- applies **form field config** — the form shows only the 8 client-facing fields
  (标题, 在哪个页面, 期望看到, 实际看到 required; 操作步骤, 截图, 紧急度, 类型
  optional; all others hidden), matching the uccargo reference;
- grants the configured **`operator_openid`** `full_access` so the base is editable in the web UI (idempotent — safe to repeat).

So from a project session (e.g. inside the `math-blocks` repo, which already has
a `.lark-project` marker), just run `/ilk-lark-init` again — or directly
`init-project --project <name>` — to pull in the latest tracker features. Nothing
is recreated; the existing base, records, and shared form URL are preserved.

> Prerequisite for the editability grant: `operator_openid` must be set once in
> the shared config (`set-operator <open_id>`). It is global across all projects,
> so setting it once benefits every project's re-run. Without it, the base is
> still updated (schema + views) but stays app-owned/not-editable until you set it
> and re-run.

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
