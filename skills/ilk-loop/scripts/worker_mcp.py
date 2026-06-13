#!/usr/bin/env python3
"""Report the loop WORKER's MCP server set.

The ilk loop runs via the ``claude-worker`` engine, which pins
``CLAUDE_CONFIG_DIR`` to a separate worker home (default ``~/.claude-worker``;
see ``tools/claude-worker/claude-worker.ps1``). The worker reads its OWN
``<worker-home>/.claude.json`` ``mcpServers`` map — NOT the interactive
``~/.claude.json`` that ``claude mcp list`` shows in a normal session.

``/ilk-plan`` step 4b must validate MCP-naming ``env_prereqs``/ACs against the
surface the loop actually runs in, so it uses this helper instead of the
interactive ``claude mcp list``. If the worker's ``mcpServers`` is empty, a
sub-plan that hard-gates on an MCP will stall (observed: uccargo, 2026-06-13 —
figma showed connected interactively but was absent in the worker).

Worker-home resolution (mirrors ``claude-worker.ps1`` / ``bootstrap.ps1``):
explicit arg -> ``$CLAUDE_WORKER_HOME`` -> ``~/.claude-worker``.

CLI:
    python worker_mcp.py list [--worker-home DIR]
        -> {"worker_home": "<abs>", "mcpServers": [<names>]}  (exit 0)

Reads ``.claude.json`` with ``utf-8-sig`` (zh-CN Windows configs may carry a
BOM). Stdout is ASCII-only and import is side-effect free.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def resolve_worker_home(explicit: str | None = None) -> Path:
    """Resolve the worker home: explicit arg -> $CLAUDE_WORKER_HOME -> ~/.claude-worker."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("CLAUDE_WORKER_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude-worker"


def worker_mcp_servers(worker_home: str | os.PathLike | None = None) -> list[str]:
    """Return the sorted list of MCP server names in the worker's .claude.json.

    Returns ``[]`` (gracefully) when the worker home, the ``.claude.json``, or
    its ``mcpServers`` key is missing/empty/malformed — the schedulers and the
    planner call this every time and must never crash on a fresh worker.
    """
    home = worker_home if isinstance(worker_home, Path) else resolve_worker_home(
        str(worker_home) if worker_home else None
    )
    claude_json = Path(home) / ".claude.json"
    try:
        data = json.loads(claude_json.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError):
        return []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return []
    return sorted(servers.keys())


def main() -> int:
    parser = argparse.ArgumentParser(description="Report the loop worker's MCP server set.")
    sub = parser.add_subparsers(dest="cmd")
    p_list = sub.add_parser("list", help="List the worker's MCP servers as JSON.")
    p_list.add_argument("--worker-home", default=None, help="Override worker home dir.")

    args = parser.parse_args()
    if args.cmd != "list":
        parser.print_help()
        return 1

    home = resolve_worker_home(args.worker_home)
    out = {"worker_home": str(home), "mcpServers": worker_mcp_servers(home)}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
