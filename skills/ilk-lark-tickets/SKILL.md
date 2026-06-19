---
name: ilk-lark-tickets
description: >-
  Read, triage, and update issue tickets stored in a Feishu (Lark) Bitable.
  Use when the user asks to triage new tickets, list/update tickets, generate
  an execution plan from a ticket, link a commit/PR to a ticket, or anything
  that mentions 飞书工单 / 多维表格 / 工单池 / Feishu tickets.
---

# Lark Tickets — Issue triage via Feishu Bitable

This skill lets the AI read and write issue tickets stored in a Feishu Bitable
(多维表格), so the user can collect bug reports / feature requests via a Feishu
form and have the AI triage them automatically.

## When to use

Trigger this skill when the user says any of:

- "triage 新工单" / "看看新工单" / "拉一下飞书工单"
- "看看 T-2026-0042 这个工单" / "更新一下这个工单的状态"
- "为 T-xxxx 生成执行计划" / "把这条工单变成 plan"
- "把刚才的 commit 关联到 T-xxxx"
- Any time the user references a ticket id (e.g. `T-2026-0007`).

## Architecture

```
~/.cursor/lark-tickets/
  config.json            # app credentials + per-project bitable mapping (gitignored)
  .token_cache.json      # auto-generated, expires every 2h

<repo>/skills/ilk-lark-tickets/    # source of truth (installed via junction into ~/.cursor, ~/.claude)
  SKILL.md               # this file
  scripts/
    lark_client.py       # stdlib-only Bitable client (no pip deps)
    cli.py               # CLI: list / show / pull-new / update / next-id / download / fields
    init_bitable.py      # one-shot schema seeder for a new project's bitable

<project repo>/
  .lark-project          # one line: project name (key in config.projects)
```

**Project resolution:** The CLI walks up from cwd looking for `.lark-project`.
If found, its contents (e.g. `uccargo`) pick which project's bitable to talk to.
You can override with `--project <name>`.

## Bitable schema (uccargo project)

Field name → semantics. **Bold = client-filled (form view), plain = AI-filled, italic = system/shared.**

