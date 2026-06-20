---
name: ilk-inbox-tickets
description: >-
  Read, triage, and update entries in the cross-project handoffs inbox
  (~/Documents/handoffs/_inbox.md). Use when the user says "drain the
  inbox", "plan from inbox", "/ilk-inbox", "check the inbox for plans",
  or anything that references handoff entries / inbox planning / cross-
  project drain. Also: resolve project mappings via the inbox-projects
  registry.
---

# Inbox Tickets — Cross-project handoffs ingest adapter

This skill lets the AI read and update entries in the cross-project
handoffs inbox (`~/Documents/handoffs/_inbox.md`), feeding eligible
`pending` entries into `/ilk-plan` — with **cross-project drain** as the
primary consumption mode. It is the markdown-native twin of
`ilk-lark-tickets`.

## When to use

Trigger this skill when the user says any of:

- "drain the inbox" / "plan from inbox" / "inbox → plan"
- "check the inbox" / "what's in the inbox for planning"
- "/ilk-inbox" — the cross-project fan-out command
- "resolve inbox projects" / "map inbox entries"
- Any time the user references a handoff entry slug (e.g. `2026-06-18-some-task`).
- "set up inbox projects registry" / "configure inbox-projects.json"

## Architecture

```
~/Documents/handoffs/
  _inbox.md                 # source of truth — markdown inbox (shared across projects)
  _inbox-archive.md         # archived entries (auto-created on archive)
  <slug>-handoff.md         # optional Tier-2 related handoff files

~/.ilk-data/
  inbox-projects.json       # project registry: **Project** string → repo root / not_plannable

<repo>/skills/ilk-inbox-tickets/    # source of truth (installed via junction into ~/.cursor, ~/.claude)
  SKILL.md               # this file
  scripts/
    inbox_parser.py      # entry model, field parse, Related follow, prose-status extraction
    project_registry.py  # inbox-projects.json resolution + needs-mapping report
    cli.py               # CLI: list / show / update / archive / resolve
    tests/
      test_inbox_parser.py
      test_cli.py
      fixtures/
        _inbox.md        # hermetic test fixture

<repo>/commands/
  ilk-inbox.md           # /ilk-inbox command body (cross-project fan-out)
```

**Key difference from Lark:** The inbox is a SINGLE file spanning many
projects, keyed per-entry by `**Project**:`. Three problems the Lark
path never had to solve:

1. **Project resolution via a registry** — `**Project**:` values are
   heterogeneous (org/repo slugs, slug+path, non-repo paths). The
   registry `~/.ilk-data/inbox-projects.json` maps each string → on-disk
   repo root, with explicit "not plannable" markers. Unresolved entries
   surface in a needs-mapping report.
2. **`pending` ≠ ilk-eligible** — Lark's `可执行` already means
   ready-to-build; inbox `pending` does not. Entries carry `**Type**:`
   markers like "design-first proposal doc — NOT /ilk-plan". An
   eligibility predicate filters.
