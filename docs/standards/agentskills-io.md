# agentskills.io standards reference

This file records the external skill standards that `ilk-skills` aims to be
compatible with, plus the date those standards were last reviewed. When the
upstream specification changes, re-read these pages and update both this file
and `skill-compliance.md`.

## Review date

- Last reviewed: **2026-05-28**

The pages listed below should be re-checked whenever agentskills.io
publishes a notable update, or when a new skill is added to this repo.

## Source links

| Page | URL | Used for |
|---|---|---|
| Specification | https://agentskills.io/specification | Skill directory layout, `SKILL.md` frontmatter fields, allowed file types. |
| Overview / Home | https://agentskills.io/home | High-level positioning of the skill ecosystem. |
| Best practices | https://agentskills.io/skill-creation/best-practices | Progressive disclosure, description length, path portability, runtime hygiene. |

If additional agentskills.io pages are consulted during implementation, add
them as new rows above with a short "used for" note.

## Constraints this repo applies

Derived from the pages above; kept here so future agents do not need to
re-read the upstream pages to know which rules currently shape this repo.

- `SKILL.md` frontmatter uses only the agentskills.io-recognized fields
  (`name`, `description`, and any documented optional fields). Non-standard
  fields such as host-specific runtime hints are kept out of frontmatter.
- Skill `description` stays short enough to act as a router hint (one
  paragraph), with longer guidance moved into the body.
- `SKILL.md` bodies favor progressive disclosure: the entry-point file
  stays scannable, and deeper material lives in companion files under the
  skill directory (e.g. `references/`).
- Path examples in skill docs do not hardcode a single host's layout when
  the skill actually supports multiple hosts (Cursor, Claude Code, Codex).
- Runtime artifacts (logs, PID files, queue state) are not committed inside
  the skill package; they live under the per-user runtime root.

## Future standards

If this repo adopts an additional skill standard (for example a different
marketplace specification, a security baseline, or an org-internal policy),
add a sibling file under `docs/standards/` and link to it from the
compliance table in `skill-compliance.md`. Do not overload this file with
unrelated standards.
