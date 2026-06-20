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

Options:
- `--project <name>` (required) — project key in config.
- `--folder <token>` — Drive folder token to create the base inside (see
  "Editable base" below); falls back to the configured `default_folder_token`.
- `--prefix <str>` — ticket id prefix (default `T`).
- `--repo <path>` — repo root for the `.lark-project` marker (default: cwd).
- `--force-recreate` — replace an unreachable base instead of refusing.

### Editable base (one-time setup)

A base the app creates in its own space is **not editable** by you in the Feishu
web UI (only reachable by URL/API), so `init-project` prints a `WARNING` when no
folder is resolved. To get an editable base, create the base inside a Drive
folder **you own**:

1. In Feishu Drive, create a folder you own and **share it with the app** (edit).
2. Copy its folder token from the URL (`…/drive/folder/<FOLDER_TOKEN>`).
3. Run once: `python <skill-root>/ilk-lark-tickets/scripts/cli.py set-default-folder <FOLDER_TOKEN>`
4. All future `init-project` runs land in that folder (editable) with no flag —
   or pass `--folder <token>` per call.

(`init-project` also makes a best-effort attempt to grant your account
`full_access` by open_id, but the `--folder`/`default_folder_token` path is the
reliable contract.)

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
  this skill against your bitable. (For solo dev: it's fine to commit, since the
  marker only contains the project key — credentials stay local.)

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `[91403] Forbidden` | App lacks document-level permission | Add app as 文档应用 on the bitable with edit rights |
| `[99991672] Access denied` | App scope missing | Apply scope in dev console, publish a new app version |
| `[1254005] FieldNameNotFound` | Schema drifted | Run `cli.py fields` to see actual names |
| All requests fail with 401 | Token cache stale across app changes | Delete `~/.ilk-data/ilk-lark-tickets/.token_cache.json` |

## Currently configured

- **App:** `cli_a96b665a33bb9bdf`
- **Project `uccargo`:** bitable `N4xabAfpdaN9gVsKdjvckJiEnse`, table `tbl0xeC3e8S220wl`
- **Repo marker:** `c:\mywork\gitee\uccargo\.lark-project` → `uccargo`
