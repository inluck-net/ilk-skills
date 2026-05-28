# Skill compliance vs. agentskills.io

Compliance snapshot for the skills in this repo against the standards
recorded in [`agentskills-io.md`](agentskills-io.md). One row per skill.
Update the relevant rows whenever a sub-plan closes a gap.

- **Snapshot date:** 2026-05-28 (updated after paths-and-runtime-hygiene)
- **Validator:** `python3 tools/check_skill_frontmatter.py` enforces
  the Frontmatter and Description-length columns automatically.
- **Reference:** [`agentskills-io.md`](agentskills-io.md)

## Status legend

- `ok` — meets the standard.
- `gap` — does not meet the standard; cleanup pending.
- `n/a` — standard does not apply to this skill (explain in Notes).

## Compliance table

| Skill | Frontmatter | Description length | Progressive disclosure | Path portability | Runtime / package hygiene | Notes / next action |
|---|---|---|---|---|---|---|
| `ilk-feedback` | ok — moved haiku hint to `metadata.preferred_model` (ce144c5) | ok — 474 chars, well under 1024 limit | ok — body ~217 lines, supporting material in `scripts/` | ok — uses `<skill-root>` placeholder; remaining `~/.cursor` refs are the placeholder definition (08adae7) | ok — no committed runtime artifacts in skill dir | All four target columns now `ok`. |
| `ilk-launcher` | ok — moved haiku hint to `metadata.preferred_model` (ce144c5) | ok — 331 chars, well under 1024 limit | gap — `SKILL.md` is ~611 lines; deep launcher mechanics belong in references | ok — uses `<skill-root>` placeholder; bootstrap example marked Cursor-specific (08adae7) | gap — committed `projects.json.example` is user-mutable runtime config; should be example-only in package | Path portability now `ok`; split SKILL.md per progressive-disclosure sub-plan; demote `projects.json` to example-only when that lands. |
| `ilk-loop` | ok — only `name` and `description` | ok — single paragraph router hint | gap — `SKILL.md` is ~712 lines; meta-projects and worktrees sections are reference-grade | ok — uses `<skill-root>` placeholder; remaining `~/.cursor` refs are the placeholder definition (08adae7) | ok — `skills/ilk-loop/logs/` already gitignored; on-disk runtime artifacts deleted as part of step 1 | Path portability + runtime hygiene now `ok`; progressive-disclosure pass still pending. |
| `ilk-runner` | ok — only `name` and `description` | ok — single paragraph router hint | ok — body ~108 lines, no scripts of its own | ok — no host-specific paths in body | ok — no committed runtime artifacts | No action expected; revisit if it grows past best-practices length. |
| `ilk-watchdog` | ok — only `name` and `description` | ok — single paragraph router hint | ok — body ~249 lines | ok — uses `<skill-root>` placeholder; remaining `~/.cursor` refs are the placeholder definition (08adae7) | ok — no committed runtime artifacts in skill dir | All four target columns now `ok`. |

## Sub-plan ownership

The sub-plans below are expected to update specific columns. Each
sub-plan is responsible for flipping its rows from `gap` to `ok` and
amending the Notes column when it ships.

| Sub-plan slug | Columns updated |
|---|---|
| `agentskills-frontmatter-normalize` | Frontmatter, Description length |
| `agentskills-paths-and-runtime-hygiene` | Path portability, Runtime / package hygiene |
| `agentskills-progressive-disclosure` | Progressive disclosure |
| `agentskills-directory-conventions` | Progressive disclosure (support-dir naming), Notes |

## Update policy

This table is intentionally close to the code so it stays accurate. It
must be updated whenever any of the following happens:

- A sub-plan listed in [Sub-plan ownership](#sub-plan-ownership) ships a
  step that changes a skill's state for one of its owned columns.
- A new skill is added under `skills/` — append a row with each column
  set to `gap` / `ok` / `n/a` as appropriate.
- A skill is removed or renamed — update or delete its row in the same
  commit as the rename.
- A new standard is adopted in this repo — add a companion file under
  `docs/standards/` (see `agentskills-io.md` → "Future standards"), then
  extend this table with the new columns or link out to a sibling table
  in the new file.

When you update a row:

1. Edit the relevant row(s) in the table above, flipping `gap` to `ok`
   (or vice versa) and refreshing the Notes column with the action taken
   and the commit short-hash.
2. Bump the snapshot date at the top.
3. Commit the change as part of the same step that fixed the underlying
   issue, not as a separate "update compliance table" commit. This keeps
   the audit trail aligned with the code change.
