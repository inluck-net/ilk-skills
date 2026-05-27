# Skill compliance vs. agentskills.io

Compliance snapshot for the skills in this repo against the standards
recorded in [`agentskills-io.md`](agentskills-io.md). One row per skill.
Update the relevant rows whenever a sub-plan closes a gap.

- **Snapshot date:** 2026-05-28
- **Reference:** [`agentskills-io.md`](agentskills-io.md)

## Status legend

- `ok` — meets the standard.
- `gap` — does not meet the standard; cleanup pending.
- `n/a` — standard does not apply to this skill (explain in Notes).

## Compliance table

| Skill | Frontmatter | Description length | Progressive disclosure | Path portability | Runtime / package hygiene | Notes / next action |
|---|---|---|---|---|---|---|
| `ilk-feedback` | gap — frontmatter contains non-standard `model: haiku` | ok — single paragraph router hint | ok — body ~217 lines, supporting material in `scripts/` | gap — `~/.cursor/...` examples present | ok — no committed runtime artifacts in skill dir | Remove `model:` in frontmatter-normalize; rewrite host-specific paths in paths-and-runtime-hygiene. |
| `ilk-launcher` | gap — non-standard `model: haiku` | ok — single paragraph router hint | gap — `SKILL.md` is ~611 lines; deep launcher mechanics belong in references | gap — `~/.cursor/...` examples present | gap — committed `projects.json` is user-mutable runtime config; should be example-only in package | Remove `model:`; split SKILL.md per progressive-disclosure sub-plan; demote `projects.json` to example-only in paths-and-runtime-hygiene. |
| `ilk-loop` | ok — only `name` and `description` | ok — single paragraph router hint | gap — `SKILL.md` is ~712 lines; meta-projects and worktrees sections are reference-grade | gap — many `~/.cursor/...` paths in body | gap — `skills/ilk-loop/logs/` ships ~160 runtime log files inside the skill package | Move worktree/meta-projects detail under `references/` per progressive-disclosure; rewrite path examples; delete `logs/` and add it to `.gitignore` per paths-and-runtime-hygiene. |
| `ilk-runner` | ok — only `name` and `description` | ok — single paragraph router hint | ok — body ~108 lines, no scripts of its own | ok — no host-specific paths in body | ok — no committed runtime artifacts | No action expected; revisit if it grows past best-practices length. |
| `ilk-watchdog` | ok — only `name` and `description` | ok — single paragraph router hint | ok — body ~249 lines | gap — `~/.cursor/...` examples present | ok — no committed runtime artifacts in skill dir | Rewrite host-specific paths in paths-and-runtime-hygiene. |

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
