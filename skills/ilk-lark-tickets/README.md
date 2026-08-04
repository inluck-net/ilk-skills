# ilk-lark-tickets — admin/setup notes

> User-facing operational notes. The AI agent's instructions live in `SKILL.md`.

## What this is

A Cursor global skill that lets the AI agent read/write issue tickets stored in
a Feishu (Lark) Bitable. Currently used for the **uccargo** project; designed to
support multiple projects via per-repo `.lark-project` markers.

## Layout

```
~/.ilk-data/ilk-lark-tickets/
  config.json            # credentials + project mapping (DO NOT COMMIT)
  .token_cache.json      # auto-generated, ~2h lifetime

~/.cursor/skills/ilk-lark-tickets/
  SKILL.md               # agent instructions
  README.md              # this file
  scripts/
    lark_client.py       # stdlib-only Bitable client (no pip deps)
    cli.py               # CLI entry: list / show / pull-new / update / next-id / download / fields
    init_bitable.py      # one-shot schema seeder for new projects
```

## Prerequisites

- Python 3.8+ on PATH (only Python stdlib is used; no pip packages required).
- A Feishu custom app (自建应用) with these scopes:
  - `bitable:app` — read+write bitables
  - `drive:drive` — needed to download attachments via `/open-apis/drive/v1/medias/...`
- The app must be **added as a document application (文档应用) on the specific bitable**
  with editor permission. App-level scope alone is not enough; doc-level grant is required
  for write operations on Wiki-attached or restricted bitables.

## Adding a new project

### One-command bootstrap (recommended)

```powershell
python <skill-root>/ilk-lark-tickets/scripts/cli.py init-project --project <name>
```

This single command does everything: creates a new Bitable base, writes the
config entry, drops the `.lark-project` marker, seeds the 24-field schema, and
creates a **Kanban view** (grouped by `状态`) plus a **shared ticket-submit Form
view** — so a fresh base matches the reference uccargo tracker. It's
**idempotent** — re-running never duplicates the base or the views (existing
views are skipped).

**Form field config**: the form shows only the 8 client-facing fields (matching
uccargo): **标题, 在哪个页面, 期望看到, 实际看到** (required); **操作步骤,
截图, 紧急度, 类型** (optional). All other fields are hidden.

**Sharing**: defaults to `tenant_editable` on form creation. If you manually
upgrade to `anyone_editable` in the Feishu UI, re-running `init-project`
preserves that upgrade (sharing is set only on creation, not on re-runs).

**Pull-new safeguard**: form submissions land with blank `状态` (the form hides
it). `cli.py pull-new` automatically picks up and backfills these records to
`状态=新建` so they appear in the kanban's 新建 column.

**Field defaults**: the Bitable API rejects setting field default values
(`[1254082] SingleSelectFieldPropertyError`). To set 状态 default → 新建, do it
manually in the Feishu field editor (one-time UI-only step). The pull-new
safeguard already covers triage regardless.

Options:
- `--project <name>` (required) — project key in config.
- `--folder <token>` — Drive folder token to create the base inside (see
  "Editable base" below); falls back to the configured `default_folder_token`.
- `--prefix <str>` — ticket id prefix (default `T`).
- `--repo <path>` — repo root for the `.lark-project` marker (default: cwd).
- `--force-recreate` — replace an unreachable base instead of refusing.

### Editable base (one-time setup)

A base the app creates in its own space is **not editable** by you in the Feishu
web UI (only reachable by URL/API), so `init-project` prints a `WARNING` when
`operator_openid` is not configured. To make it editable:

1. Find your `open_id` — run `show-members` on an existing editable project:
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

**`--folder` / `default_folder_token`** are for **organization only** (placing
bases in a Drive folder for visibility). They do NOT confer editability.
Note: the app needs write permission on the folder or creation fails with
`1062535`.

Verify after:
```powershell
python <skill-root>/ilk-lark-tickets/scripts/cli.py fields --project <name>
```

### Manual fallback

If the Bitable already exists or you need a Wiki-embedded base:

1. **Create the bitable in Feishu.** Use a standalone bitable (`/base/...`), not Wiki-embedded,
   to avoid extra Wiki permission scopes.

2. **Grant the app document-level access.** Open the bitable → 更多 → ... → 添加文档应用 →
   search by app name → set permission to "可管理" or "可编辑".

3. **Append to `~/.ilk-data/ilk-lark-tickets/config.json`:**
   ```json
   "projects": {
     "<project_name>": {
       "bitable_app_token": "<from URL: /base/{token}?...>",
       "table_id": "<from URL: ?table={id}>",
       "ticket_id_prefix": "T"
     }
   }
   ```

4. **Drop a marker in the repo root:**
   ```powershell
   Set-Content -Path .\.lark-project -Value "<project_name>" -Encoding UTF8
   ```

5. **Seed the schema** (optional — only if the bitable is empty):
   ```powershell
   python <skill-root>/ilk-lark-tickets/scripts/init_bitable.py --project <name>
   ```

6. **Verify:**
   ```powershell
   python <skill-root>/ilk-lark-tickets/scripts/cli.py fields --project <name>
   ```

## CLI cheat sheet

```powershell
# inside any folder under a repo with .lark-project
$cli = "python C:\Users\chad\.cursor\skills\lark-tickets\scripts\cli.py"

& $cli fields                                  # list field schema
& $cli list --status 新建                      # list new tickets (summary)
& $cli pull-new                                # full content of all 新建 tickets
& $cli show recXXXX                            # one ticket
& $cli next-id                                 # next T-YYYY-NNNN
& $cli update recXXXX --field "状态=可执行"    # update fields
& $cli download recXXXX 截图 --to .\tmp        # download attachments
```

## Security

- `config.json` contains app secret. Do **not** commit it. The folder
  `~/.ilk-data/ilk-lark-tickets/` is outside any repo so this is safe by default.
- Token cache is rewritten on every refresh; safe to delete anytime.
- Never check `.lark-project` markers into a repo if you don't want others using
  this skill against your bitable. The marker holds only a project key (not
  credentials), but in a **public or shared** repo it still advertises which
  tracker a clone points at — keep it untracked and `.gitignore`d.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `[91403] Forbidden` | App lacks document-level permission | Add app as 文档应用 on the bitable with edit rights |
| `[99991672] Access denied` | App scope missing | Apply scope in dev console, publish a new app version |
| `[1254005] FieldNameNotFound` | Schema drifted | Run `cli.py fields` to see actual names |
| All requests fail with 401 | Token cache stale across app changes | Delete `~/.ilk-data/ilk-lark-tickets/.token_cache.json` |

## What your configuration looks like

Your own app / bitable / table IDs live **only** in
`~/.ilk-data/ilk-lark-tickets/config.json` — never in this repo. The shape,
with placeholder values:

- **App:** `cli_xxxxxxxxxxxxxxxx` (from the Feishu/Lark dev console)
- **Project `<project-key>`:** bitable `<app_token>`, table `<table_id>` — both
  readable off the bitable URL:
  `https://<host>/base/<app_token>?table=<table_id>`
- **Repo marker:** `<repo>/.lark-project` → `<project-key>`

To see what is actually configured on this machine, read
`~/.ilk-data/ilk-lark-tickets/config.json`; `cli.py fields` confirms the
credentials and IDs in it can reach the bitable.
