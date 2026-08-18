"""ship_config — loader for the ship: block in .ilk-launch.json.

Loads and validates the project-level ship configuration using the same
3-location precedence as launch.sh:240-269 and doctor.py:670-680:

  1. <external_plans_dir>/.ilk-launch.json
  2. <project_root>/docs/plans/.ilk-launch.json
  3. <project_root>/.ilk-launch.json

Returns one of three result types:
  - ShipConfig     — valid, with resolved_path and location
  - NotConfigured  — no ship: key (degrade-to-default)
  - MalformedConfig — invalid schema (hard error)

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Tuple, Union


class Location(Enum):
    """Which of the three precedence locations the config was read from."""
    EXTERNAL_PLANS = 1
    DOCS_PLANS = 2
    PROJECT_ROOT = 3


@dataclass(frozen=True)
class ShipConfig:
    """Valid ship: block loaded from .ilk-launch.json."""
    ship: dict[str, Any]
    resolved_path: Path
    location: Location
    stale_exclusions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NotConfigured:
    """No ship: block found — degrade-to-default, not an error."""
    resolved_path: Optional[Path] = None
    location: Optional[Location] = None


@dataclass(frozen=True)
class MalformedConfig:
    """Invalid ship: block — hard error, names the offending key."""
    detail: str
    resolved_path: Path
    location: Location


ShipConfigResult = Union[ShipConfig, NotConfigured, MalformedConfig]


# ── Validation helpers ──────────────────────────────────────────────────────

def _validate_ship_block(ship: Any, resolved_path: Path, location: Location,
                         staleness_days: int) -> ShipConfigResult:
    """Validate the ship: value and return the appropriate result type."""
    if ship is None:
        return NotConfigured(resolved_path=resolved_path, location=location)

    if not isinstance(ship, dict):
        return MalformedConfig(
            detail=f"'ship' must be a dict, got {type(ship).__name__}",
            resolved_path=resolved_path,
            location=location,
        )

    # suite is required
    suite = ship.get("suite")
    if suite is None:
        return MalformedConfig(
            detail="'ship.suite' is required",
            resolved_path=resolved_path,
            location=location,
        )
    if not isinstance(suite, dict):
        return MalformedConfig(
            detail=f"'ship.suite' must be a dict, got {type(suite).__name__}",
            resolved_path=resolved_path,
            location=location,
        )
    if "command" not in suite:
        return MalformedConfig(
            detail="'ship.suite.command' is required",
            resolved_path=resolved_path,
            location=location,
        )
    if not isinstance(suite["command"], str) or not suite["command"].strip():
        return MalformedConfig(
            detail="'ship.suite.command' must be a non-empty string",
            resolved_path=resolved_path,
            location=location,
        )

    # Normalize flags to list
    flags = suite.get("flags")
    if flags is None:
        suite["flags"] = []
    elif not isinstance(flags, list):
        return MalformedConfig(
            detail=f"'ship.suite.flags' must be a list, got {type(flags).__name__}",
            resolved_path=resolved_path,
            location=location,
        )

    # Validate baseline_red entries (AC-5, AC-6)
    baseline_red = ship.get("baseline_red", [])
    if not isinstance(baseline_red, list):
        return MalformedConfig(
            detail=f"'ship.baseline_red' must be a list, got {type(baseline_red).__name__}",
            resolved_path=resolved_path,
            location=location,
        )

    stale_exclusions: list[str] = []
    cutoff = datetime.now() - timedelta(days=staleness_days) if staleness_days > 0 else None

    for i, entry in enumerate(baseline_red):
        if not isinstance(entry, dict):
            return MalformedConfig(
                detail=f"'ship.baseline_red[{i}]' must be a dict",
                resolved_path=resolved_path,
                location=location,
            )
        if "node_id" not in entry:
            return MalformedConfig(
                detail=f"'ship.baseline_red[{i}].node_id' is required",
                resolved_path=resolved_path,
                location=location,
            )
        reason = entry.get("reason")
        if not reason or not isinstance(reason, str) or not reason.strip():
            return MalformedConfig(
                detail=f"'ship.baseline_red[{i}].reason' must be a non-empty string",
                resolved_path=resolved_path,
                location=location,
            )
        # AC-6: staleness reporting
        as_of = entry.get("as_of")
        if as_of and cutoff:
            try:
                entry_date = datetime.strptime(as_of, "%Y-%m-%d")
                if entry_date < cutoff:
                    stale_exclusions.append(entry["node_id"])
            except (ValueError, TypeError):
                pass  # invalid date format is not a schema error

    return ShipConfig(
        ship=ship,
        resolved_path=resolved_path,
        location=location,
        stale_exclusions=stale_exclusions,
    )


# ── File resolution ─────────────────────────────────────────────────────────

def _resolve_ext_plans_dir(project_path: Path) -> Optional[Path]:
    """Resolve the external plans dir using ilk_paths if available.

    Falls back to computing the project key inline (same algorithm as
    ilk_paths.project_key) so ship_config has no hard dependency on
    ilk_paths at import time.
    """
    # Try to import ilk_paths for the canonical resolution
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                               / "ilk-loop" / "scripts"))
        from ilk_paths import external_plans_dir, find_project_root, project_key
        root, _kind = find_project_root(project_path)
        if root is not None:
            return external_plans_dir(project_key(root))
    except (ImportError, Exception):
        pass

    # Fallback: derive key inline (same algorithm as ilk_paths.project_key)
    import re
    import hashlib
    abs_str = str(project_path.resolve()).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", abs_str).strip("-")
    if len(slug) <= 80:
        key = slug
    else:
        h = hashlib.sha1(abs_str.encode()).hexdigest()[:7]
        key = slug[:80 - 8].rstrip("-") + "-" + h
    data_home = os.environ.get("ILK_DATA_HOME") or os.environ.get("ILK_DATA_DIR")
    base = Path(data_home).expanduser() if data_home else Path.home() / ".ilk-data"
    return base / "projects" / key / "plans"


def _find_config_file(project_path: Path,
                      ext_plans_dir: Optional[Path] = None,
                      ) -> Tuple[Optional[Path], Optional[Location]]:
    """Find .ilk-launch.json using the 3-location precedence.

    Returns (path, location) or (None, None) if no file found.
    """
    if ext_plans_dir is None:
        ext_plans_dir = _resolve_ext_plans_dir(project_path)

    candidates: list[tuple[Path, Location]] = []
    if ext_plans_dir is not None:
        candidates.append((ext_plans_dir / ".ilk-launch.json", Location.EXTERNAL_PLANS))
    candidates.append((project_path / "docs" / "plans" / ".ilk-launch.json", Location.DOCS_PLANS))
    candidates.append((project_path / ".ilk-launch.json", Location.PROJECT_ROOT))

    for path, loc in candidates:
        if path.is_file():
            return path, loc

    return None, None


def _read_and_parse(path: Path) -> dict | MalformedConfig:
    """Read and JSON-parse a .ilk-launch.json file.

    Returns the parsed dict, or a MalformedConfig if parsing fails.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return MalformedConfig(
            detail=f"cannot read file: {e}",
            resolved_path=path,
            location=Location.PROJECT_ROOT,  # placeholder, caller overrides
        )

    if not text.strip():
        return MalformedConfig(
            detail="file is empty",
            resolved_path=path,
            location=Location.PROJECT_ROOT,
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return MalformedConfig(
            detail=f"invalid JSON: {e}",
            resolved_path=path,
            location=Location.PROJECT_ROOT,
        )

    if not isinstance(data, dict):
        return MalformedConfig(
            detail=f"top-level value must be a dict, got {type(data).__name__}",
            resolved_path=path,
            location=Location.PROJECT_ROOT,
        )

    return data


# ── Public API ──────────────────────────────────────────────────────────────

def load_ship_config(
    project_path: Path,
    *,
    ext_plans_dir: Optional[Path] = None,
    staleness_days: int = 90,
) -> ShipConfigResult:
    """Load the ship: block from .ilk-launch.json.

    Args:
        project_path: The project root directory.
        ext_plans_dir: Override for the external plans directory (for testing).
        staleness_days: Threshold for reporting stale baseline_red entries.

    Returns:
        ShipConfig, NotConfigured, or MalformedConfig.
    """
    resolved_path, location = _find_config_file(project_path, ext_plans_dir)

    if resolved_path is None:
        return NotConfigured()

    # Read and parse
    parsed = _read_and_parse(resolved_path)
    if isinstance(parsed, MalformedConfig):
        return MalformedConfig(
            detail=parsed.detail,
            resolved_path=resolved_path,
            location=location,
        )

    ship = parsed.get("ship")
    if ship is None:
        return NotConfigured(resolved_path=resolved_path, location=location)

    return _validate_ship_block(ship, resolved_path, location, staleness_days)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load and validate the ship: block from .ilk-launch.json",
    )
    parser.add_argument("--validate", action="store_true",
                        help="Validate the ship config and report status")
    parser.add_argument("--project", type=Path, default=Path("."),
                        help="Project root path (default: cwd)")
    args = parser.parse_args()

    if not args.validate:
        parser.print_help()
        sys.exit(0)

    result = load_ship_config(args.project.resolve())

    if isinstance(result, ShipConfig):
        print(f"OK: {result.resolved_path}")
        print(f"  location: {result.location.name}")
        print(f"  suite.command: {result.ship['suite']['command']}")
        flags = result.ship['suite'].get('flags', [])
        if flags:
            print(f"  suite.flags: {flags}")
        br = result.ship.get('baseline_red', [])
        if br:
            print(f"  baseline_red: {len(br)} entries")
        if result.stale_exclusions:
            print(f"  stale exclusions: {result.stale_exclusions}")
    elif isinstance(result, NotConfigured):
        if result.resolved_path:
            print(f"Not configured: file at {result.resolved_path} has no 'ship' key")
        else:
            print("Not configured: no .ilk-launch.json found")
    elif isinstance(result, MalformedConfig):
        print(f"Malformed: {result.detail}", file=sys.stderr)
        print(f"  file: {result.resolved_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
