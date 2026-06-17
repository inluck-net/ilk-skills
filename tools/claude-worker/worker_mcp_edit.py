#!/usr/bin/env python3
"""Add an MCP server to the loop WORKER home correctly.

Codifies the procedure that was done by hand on 2026-06-13 (and got the wrong
config target twice before that): the loop worker runs with
``CLAUDE_CONFIG_DIR=~/.claude-worker`` and reads ITS OWN ``.claude.json``
``mcpServers`` — not the interactive ``~/.claude.json``. So adding an MCP for
the loop means editing the worker home, and for an OAuth MCP (figma) it means
copying ONLY that server's ``mcpOAuth`` token into the worker's
``.credentials.json`` — NEVER ``claudeAiOauth``, which would inject the
planner's Claude identity and break the worker's deliberate isolation
(see ``claude-worker.ps1``).

Pure, unit-testable core. The ``ilk-worker-mcp.ps1`` / ``.sh`` wrappers are thin
shells over the ``main()`` CLI here.

Files read/written with ``utf-8-sig`` on read (BOM-tolerant) and BOM-free
``utf-8`` on write. Atomic writes (temp file + ``os.replace``).
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

# Known MCP server presets (mirror the shapes in ~/.claude.json).
PRESETS: dict[str, dict] = {
    "figma": {"type": "http", "url": "https://mcp.figma.com/mcp"},
    "chrome-devtools": {
        "type": "stdio",
        "command": "cmd",
        "args": ["/c", "npx", "chrome-devtools-mcp@latest",
                 "--browserUrl", "http://localhost:9222"],
        "env": {},
    },
}

# MCP servers that authenticate via OAuth (need an mcpOAuth token copied).
OAUTH_SERVERS = {"figma"}


def resolve_worker_home(explicit: str | None = None) -> Path:
    """explicit arg -> $CLAUDE_WORKER_HOME -> ~/.claude-worker."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("CLAUDE_WORKER_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude-worker"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _atomic_write_json(path: Path, obj: dict, *, restrict: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # BOM-free
            json.dump(obj, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if restrict:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def add_server(worker_home: str | os.PathLike, name: str, server_obj: dict) -> Path:
    """Write ``server_obj`` into ``<worker_home>/.claude.json`` mcpServers.

    Preserves all other keys; idempotent. Returns the .claude.json path.
    """
    home = Path(worker_home)
    claude_json = home / ".claude.json"
    data = _read_json(claude_json)
    if not isinstance(data, dict):
        data = {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[name] = server_obj
    data["mcpServers"] = servers
    _atomic_write_json(claude_json, data)
    return claude_json


def copy_server_oauth(
    worker_home: str | os.PathLike,
    user_cred_path: str | os.PathLike,
    name: str,
) -> Path | None:
    """Copy ONLY ``name``'s mcpOAuth entry from the user credentials into the
    worker ``.credentials.json``.

    NEVER copies ``claudeAiOauth`` (preserves the worker's Claude-identity
    isolation). Returns the worker .credentials.json path, or None if the user
    file has no matching mcpOAuth entry.
    """
    home = Path(worker_home)
    user = _read_json(Path(user_cred_path))
    user_oauth = user.get("mcpOAuth") if isinstance(user, dict) else None
    if not isinstance(user_oauth, dict):
        return None
    matched = {
        k: v for k, v in user_oauth.items()
        if k == name or k.startswith(name + "|")
    }
    if not matched:
        return None

    worker_cred = home / ".credentials.json"
    existing = _read_json(worker_cred)
    if not isinstance(existing, dict):
        existing = {}
    # Hard guarantee: never carry the planner's Claude identity into the worker.
    existing.pop("claudeAiOauth", None)
    oauth = existing.get("mcpOAuth")
    if not isinstance(oauth, dict):
        oauth = {}
    oauth.update(matched)
    existing["mcpOAuth"] = oauth
    assert "claudeAiOauth" not in existing, "worker creds must not carry claudeAiOauth"
    _atomic_write_json(worker_cred, existing, restrict=True)
    return worker_cred


def worker_servers(worker_home: str | os.PathLike) -> list[str]:
    data = _read_json(Path(worker_home) / ".claude.json")
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return sorted(servers.keys()) if isinstance(servers, dict) else []


def _default_user_cred() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Add/list/verify MCP servers in the loop worker home.")
    parser.add_argument("--worker-home", default=None, help="Override worker home dir.")
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Add a known MCP server to the worker.")
    p_add.add_argument("name", help="Server name (e.g. figma, chrome-devtools).")
    p_add.add_argument("--from-user", action="store_true",
                       help="Also copy this server's mcpOAuth from ~/.claude/.credentials.json.")
    p_add.add_argument("--user-cred", default=None, help="Override user credentials path.")

    sub.add_parser("list", help="List the worker's MCP servers (JSON).")
    sub.add_parser("verify", help="Run `claude mcp list` under the worker config dir.")

    args = parser.parse_args()
    home = resolve_worker_home(args.worker_home)

    if args.cmd == "add":
        preset = PRESETS.get(args.name)
        if preset is None:
            print(f"ERROR: unknown server '{args.name}'. Known: {sorted(PRESETS)}")
            return 2
        add_server(home, args.name, preset)
        msg = {"worker_home": str(home), "added": args.name, "oauth_copied": False}
        if args.from_user and args.name in OAUTH_SERVERS:
            cred = Path(args.user_cred).expanduser() if args.user_cred else _default_user_cred()
            out = copy_server_oauth(home, cred, args.name)
            msg["oauth_copied"] = out is not None
        print(json.dumps(msg))
        return 0

    if args.cmd == "list":
        print(json.dumps({"worker_home": str(home), "mcpServers": worker_servers(home)}))
        return 0

    if args.cmd == "verify":
        import subprocess
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(home)}
        try:
            proc = subprocess.run(["claude", "mcp", "list"], env=env, text=True,
            encoding="utf-8", errors="replace",
                                  capture_output=True, timeout=60)
            print(proc.stdout.strip() or proc.stderr.strip())
            return proc.returncode
        except (OSError, subprocess.SubprocessError) as e:
            print(f"ERROR: could not run 'claude mcp list': {e}")
            return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
