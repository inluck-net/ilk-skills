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
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Default CCSwitch paths ──────────────────────────────────────────────────

def _default_ccswitch_dir() -> Path:
    """Return the platform-specific CCSwitch config directory."""
    home = Path.home()
    return home / ".cc-switch"


CCSWITCH_DIR = _default_ccswitch_dir()
CCSWITCH_DB = CCSWITCH_DIR / "cc-switch.db"
CCSWITCH_SETTINGS = CCSWITCH_DIR / "settings.json"


# ── Data model ──────────────────────────────────────────────────────────────

@dataclass
class Provider:
    """A CCSwitch provider record mapped to worker env fields."""
    id: str
    name: str
    app_type: str
    category: str | None = None
    is_current: bool = False
    base_url: str = ""
    auth_token: str = ""
    model: str = ""
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def is_claude(self) -> bool:
        return self.app_type == "claude"

    @property
    def is_official(self) -> bool:
        return self.category == "official"

    @property
    def has_required_env(self) -> bool:
        return bool(self.base_url and self.auth_token and self.model)


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


# ── Provider storage parsing ────────────────────────────────────────────────

# Fields in settings_config.env that map to worker bootstrap env vars.
_WORKER_ENV_KEYS = {
    "ANTHROPIC_BASE_URL": "base_url",
    "ANTHROPIC_AUTH_TOKEN": "auth_token",
    "ANTHROPIC_MODEL": "model",
}

# Additional env keys that are useful metadata but not required for bootstrap.
_EXTRA_ENV_KEYS = {
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_REASONING_MODEL",
}


def _parse_settings_config(raw: str | None) -> dict:
    """Parse the JSON settings_config column, returning {} on null/invalid."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _extract_env(settings_config: dict) -> dict[str, str]:
    """Extract the env block from settings_config, skipping empty values."""
    env = settings_config.get("env", {})
    if not isinstance(env, dict):
        return {}
    return {k: v for k, v in env.items() if isinstance(v, str) and v}


def parse_providers_from_db(db_path: Path) -> list[Provider]:
    """Read all Claude-compatible providers from the CCSwitch SQLite database.

    Returns a list of Provider dataclasses.  Only providers where
    app_type == 'claude' are included.  The auth_token field is populated
    (it lives in the dataclass for export) but is never printed by this
    module's human-facing output.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"CCSwitch database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, app_type, name, settings_config, category, is_current "
            "FROM providers"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    providers: list[Provider] = []
    for row in rows:
        pid, app_type, name, raw_config, category, is_current = row
        config = _parse_settings_config(raw_config)
        env = _extract_env(config)

        # Map known env keys to provider fields.
        base_url = env.pop("ANTHROPIC_BASE_URL", "")
        auth_token = env.pop("ANTHROPIC_AUTH_TOKEN", "")
        model = env.pop("ANTHROPIC_MODEL", "")

        # Remaining env keys become extra_env.
        extra = {k: v for k, v in env.items() if k in _EXTRA_ENV_KEYS}

        providers.append(Provider(
            id=pid or "",
            name=name or pid or "(unnamed)",
            app_type=app_type or "",
            category=category,
            is_current=bool(is_current),
            base_url=base_url,
            auth_token=auth_token,
            model=model,
            extra_env=extra,
        ))

    return providers


# ── Subcommands ─────────────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> None:
    """List discovered Claude providers (redacted)."""
    ccswitch_dir = Path(args.ccswitch_dir)
    check_ccswitch_dir(ccswitch_dir)

    db_path = ccswitch_dir / "cc-switch.db"
    providers = parse_providers_from_db(db_path)
    claude_providers = [p for p in providers if p.is_claude]

    if not claude_providers:
        print("No Claude providers found in CCSwitch database.")
        return

    if args.format == "json":
        # Redacted JSON output.
        rows = []
        for p in claude_providers:
            rows.append({
                "id": p.id,
                "name": p.name,
                "category": p.category,
                "is_current": p.is_current,
                "base_url": p.base_url,
                "auth_token": mask_token(p.auth_token),
                "model": p.model,
            })
        print(json.dumps(rows, indent=2))
    else:
        # Human-readable table.
        for p in claude_providers:
            current = " *" if p.is_current else ""
            official = " [official]" if p.is_official else ""
            print(f"  {p.id}{current}{official}")
            print(f"    name:       {p.name}")
            print(f"    base_url:   {p.base_url or '(not set)'}")
            print(f"    auth_token: {mask_token(p.auth_token)}")
            print(f"    model:      {p.model or '(not set)'}")
            if p.extra_env:
                for k, v in p.extra_env.items():
                    print(f"    {k}: {v}")
            print()


def cmd_export(args: argparse.Namespace) -> None:
    """Export a selected provider for bootstrap consumption."""
    ccswitch_dir = Path(args.ccswitch_dir)
    check_ccswitch_dir(ccswitch_dir)

    if not args.provider:
        print("error: --provider is required for export", file=sys.stderr)
        sys.exit(2)

    db_path = ccswitch_dir / "cc-switch.db"
    providers = parse_providers_from_db(db_path)
    claude_providers = [p for p in providers if p.is_claude]

    # Match by id or name (case-insensitive).
    target = args.provider
    match = None
    for p in claude_providers:
        if p.id == target or p.name.lower() == target.lower():
            match = p
            break

    if not match:
        print(f"error: provider '{target}' not found", file=sys.stderr)
        print("Available Claude providers:", file=sys.stderr)
        for p in claude_providers:
            print(f"  {p.id}  ({p.name})", file=sys.stderr)
        sys.exit(1)

    if not match.has_required_env:
        missing = []
        if not match.base_url:
            missing.append("ANTHROPIC_BASE_URL")
        if not match.auth_token:
            missing.append("ANTHROPIC_AUTH_TOKEN")
        if not match.model:
            missing.append("ANTHROPIC_MODEL")
        print(f"error: provider '{match.name}' is missing required fields: "
              f"{', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # Build the export payload.
    output = {
        "id": match.id,
        "name": match.name,
        "ANTHROPIC_BASE_URL": match.base_url,
        "ANTHROPIC_AUTH_TOKEN": match.auth_token if args.machine else mask_token(match.auth_token),
        "ANTHROPIC_MODEL": match.model,
    }
    if match.extra_env:
        output["extra_env"] = match.extra_env

    print(json.dumps(output, indent=2))


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
