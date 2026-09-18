#!/usr/bin/env bash
# claude-manager — launch Claude Code under the manager role's home.
# Generated from tools/claude-worker/role-registry.json (role "manager",
# home ~/.claude-manager, tier "manager"); installed on PATH by
# install.sh --only-path. Replaces the hand-written stopgap that lived at
# ~/.local/bin/claude-manager until 2026-09-18.
# Design: docs/role-tier-registry-design.md.
exec "$HOME/.local/bin/claude-worker" --home "$HOME/.claude-manager" "$@"
