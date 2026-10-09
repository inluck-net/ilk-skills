#!/usr/bin/env python3
"""Pull a remote host's backlog rows and merge them into the local backlog.

The backlog is per host (`~/.ilk-data/ilk-skills-improvements/candidates.json`).
Autoplan runs only on the RSI host (chad-mbp). This module pulls rezmac's
rows over ssh, merges them tagged with ``relations.origin_host``, and writes
the local file only when both sides parsed.

CLI::

    python3 backlog_sync.py --auto [--data-root R]
    python3 backlog_sync.py --host H [--backlog-dir X] [--force]

Python 3.9 compatible.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

# ── imports ──────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve()
_WATCHDOG_SCRIPTS = _HERE.parent.parent.parent / "ilk-watchdog" / "scripts"
_SELF_IMPROVE_SCRIPTS = _HERE.parent.parent.parent / "ilk-self-improve" / "scripts"
for _p in (_WATCHDOG_SCRIPTS, _SELF_IMPROVE_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import triage_backlog  # noqa: E402

# ── constants ────────────────────────────────────────────────────────────────

_DEFAULT_BACKLOG_DIR = Path.home() / ".ilk-data" / "ilk-skills-improvements"
_REMOTE_CMD = "hostname; cat ~/.ilk-data/ilk-skills-improvements/candidates.json"
_SSH_TIMEOUT = 10
_DEFAULT_MIN_INTERVAL_MIN = 30


# ── pure merge ───────────────────────────────────────────────────────────────


def merge_remote(
    local: list[dict[str, Any]],
    remote: list[dict[str, Any]],
    host: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Merge remote rows into local, tagging with origin_host.

    Pure: reads no files, writes nothing.

    Returns (merged, added_ids, refreshed_ids).
    """
    local_by_id: dict[str, dict[str, Any]] = {}
    for e in local:
        eid = e.get("id")
        if eid:
            local_by_id[eid] = e

    added: list[str] = []
    refreshed: list[str] = []
    merged = list(local)  # shallow copy of list, not entries

    for r in remote:
        rid = r.get("id")
        if not rid or not isinstance(rid, str):
            continue  # skip rows without string id

        if rid not in local_by_id:
            # New row: append with origin_host
            entry = copy.deepcopy(r)
            rels = entry.setdefault("relations", {})
            rels["origin_host"] = host
            ev = entry.get("evidence")
            if isinstance(ev, dict):
                ev["host"] = host
            merged.append(entry)
            added.append(rid)
        else:
            local_entry = local_by_id[rid]
            local_origin = local_entry.get("relations", {}).get("origin_host")
            if local_origin == host:
                # Refresh from remote
                changed = False
                remote_last = r.get("last_seen", "")
                local_last = local_entry.get("last_seen", "")
                if remote_last > local_last:
                    local_entry["last_seen"] = remote_last
                    changed = True
                remote_count = r.get("seen_count", 0)
                local_count = local_entry.get("seen_count", 0)
                if remote_count > local_count:
                    local_entry["seen_count"] = remote_count
                    changed = True
                for field in ("title", "gap", "evidence"):
                    rv = r.get(field)
                    if rv and rv != local_entry.get(field):
                        local_entry[field] = rv
                        changed = True
                if changed:
                    refreshed.append(rid)
            else:
                # Same failure on both hosts: add to seen_on_hosts
                rels = local_entry.setdefault("relations", {})
                hosts = rels.setdefault("seen_on_hosts", [])
                if host not in hosts:
                    hosts.append(host)
                    hosts.sort()

    return merged, added, refreshed


# ── pull ─────────────────────────────────────────────────────────────────────