3. **Partial-scope entries** — a single entry is often a multi-PR cluster
   mid-flight; `**Status**:` is rich prose ("shipped: PR #932 …
   REMAINING: P1/P2"). The parser reads the prose status and plans only
   the remaining scope.

## Entry fields

Each inbox entry is delimited by an H2 heading: `## YYYY-MM-DD — <slug>`.

Field semantics. **Bold = parsed from `**Key**:` lines**, *italic = derived*.

| # | Field | Source | Purpose |
|---|---|---|---|
| 1 | **Title** | heading slug | One-line summary (the slug after the date) |
| 2 | **Date** | heading date | Creation date |
| 3 | **Status** | `**Status**:` | Prose lifecycle state + optional REMAINING scope |
| 4 | **Type** | `**Type**:` | `task` / `bug` / `proposal` / `research` / `docs` / etc. |
| 5 | **Priority** | `**Priority**:` | `P0` / `P1` / `P2` / `P3` |
| 6 | **Project** | `**Project**:` | Cross-project key — resolved via registry to a repo root |
| 7 | **Scope** | `**Scope**:` | Free-text scope description |
| 8 | **Related** | `**Related**:` | Optional `<slug>-handoff.md` filename for Tier-2 handoffs |
| 9 | **Plan** | `**Plan**:` | Added by the adapter after planning (path or URL) |
| 10 | *body* | block text | Everything after the parsed fields |

## Status state machine

```
pending ──(plan created)──> in-progress ──(all steps done)──> shipped ──(archive)──> archived
     │                           │
     └──(ineligible, skipped)    └──(blocked by external dep)──> blocked
```

Mapping to the Lark state machine for parity:

| Inbox state | Lark equivalent | Meaning |
|---|---|---|
| `pending` | `可执行` | Ready for planning |
| `in-progress` | `计划中` / `实施中` | Plan exists, steps in flight |
| `shipped` | `待验证` / `已发布` | All steps done |
| `archived` | `关闭` | Moved to `_inbox-archive.md` |
| `blocked` | `待澄清` | External dependency |

The CLI parses `**Status**:` prose to detect the leading state token
(`pending`, `shipped`, `in-progress`, `blocked`). Anything without a
recognized prefix defaults to `pending`. `REMAINING:` markers capture
partial-scope text for planning only the outstanding work.

## Project registry (`inbox-projects.json`)

Located at `~/.ilk-data/inbox-projects.json` (respects `$ILK_DATA_HOME`).

```json
{
  "projects": {
    "org/repo": {"path": "/Users/chad/Projects/github/org/repo"},
    "slug (extra info)": {"path": "/Users/chad/Projects/other"},
    "design-docs": {"not_plannable": true}
  }
}
```

- **Keys** are the exact `**Project**:` string values from inbox entries
  (case-sensitive). For entries like `slug (path)`, the parser also tries
  the leading slug token as a fallback.
- **`path`** — absolute on-disk repo root. The adapter `cd`s here for
  per-project plan generation.
- **`not_plannable: true`** — intentionally excluded from ilk planning
  (template dirs, design docs, research repos).
- **Missing keys** → `UNRESOLVED`. The `resolve` verb prints a
  needs-mapping report; the `/ilk-inbox` command runs this as a precheck
  before planning.

## Eligibility predicate

An entry is **ilk-eligible** when ALL of:

1. `**Status**:` parses to `pending` (not already shipped/blocked/in-progress).
2. `**Type**:` does NOT contain any ineligibility marker (case-insensitive):
   `not /ilk-plan`, `proposal`, `research`, `design-first`, `docs`.
3. `**Project**:` resolves to a plannable repo root (not `UNRESOLVED` or
   `NOT_PLANNABLE`).

Entries that fail eligibility are silently excluded from `list` and
`group_by_project`. Use `resolve` to surface unresolved projects.

## Core CLI

Run from any directory:

```bash
# List eligible pending entries, grouped by resolved project
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py list --all

# List entries for one project (auto-detected from cwd, or --project)
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py list --project org/repo

# Show one entry's full fields + body + related handoff
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py show <slug>

# Update an entry's status and add a plan reference
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py update <slug> \
  --status in-progress \
  --plan "docs/plans/2026-06-20-my-plan.md"

# Move an entry to the archive file
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py archive <slug>

# Print the needs-mapping report (entries with unresolved projects)
python3 <skill-root>/ilk-inbox-tickets/scripts/cli.py resolve
```

Global flags (all verbs):

- `--inbox PATH` — override inbox file (default: `~/Documents/handoffs/_inbox.md`).
- `--registry PATH` — override registry file (default: `~/.ilk-data/inbox-projects.json`).

`list` and `resolve` accept `--json` for machine-readable output.

## Standard workflows

### 1. Cross-project drain (`/ilk-inbox` — primary flow)

This is the main consumption mode. See `commands/ilk-inbox.md` for the
full flow. Summary:

1. Run `cli.py resolve` — surface any unmapped projects first.
2. Run `cli.py list --all` — get all eligible pending entries grouped by
   resolved project.
3. For each project group:
   - Format a task description from the entries.
   - Delegate to `/ilk-plan` (user-approval gate preserved).
   - After the plan is written, `cli.py update <slug> --status in-progress --plan <path>`.
4. Point the user at `/ilk-schedule` to drain.

### 2. Per-project pull (filtered special case)

When the user is inside a specific project repo and wants only that
project's entries:

1. `cd` into the target repo.
2. Run `cli.py list` (auto-detects project from cwd via the registry).
3. Format + delegate to `/ilk-plan` for just that project's entries.

### 3. Resolve project mappings (`resolve inbox projects`)

1. Run `cli.py resolve` to see unresolved entries.
2. For each unresolved entry, ask the user for the repo root or
   `not_plannable` designation.
3. Update `~/.ilk-data/inbox-projects.json` accordingly.
4. Re-run `cli.py resolve` to confirm all clear.

### 4. Archive a shipped entry

1. Confirm the entry's plan is fully shipped (all steps done).
2. Run `cli.py archive <slug>` — moves the entry block from `_inbox.md`
   to `_inbox-archive.md`.

## Error handling

- **Inbox not found**: tell the user the expected path and ask them to
  create it or pass `--inbox`.
- **Registry not found**: the CLI returns an empty registry (all entries
  unresolved). Run the resolve workflow to set up mappings.
- **Entry not found**: the slug must match exactly (date + slug from the
  H2 heading). Suggest running `cli.py list` to see available entries.

## See also

- `commands/ilk-inbox.md` — the `/ilk-inbox` command body.
- `skills/ilk-lark-tickets/SKILL.md` — the Lark twin (parallel structure).
- `skills/ilk-loop/SKILL.md` — the loop convention (workflow #5 for plan
  generation).
