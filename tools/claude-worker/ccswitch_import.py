#!/usr/bin/env python3
"""Discover CCSwitch Claude providers and export them for worker bootstrap.

Read-only helper that lists Claude-compatible providers from the local CCSwitch
configuration and emits safe, machine-readable output for the worker bootstrap
script.  Never mutates CCSwitch state or prints raw tokens by default.

SAFETY (non-negotiable):
  * Read-only access to CCSwitch config files.  Never writes to
    ~/.cc-switch/ or any CCSwitch database.
  * Token values are redacted in human output.  Raw token output requires
    the explicit --machine flag and is only intended for piping into
    bootstrap.sh / bootstrap.ps1.
  * If the CCSwitch config is missing or unreadable, report a clear error
    instead of crashing with a traceback.

Usage:
  python3 ccswitch_import.py list                   # redacted human list
  python3 ccswitch_import.py list --format json     # redacted JSON
  python3 ccswitch_import.py export --provider <id> # JSON for bootstrap
  python3 ccswitch_import.py export --provider <id> --machine  # raw tokens

Cross-platform: macOS (default paths), Windows (%USERPROFILE%), and explicit
--ccswitch-dir override.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

# ── Default CCSwitch paths ──────────────────────────────────────────────────

def _default_ccswitch_dir() -> Path:
    """Return the platform-specific CCSwitch config directory."""
    home = Path.home()
    return home / ".cc-switch"


CCSWITCH_DIR = _default_ccswitch_dir()
CCSWITCH_DB = CCSWITCH_DIR / "cc-switch.db"
CCSWITCH_SETTINGS = CCSWITCH_DIR / "settings.json"


# ── Helpers ─────────────────────────────────────────────────────────────────

def mask_token(value: str) -> str:
    """Return a length-bucketed placeholder that leaks nothing about the token."""
    if not value:
        return "(missing)"
    return f"***set ({len(value)} chars)***"


def check_ccswitch_dir(path: Path) -> None:
    """Validate that the CCSwitch directory and expected files exist."""
    if not path.is_dir():
        raise FileNotFoundError(
            f"CCSwitch directory not found: {path}\n"
            "Is CCSwitch installed?  Default location: ~/.cc-switch/"
        )
    db = path / "cc-switch.db"
    settings = path / "settings.json"
    if not db.exists() and not settings.exists():
        raise FileNotFoundError(
            f"No CCSwitch config found in {path}.\n"
            "Expected cc-switch.db or settings.json."
        )


# ── Subcommands ─────────────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> None:
    """List discovered Claude providers (redacted)."""
    ccswitch_dir = Path(args.ccswitch_dir)
    check_ccswitch_dir(ccswitch_dir)

    # Placeholder — step 1 will implement actual parsing.
    print(f"list: would scan {ccswitch_dir} for Claude providers")
    print("(parsing not yet implemented — this is the skeleton)")


def cmd_export(args: argparse.Namespace) -> None:
    """Export a selected provider for bootstrap consumption."""
    ccswitch_dir = Path(args.ccswitch_dir)
    check_ccswitch_dir(ccswitch_dir)

    if not args.provider:
        print("error: --provider is required for export", file=sys.stderr)
        sys.exit(2)

    # Placeholder — step 2 will implement normalization + export.
    print(f"export: would export provider '{args.provider}' from {ccswitch_dir}")
    print("(export not yet implemented — this is the skeleton)")


# ── CLI ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccswitch_import",
        description="Discover CCSwitch Claude providers for worker bootstrap.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--ccswitch-dir",
        default=str(CCSWITCH_DIR),
        help="CCSwitch config directory (default: platform-specific, "
             "typically ~/.cc-switch)",
    )

    sub = parser.add_subparsers(dest="command", help="available commands")

    # list
    p_list = sub.add_parser(
        "list",
        help="list discovered Claude providers (redacted)",
    )
    p_list.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="output format (default: human)",
    )

    # export
    p_export = sub.add_parser(
        "export",
        help="export a provider for bootstrap (--machine for raw tokens)",
    )
    p_export.add_argument(
        "--provider",
        required=True,
        help="provider id or name to export",
    )
    p_export.add_argument(
        "--machine",
        action="store_true",
        help="emit raw token (default: redacted; only use for piping to bootstrap)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        if args.command == "list":
            cmd_list(args)
        elif args.command == "export":
            cmd_export(args)
        else:
            parser.print_help()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
