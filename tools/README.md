# tools/

One-off utilities that aren't part of the day-to-day skill surface but
operators occasionally need.

Anything here should:

- be invoked manually (the loop / watchdog / launcher never call it)
- have a `--dry-run` (or equivalent) default so accidental runs don't
  mutate state
- live outside `skills/` so it doesn't show up in Cursor / Claude Code
  skill discovery

## Index

### `migration/migrate_plans_to_external.py`

Moves a project's legacy in-tree `<git_root>/docs/plans/` into the new
externalised layout under `~/.ilk-data/projects/<key>/plans/`.

```powershell
# Dry-run (default)
python tools/migration/migrate_plans_to_external.py --project <path>

# Apply
python tools/migration/migrate_plans_to_external.py --project <path> --apply
```

Run this once per project, between batches, when the project's
`loop_status.py` output says `source: in-tree`. After it succeeds,
review and commit the resulting `git rm` deletions in the project repo.

See the script's own `--help` for all flags.

### `check_skill_frontmatter.py`

Validates every `skills/*/SKILL.md` against the agentskills.io frontmatter
baseline recorded in [`docs/standards/agentskills-io.md`](../docs/standards/agentskills-io.md):
required `name` matching the directory, non-empty `description` under
1024 characters, and no non-standard top-level `model:` field.

```bash
python3 tools/check_skill_frontmatter.py
```

Stdlib-only, read-only, exits non-zero on the first failing skill so it
can be wired into local pre-commit or CI checks without extra installs.
