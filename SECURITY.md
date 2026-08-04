# Security

## Reporting a vulnerability

Please report suspected vulnerabilities privately rather than in a public
issue: open a [GitHub security
advisory](https://github.com/inluck-net/ilk-skills/security/advisories/new) on
this repo. Include what you ran, what happened, and the platform.

## What this toolkit does to your machine

ilk-skills is an automation toolkit that drives AI coding agents. Understand
these properties before running it:

- **The installer edits user-global agent instructions.** `install.sh --apply`
  writes a managed block into `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, and
  `~/.cursor/rules/ilk-auto-plan.mdc`. This affects **every** project on the
  machine. The block is delimited by `<!-- ilk:auto-plan:start -->` /
  `<!-- ilk:auto-plan:end -->`; set `auto_use_ilk_plan: false` in
  `conventions/config.yml` and re-run the installer to remove it.
- **Installed skills are symlinks into your clone.** Editing this working tree
  immediately changes the behavior of every host agent and any running loop.
  Treat the clone as executable code, not inert config.
- **The loop runs agent sessions unattended** and commits code. It is designed
  to run overnight without supervision, which means an agent's mistakes can
  reach your git history. Review what it ships.
- **`dangerous_paths.yaml`** (`skills/ilk-loop/templates/`) lists paths the
  loop refuses to touch. Review it against your own layout.

## Credentials and per-machine state

No credentials belong in this repo. Secrets and machine-specific state live
outside it, under `~/.ilk-data/`:

| Data | Location |
|---|---|
| Lark/Feishu app id + secret | `~/.ilk-data/ilk-lark-tickets/config.json` |
| Lark token cache | `~/.ilk-data/ilk-lark-tickets/.token_cache.json` |
| Real project paths | `skills/ilk-launcher/projects.json` (gitignored) |
| Per-checkout remote type | `.ilk-remote-type` (gitignored) |
| Lark tracker marker | `.lark-project` (gitignored) |

`.gitignore` covers each of these. If you add a new kind of per-machine state,
add it there in the same change and commit a `*.example.*` file instead.

Provider tokens (e.g. `ANTHROPIC_AUTH_TOKEN` for the `claude-worker` engine)
are read from the environment or the worker home's settings and are **masked**
in wrapper output. If you find a code path that prints one in cleartext, that
is a bug worth reporting privately.

## Supported versions

This is a solo/small-team toolkit released as rolling tags. Only the latest
tag is supported; there are no backported fixes.
