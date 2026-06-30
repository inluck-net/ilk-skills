#!/usr/bin/env bash
# Add / list / verify an MCP server in the loop WORKER home (macOS/Linux).
#
# Thin wrapper over worker_mcp_edit.py. The loop worker runs with
# CLAUDE_CONFIG_DIR=<worker home> (default ~/.claude-worker) and reads its OWN
# .claude.json mcpServers — NOT ~/.claude.json. Use this to give the loop an
# MCP it can actually reach.
#
#   add <name> [--from-user]   Add a known server (chrome-devtools, playwright,
#                              figma). With --from-user, also copy that server's
#                              OAuth token from ~/.claude/.credentials.json
#                              (figma) — never the planner's Claude identity.
#   remove <name>              Remove a server from the worker (idempotent).
#   list                       Print the worker's MCP servers (JSON).
#   verify                     Run `claude mcp list` under the worker config dir.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/worker_mcp_edit.py"
[[ -f "$PY" ]] || { echo "worker_mcp_edit.py not found: $PY" >&2; exit 1; }

PYBIN="python3"
command -v python3 >/dev/null 2>&1 || PYBIN="python"

exec "$PYBIN" "$PY" "$@"
