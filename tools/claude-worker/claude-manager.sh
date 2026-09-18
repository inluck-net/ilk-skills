#!/usr/bin/env bash
# claude-manager — launch Claude Code under the manager role's home.
# Generated from tools/claude-worker/role-registry.json (role "manager",
# home ~/.claude-manager, tier "manager"); installed on PATH by
# install.sh --only-path. Replaces the hand-written stopgap that lived at
# ~/.local/bin/claude-manager until 2026-09-18.
# Design: docs/architecture/role-tier-registry-design.md.
#
# Passes --quiet so a routine manager launch prints nothing on success; the
# fail-closed preflight still reports problems to stderr and exits 3. For the
# full banner run: claude-worker --home ~/.claude-manager
exec "$HOME/.local/bin/claude-worker" --home "$HOME/.claude-manager" --quiet "$@"