| # | Field | Type | Filled by | Purpose |
|---|---|---|---|---|
| 1 | **标题** | Text | client | One-line summary (primary field) |
| 2 | **原文描述** | Text | client | Verbatim client words |
| 3 | **在哪个页面** | URL | client | URL of the page they were on |
| 4 | **操作步骤** | Text | client | What they were doing |
| 5 | **期望看到** | Text | client | Expected behavior |
| 6 | **实际看到** | Text | client | Actual behavior |
| 7 | **截图** | Attachment | client | Screenshot(s) |
| 8 | **录入端** | SingleSelect | client | `Portal` / `Admin` / `通用` |
| 9 | **紧急度** | SingleSelect | client | `低` / `中` / `高` / `紧急` (client's self-reported urgency) |
| 10 | ticket_id | Text | AI | `T-YYYY-NNNN` (use `cli.py next-id`) |
| 11 | 类型 | SingleSelect | AI | `bug` / `新功能` / `体验优化` / `咨询` / `重复` / `无效` |
| 12 | 涉及模块 | Text | AI | Free-text, comma-separated. e.g. `订单, 钱包` or `api/orders, portal/cart` |
| 13 | AI 理解 | Text | AI | Concise restatement / summary |
| 14 | 缺失信息 | Text | AI | If status=待澄清, list what's missing |
| 15 | AI 优先级建议 | SingleSelect | AI | `P0` / `P1` / `P2` / `P3` (AI's call, may differ from 紧急度) |
| 16 | 关联 plan | URL | AI | Link to the execution plan markdown (Gitee/GitHub URL) |
| 17 | 关联 commit | Text | AI/CI | Commit short hashes, comma-separated |
| 18 | E2E 结果 | SingleSelect | CI | `未运行` / `通过` / `失败` / `跳过` |
| 19 | E2E 报告链接 | URL | CI | Link to the playwright HTML report |
| *20* | *状态* | *SingleSelect* | *shared* | `新建` / `待澄清` / `可执行` / `计划中` / `实施中` / `待验证` / `已发布` / `关闭` / `重复` / `无效` |
| *21* | *处理人* | *User* | *shared* | Person currently responsible |
| *22* | *录入人* | *User* | *system* | Auto from form submitter |
| *23* | *录入时间* | *CreatedTime* | *system* | Auto |
| *24* | *最后更新* | *ModifiedTime* | *system* | Auto |

> If the schema drifts, run `python cli.py fields` to inspect actual field names/types,
> and `python cli.py options` (TODO) or read SingleSelect `property.options` directly.

## Status state machine

```
新建 ──(AI triage)──┬──> 可执行 ──(AI plan)──> 计划中 ──(impl)──> 实施中 ──(ship)──> 待验证 ──(client OK)──> 已发布 ──> 关闭
                    │                                                                          │
                    └──> 待澄清 ─(client clarifies)─> 新建                                      └──(client NG)──> 实施中
                    └──> 重复 / 无效 (terminal)
```

## Core CLI

Run from any directory inside a repo that has a `.lark-project` marker:

```powershell
# Discover schema
python C:\Users\chad\.cursor\skills\lark-tickets\scripts\cli.py fields

# Pull all 新建 tickets for triage (full content)
python ...\cli.py pull-new

# Summary list, optionally filtered by status
python ...\cli.py list --status 可执行 --limit 20

# One ticket, full content
python ...\cli.py show recXXXXXXXX

# Generate next ticket id
python ...\cli.py next-id

# Update fields. Repeat --field NAME=VALUE.
# JSON-looking values are parsed as JSON; plain strings are auto-wrapped for Text fields.
python ...\cli.py update recXXXXXXXX `
  --field "ticket_id=T-2026-0042" `
  --field "类型=Bug" `
  --field "优先级=P1 高" `
  --field "功能模块=订单" `
  --field "影响仓库=[\"api\",\"portal\"]" `
  --field "状态=可执行" `
  --field "AI 摘要=用户在订单详情页点击取消时报 500"

# Download attachments to a folder
python ...\cli.py download recXXXXXXXX 截图 --to .\tmp\screenshots
```

> On Windows PowerShell, escape inner double quotes as `\"` when passing JSON values.

## Standard workflows

### 1. Triage workflow (`triage 新工单`)

1. Run `cli.py pull-new` and read all 新建 tickets.
2. For each ticket:
   - If clarification is needed (description too vague, no page URL, can't reproduce):
     - Set `状态=待澄清`, fill `缺失信息` with a bulleted list of what's missing,
       optionally fill `AI 理解` with whatever you *did* understand.
   - Otherwise:
     - Generate a `ticket_id` via `cli.py next-id`.
     - Fill: `ticket_id`, `类型`, `涉及模块`, `AI 理解`, `AI 优先级建议`.
     - If attachments exist, optionally download (`cli.py download`) to inspect.
     - Set `状态=可执行`.
3. Report a one-line summary per ticket back to the user.

Heuristics for triage:
- 类型:
  - "500" / "报错" / "白屏" / "无法" / "点了没反应" → `bug`
  - "希望能" / "可以加个" / "建议增加" → `新功能`
  - "如果...会更好" / "界面有点" / "按钮太小" → `体验优化`
  - "怎么用?" / "在哪里看?" → `咨询`
  - Already exists in another ticket → `重复` (cross-link in `AI 理解`)
- AI 优先级建议 (independent from client's 紧急度):
  - Affects payment / login / data loss → `P0`
  - Blocks main flow for many users → `P1`
  - Annoying but has workaround → `P2`
  - Polish / nice-to-have → `P3`
  - If client said `紧急` but you think it's `P3`, still record `P3` and explain in `AI 理解`.
- 涉及模块 (free text, infer from URL in 在哪个页面):
  - `/cart` → `portal/cart`
  - `/orders` → `portal/orders` (often + `api/orders`)
  - `/wallet` → `portal/wallet` (+ `api/wallet`)
  - `/admin/...` → `ops/<area>`
  - `v3-api.thorder.com` → `api/<app>`
  - Login/register → `users` (+ `portal/auth` or `ops/auth`)
  - Use comma to separate multiple modules.

### 2. Plan generation (`为 T-xxxx 生成执行计划`)

1. `cli.py show <record_id>` to fetch full content (or look it up by ticket_id first).
2. Use `/ilk-plan` (or `/ilk-lark-tickets` to source straight from triaged tickets) to build a step-recoverable plan.
3. Save plan to `docs/plans/YYYY-MM-DD-<short-slug>.md`.
4. Update the ticket:
   ```powershell
   python ...\cli.py update <record_id> `
     --field "关联 plan=https://gitee.com/uccargo/docs/blob/master/plans/2026-04-17-...md" `
     --field "状态=计划中"
   ```

### 3. Linking commits (`把这个 commit 关联到 T-xxxx`)

When the user includes `[ticket:T-xxxx]` in a commit message:

1. Resolve `T-xxxx` → `record_id` via `cli.py list` (search the `ticket_id` field).
2. Read existing `关联 commit`, append the new short hash (comma-separated), write back.
3. If commit message also includes `[done]`, set `状态=待验证`.

### 4. E2E result writeback (CI-driven, future)

When the `ship-and-test` flow finishes, populate:
- `E2E 结果` = 通过 / 失败 / 跳过
- `E2E 报告链接` = the Playwright HTML report URL on staging
- If 通过 and ticket is `待验证`, optionally bump to `已发布`.

### 4. Daily standup (`每日工单速报`)

`cli.py list --status 可执行` and `cli.py list --status 实施中` and `cli.py list --status 待澄清`,
then summarize counts and any P0/P1 items.

## Writing values: gotchas

- **Text fields**: pass plain strings; the CLI auto-wraps to `[{"text": "...", "type": "text"}]`.
- **SingleSelect**: pass the option *name* as a string (e.g. `"P1 高"`).
- **MultiSelect**: pass a JSON array of names: `--field "影响仓库=[\"api\",\"portal\"]"`.
- **URL**: pass plain string; auto-wrapped to `{"link": "...", "text": "..."}`.
- **User**: pass `[{"id": "ou_xxx"}]` JSON. Skip if you don't have the user's `open_id`.
- **Date/CreatedTime/ModifiedTime**: don't write; system-managed.
- **Attachment**: writing requires uploading via `/open-apis/drive/v1/medias/upload_all` first; out of scope for triage.

## Error handling

- `Lark API error: [91403]` → app lacks doc-level permission. Tell the user to add the
  app as a document collaborator on the bitable.
- `Lark API error: [99991672]` → app lacks the required scope. Tell the user which scope
  (the body lists e.g. `bitable:app`).
- Token cache lives at `~/.cursor/lark-tickets/.token_cache.json`. Delete it to force refresh.

## Adding a new project

1. Create the bitable in Feishu, add the app as a "文档应用" with edit rights.
2. Append to `~/.cursor/lark-tickets/config.json`:
   ```json
   "projects": {
     "myproj": {
       "bitable_app_token": "Nxxx",
       "table_id": "tblxxx",
       "ticket_id_prefix": "T"
     }
   }
   ```
3. In the new repo's root, write `.lark-project` containing `myproj`.
4. Optionally run `scripts/init_bitable.py` (see README) to seed the standard 24-field schema.

## See also

- `~/.cursor/skills/ilk-lark-tickets/README.md` — admin/setup notes (creds, schema seeding).
- `docs/plans/README.md` (per-project) — plan file naming and archive convention.
