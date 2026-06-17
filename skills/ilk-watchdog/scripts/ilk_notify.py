"""Cross-platform desktop notification helper for ilk-watchdog/scheduler.

Sends a notification when a significant ilk event occurs (ship, blocked,
restart, postmortem-failed, queue-drained).  Designed to be fire-and-forget
— failure is swallowed, never alters control flow.

Platform backends
-----------------
- **macOS (darwin):** ``osascript -e 'display notification …'``
- **Windows (win32):** BurntToast (PowerShell) if available, else console line.
- **Linux:** ``notify-send`` if present, else console line.

All backends are no-ops when the platform backend is missing — a console
fallback line is always safe.

Environment
-----------
``ILK_NOTIFY`` — unset or ``1`` = on, ``0`` = off (suppress all output).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


# ── event formatting ────────────────────────────────────────────────

_EVENT_TITLES = {
    "ship": "ilk — shipped",
    "blocked": "ilk — blocked",
    "restart": "ilk — restarting",
    "postmortem-failed": "ilk — postmortem failed",
    "queue-drained": "ilk — queue drained",
}


def _format_message(event: str, project: str, detail: str | None = None) -> tuple[str, str]:
    """Return (title, body) for the notification."""
    title = _EVENT_TITLES.get(event, f"ilk — {event}")
    body = f"Project: {project}"
    if detail:
        body += f"\n{detail}"
    return title, body


# ── platform backends ───────────────────────────────────────────────

def _notify_darwin(title: str, body: str, *, dry_run: bool = False) -> str:
    """macOS notification via osascript."""
    script = f'display notification "{body}" with title "{title}"'
    cmd = ["osascript", "-e", script]
    if dry_run:
        return " ".join(cmd)
    subprocess.run(cmd, capture_output=True, timeout=10, encoding="utf-8", errors="replace")
    return ""


def _notify_win32(title: str, body: str, *, dry_run: bool = False) -> str:
    """Windows notification via BurntToast or console fallback."""
    # Try BurntToast first (PowerShell module).
    ps_cmd = (
        f"try {{ New-BurntToastNotification -Text '{title}', '{body}' -ErrorAction Stop; "
        f"write-output 'sent via BurntToast' }} catch {{ "
        f"write-output 'CONSOLE: {title} — {body}' }}"
    )
    cmd = ["powershell", "-NoProfile", "-Command", ps_cmd]
    if dry_run:
        return " ".join(cmd)
    try:
        subprocess.run(cmd, capture_output=True, timeout=15, encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _notify_linux(title: str, body: str, *, dry_run: bool = False) -> str:
    """Linux notification via notify-send or console fallback."""
    cmd = ["notify-send", title, body]
    if dry_run:
        return " ".join(cmd)
    try:
        subprocess.run(cmd, capture_output=True, timeout=10,
        encoding="utf-8", errors="replace",
                       stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except FileNotFoundError:
        # notify-send not installed — console fallback
        print(f"[ilk-notify] {title} — {body}")
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _notify_console(title: str, body: str, *, dry_run: bool = False) -> str:
    """Fallback: print to console."""
    line = f"[ilk-notify] {title} — {body}"
    if dry_run:
        return line
    print(line)
    return ""


_BACKENDS = {
    "darwin": _notify_darwin,
    "win32": _notify_win32,
    "linux": _notify_linux,
}


def _get_backend(platform: str):
    """Return the notification function for *platform*."""
    return _BACKENDS.get(platform, _notify_console)


# ── main ────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="ilk desktop notification helper")
    ap.add_argument("--event", required=True,
                    help="Event name (ship, blocked, restart, postmortem-failed, queue-drained)")
    ap.add_argument("--project", required=True,
                    help="Project name/key")
    ap.add_argument("--detail", default=None,
                    help="Optional detail string")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the platform command instead of executing")
    ap.add_argument("--platform", default=None,
                    help="Override sys.platform (for testing)")
    args = ap.parse_args()

    # Gate: ILK_NOTIFY=0 suppresses everything.
    if os.environ.get("ILK_NOTIFY", "1") == "0":
        return 0

    platform = args.platform or sys.platform
    title, body = _format_message(args.event, args.project, args.detail)
    backend = _get_backend(platform)

    output = backend(title, body, dry_run=args.dry_run)
    if output:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
