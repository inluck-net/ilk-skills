#!/usr/bin/env python3
"""rsi_switch — one command to pause, park, off, resume and report RSI.

Usage:
  rsi_switch.py pause [--for DURATION] [--reason TEXT] [--by NAME]
  rsi_switch.py park  [--for DURATION] [--reason TEXT] [--by NAME]
  rsi_switch.py off   [--reason TEXT]
  rsi_switch.py resume
  rsi_switch.py status [--json]

DURATION is like ``30m``, ``2h``, ``1d``.

Levels:
  pause  — stops autoplan starts; does not touch masters
  park   — stops autoplan starts AND parks every auto-planned queued/active master
  off    — creates autoplan.disabled (kill switch; resume removes it)

Never kills a live runner — use /ilk-stop for that.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Ensure toolkit scripts are importable.
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ilk_audit import write_audit  # noqa: E402
from ilk_paths import ilk_data_root  # noqa: E402


# ── duration parsing ─────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_duration(text: str) -> timedelta:
    """Parse a duration like '30m', '2h', '1d' into a timedelta."""
    m = _DURATION_RE.match(text)
    if not m:
        raise ValueError(f"invalid duration: {text!r} (expected like 30m, 2h, 1d)")
    return timedelta(seconds=int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()])


# ── file helpers ─────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON file; return None on any parse/IO error."""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically (write to tmp then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# ── toolkit plans resolution ─────────────────────────────────────────────


def _find_toolkit_plans(data_root: Path) -> Path | None:
    """Find the toolkit project's plans dir.

    The toolkit project is identified by having ``commands/ilk-plan.md``
    somewhere in its tree.  We look for it via the project key that
    ilk_paths resolves for the current working directory, then check if
    that project's plans dir contains masters with ``auto_planned``.
    """
    # Walk up from cwd looking for a project with commands/ilk-plan.md
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "commands" / "ilk-plan.md").is_file():
            # Found toolkit project — resolve its plans dir
            from ilk_paths import find_plans_dir
            plans_dir, _ = find_plans_dir(parent)
            if plans_dir:
                return Path(plans_dir)
    return None