def pull(
    host: str,
    backlog_dir: Path | None = None,
    *,
    state_dir: Path | None = None,
    runner: Callable = subprocess.run,
    min_interval_min: int = _DEFAULT_MIN_INTERVAL_MIN,
    now: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch a remote host's backlog, merge, and write.

    Returns a result dict with at least ``status``.
    """
    if backlog_dir is None:
        backlog_dir = _DEFAULT_BACKLOG_DIR
    else:
        backlog_dir = Path(backlog_dir)

    if state_dir is None:
        state_dir = backlog_dir.parent / "backlog-sync"
    else:
        state_dir = Path(state_dir)

    if now is None:
        now = time.time()

    # Throttle check
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"{host}.json"
    if not force and state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            last_ok = state.get("last_ok_epoch", 0)
            if now - last_ok < min_interval_min * 60:
                return {"status": "throttled"}
        except (json.JSONDecodeError, OSError):
            pass

    # Run ssh
    argv = [
        "ssh", "-o", f"ConnectTimeout={_SSH_TIMEOUT}",
        "-o", "BatchMode=yes",
        host, _REMOTE_CMD,
    ]
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return {"status": "unreadable", "error": str(exc)}

    # Check for self
    stdout = proc.stdout
    lines = stdout.split("\n", 1)
    remote_hostname = lines[0].strip() if lines else ""
    if remote_hostname == socket.gethostname():
        return {"status": "self"}

    # Unreachable (ssh exit 255)
    if proc.returncode == 255:
        return {"status": "unreachable"}

    # Other non-zero exit
    if proc.returncode != 0:
        return {"status": "unreadable", "exit_code": proc.returncode,
                "stderr": proc.stderr[:200]}

    # Parse remote body
    remote_body = lines[1] if len(lines) > 1 else ""
    try:
        remote_entries = json.loads(remote_body)
        if not isinstance(remote_entries, list):
            return {"status": "unreadable", "error": "remote body not a list"}
    except (json.JSONDecodeError, ValueError):
        return {"status": "unreadable", "error": "remote body not JSON"}

    # Read local with strict reader
    try:
        local_entries = triage_backlog.read_backlog_strict(backlog_dir)
    except Exception:
        return {"status": "unreadable", "error": "local read failed"}

    # Merge
    merged, added, refreshed = merge_remote(local_entries, remote_entries, host)

    # Write with lock
    lock_path = backlog_dir / "candidates.json.lock"
    backlog_dir.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            _save_raw(backlog_dir, merged)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
    except OSError as exc:
        return {"status": "unreadable", "error": f"write failed: {exc}"}

    # Update throttle state
    state_data = {
        "last_ok_epoch": now,
        "remote_rows": len(remote_entries),
        "added": len(added),
    }
    try:
        fd, tmp = tempfile.mkstemp(dir=str(state_dir), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state_data, fh)
        os.replace(tmp, str(state_file))
    except OSError:
        pass

    return {
        "status": "ok",
        "host": host,
        "remote_rows": len(remote_entries),
        "added": added,
        "refreshed": refreshed,
    }


def _save_raw(backlog_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Atomically write the JSON array to disk."""
    p = backlog_dir / "candidates.json"
    fd, tmp = tempfile.mkstemp(dir=str(backlog_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull remote backlog rows and merge into local.",
    )
    parser.add_argument("--auto", action="store_true",
                        help="Auto-detect RSI host and pull from remotes")
    parser.add_argument("--host", default=None,
                        help="Pull from a specific host")
    parser.add_argument("--data-root", default=None,
                        help="Data root (default: ilk_paths.ilk_data_root())")
    parser.add_argument("--backlog-dir", default=None,
                        help="Backlog directory")
    parser.add_argument("--force", action="store_true",
                        help="Ignore throttle")
    args = parser.parse_args()

    if args.auto:
        # Resolve data root
        if args.data_root:
            data_root = Path(args.data_root)
        else:
            try:
                import ilk_paths
                data_root = ilk_paths.ilk_data_root()
            except Exception:
                data_root = Path.home() / ".ilk-data"

        # Check autoplan enablement
        import autoplan
        rsi_key, err = autoplan._find_toolkit_project(data_root)
        if rsi_key is None:
            print(json.dumps({"status": "not-rsi-host"}))
            return 0

        # Resolve toolkit repo and hosts
        toolkit_repo = autoplan._resolve_toolkit_repo(data_root, rsi_key)
        if toolkit_repo is None:
            print(json.dumps({"status": "not-rsi-host"}))
            return 0

        launch_file = Path(toolkit_repo) / ".ilk-launch.json"
        try:
            launch = json.loads(launch_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(json.dumps({"status": "not-rsi-host"}))
            return 0

        hosts = launch.get("ship", {}).get("hosts", [])
        if len(hosts) < 2:
            print(json.dumps({"status": "not-rsi-host"}))
            return 0

        # Pull from hosts[1:] (skip local host)
        remote_hosts = hosts[1:]
        backlog_dir = Path(args.backlog_dir) if args.backlog_dir else None
        results = []
        any_unreachable = False
        any_unreadable = False

        for h in remote_hosts:
            r = pull(h, backlog_dir, force=args.force)
            results.append(r)
            if r["status"] == "unreachable":
                any_unreachable = True
            elif r["status"] == "unreadable":
                any_unreadable = True

        print(json.dumps(results))
        if any_unreadable:
            return 3
        if any_unreachable:
            return 4
        return 0

    elif args.host:
        backlog_dir = Path(args.backlog_dir) if args.backlog_dir else None
        result = pull(args.host, backlog_dir, force=args.force)
        print(json.dumps(result))
        if result["status"] == "unreadable":
            return 3
        if result["status"] == "unreachable":
            return 4
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())