def _auto_planned_queued_masters(plans_dir: Path) -> list[str]:
    """Return names of queued/active masters with auto_planned: true."""
    from plan_status import normalize_master_status, parse_frontmatter

    names = []
    for p in sorted(plans_dir.glob("MASTER-*.md")):
        try:
            fm = parse_frontmatter(p.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        status = normalize_master_status(fm.get("status") or "")
        if status not in ("queued", "active"):
            continue
        if str(fm.get("auto_planned", "")).lower() == "true":
            names.append(p.name)
    return names


def _run_park_master(plans_dir: Path, master: str, reason: str, *,
                     unpark: bool = False) -> int:
    """Run park_master.py with the given arguments."""
    park_script = _SCRIPTS / "park_master.py"
    cmd = [sys.executable, str(park_script),
           "--plans-dir", str(plans_dir),
           "--master", master]
    if unpark:
        cmd.append("--unpark")
    else:
        cmd.extend(["--reason", reason, "--auto", "--yield"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode


# ── commands ─────────────────────────────────────────────────────────────


def _cmd_pause(args: argparse.Namespace, data_root: Path, level: str) -> int:
    """Pause or park RSI."""
    now = datetime.now(timezone.utc)
    paused_file = data_root / "autoplan" / "paused.json"

    entry: dict[str, Any] = {
        "by": args.by or os.environ.get("USER", "unknown"),
        "reason": args.reason or level,
        "since": now.isoformat(),
        "level": level,
    }

    if args.for_:
        try:
            dur = _parse_duration(args.for_)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        entry["until"] = (now + dur).isoformat()

    parked_masters: list[str] = []

    if level == "park":
        plans_dir = _find_toolkit_plans(data_root)
        if plans_dir:
            auto_masters = _auto_planned_queued_masters(plans_dir)
            for master in auto_masters:
                rc = _run_park_master(plans_dir, master,
                                      f"rsi park: {entry['reason']}")
                if rc == 0:
                    parked_masters.append(master)
        if parked_masters:
            entry["parked_masters"] = parked_masters

    _write_json(paused_file, entry)

    audit_kind = "rsi-parked" if level == "park" else "rsi-paused"
    write_audit(audit_kind, "ilk-skills", root=data_root,
                reason=entry["reason"], level=level)

    print(f"RSI: {level} by {entry['by']}", end="")
    if "until" in entry:
        until_dt = datetime.fromisoformat(entry["until"])
        print(f" until {until_dt.strftime('%H:%M')}", end="")
    print(f" (reason: {entry['reason']})", end="")
    if parked_masters:
        print(f" — {len(parked_masters)} auto-planned master(s) parked", end="")
    print()
    return 0


def _cmd_off(args: argparse.Namespace, data_root: Path) -> int:
    """Create autoplan.disabled."""
    disabled_file = data_root / "autoplan.disabled"
    reason = args.reason or "off"
    disabled_file.parent.mkdir(parents=True, exist_ok=True)
    disabled_file.write_text(reason + "\n", encoding="utf-8")

    write_audit("rsi-off", "ilk-skills", root=data_root, reason=reason)
    print(f"RSI: off (reason: {reason})")
    return 0


def _cmd_resume(data_root: Path) -> int:
    """Remove autoplan.disabled and paused.json; unpark parked masters."""
    disabled_file = data_root / "autoplan.disabled"
    paused_file = data_root / "autoplan" / "paused.json"

    had_disabled = disabled_file.exists()
    had_paused = paused_file.exists()

    # Read parked_masters before removing paused.json
    parked_masters: list[str] = []
    if had_paused:
        paused_data = _read_json(paused_file)
        if paused_data and "parked_masters" in paused_data:
            parked_masters = list(paused_data["parked_masters"])

    # Remove files
    if had_disabled:
        disabled_file.unlink(missing_ok=True)
    if had_paused:
        paused_file.unlink(missing_ok=True)

    # Unpark parked masters
    if parked_masters:
        plans_dir = _find_toolkit_plans(data_root)
        if plans_dir:
            for master in parked_masters:
                _run_park_master(plans_dir, master, "", unpark=True)

    if not had_disabled and not had_paused:
        # Nothing to do — exit 0 silently
        print("RSI: on (nothing to resume)")
        return 0

    write_audit("rsi-resumed", "ilk-skills", root=data_root)
    print("RSI: on (resumed)")
    return 0


def _cmd_status(args: argparse.Namespace, data_root: Path) -> int:
    """Print one line describing RSI state."""
    paused_file = data_root / "autoplan" / "paused.json"
    disabled_file = data_root / "autoplan.disabled"

    # Off takes priority
    if disabled_file.exists():
        reason = ""
        try:
            reason = disabled_file.read_text(encoding="utf-8-sig").strip()
        except OSError:
            pass
        line = f"RSI: off{f' ({reason})' if reason else ''}"
        if hasattr(args, "json") and args.json:
            print(json.dumps({"state": "off", "reason": reason}))
        else:
            print(line)
        return 0

    # Paused (may be expired)
    if paused_file.exists():
        paused_data = _read_json(paused_file)
        if paused_data is None:
            # Corrupt file — fail closed
            line = "RSI: paused (unreadable pause file)"
            if hasattr(args, "json") and args.json:
                print(json.dumps({"state": "paused", "error": "unreadable"}))
            else:
                print(line)
            return 0

        level = paused_data.get("level", "pause")
        by = paused_data.get("by", "unknown")
        reason = paused_data.get("reason", "")
        until_str = paused_data.get("until")
        parked = paused_data.get("parked_masters", [])

        if until_str:
            try:
                until_dt = datetime.fromisoformat(until_str)
                if until_dt.tzinfo is None:
                    until_dt = until_dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if until_dt <= now:
                    line = "RSI: on (pause expired, clears at next tick)"
                    if hasattr(args, "json") and args.json:
                        print(json.dumps({"state": "on", "note": "pause expired"}))
                    else:
                        print(line)
                    return 0
                until_display = until_dt.strftime("%H:%M")
            except (ValueError, TypeError):
                until_display = until_str
        else:
            until_display = None

        parts = [f"RSI: {level} by {by}"]
        if until_display:
            parts.append(f"until {until_display}")
        if reason:
            parts.append(f"(reason: {reason})")
        if parked:
            parts.append(f"— {len(parked)} auto-planned master(s) parked")

        line = " ".join(parts)
        if hasattr(args, "json") and args.json:
            out: dict[str, Any] = {"state": level, "by": by, "reason": reason}
            if until_display:
                out["until"] = until_display
            if parked:
                out["parked_masters"] = parked
            print(json.dumps(out))
        else:
            print(line)
        return 0

    # On — try to gather autoplan state for a richer status
    state_file = data_root / "autoplan" / "state.json"
    state = _read_json(state_file) or {}
    idle_cycles = state.get("idle_cycles", 0)

    # Eligible candidates (best effort)
    eligible = 0
    try:
        # Try to import autoplan_rails for candidate count
        _self_scripts = Path(__file__).resolve().parent
        if str(_self_scripts) not in sys.path:
            sys.path.insert(0, str(_self_scripts))
        import autoplan_rails
        backlog_dir = data_root / "backlog"
        if backlog_dir.is_dir():
            candidates_file = backlog_dir / "candidates.json"
            if candidates_file.is_file():
                entries = json.loads(candidates_file.read_text(encoding="utf-8-sig"))
                eligible = len(autoplan_rails.rank(entries))
    except (OSError, json.JSONDecodeError, ImportError):
        pass

    line = f"RSI: on — idle {idle_cycles}/3, {eligible} eligible candidates"
    if hasattr(args, "json") and args.json:
        print(json.dumps({"state": "on", "idle_cycles": idle_cycles,
                          "eligible_candidates": eligible}))
    else:
        print(line)
    return 0


# ── main ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    sub = ap.add_subparsers(dest="command")

    # pause
    p_pause = sub.add_parser("pause", help="pause autoplan starts")
    p_pause.add_argument("--for", dest="for_", default=None,
                         help="duration (e.g. 30m, 2h, 1d)")
    p_pause.add_argument("--reason", default=None)
    p_pause.add_argument("--by", default=None)

    # park
    p_park = sub.add_parser("park", help="pause AND park auto-planned masters")
    p_park.add_argument("--for", dest="for_", default=None,
                        help="duration (e.g. 30m, 2h, 1d)")
    p_park.add_argument("--reason", default=None)
    p_park.add_argument("--by", default=None)

    # off
    p_off = sub.add_parser("off", help="create autoplan.disabled (kill switch)")
    p_off.add_argument("--reason", default=None)

    # resume
    sub.add_parser("resume", help="remove pause/off, unpark parked masters")

    # status
    p_status = sub.add_parser("status", help="print RSI state")
    p_status.add_argument("--json", action="store_true", dest="json")

    a = ap.parse_args(argv)

    if not a.command:
        ap.print_help()
        return 1

    data_root = ilk_data_root()

    if a.command == "pause":
        return _cmd_pause(a, data_root, "pause")
    elif a.command == "park":
        return _cmd_pause(a, data_root, "park")
    elif a.command == "off":
        return _cmd_off(a, data_root)
    elif a.command == "resume":
        return _cmd_resume(data_root)
    elif a.command == "status":
        return _cmd_status(a, data_root)
    else:
        ap.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())