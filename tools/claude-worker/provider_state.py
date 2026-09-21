"""Provider-state published contract — one file other tools may trust.

A secret-free JSON file keyed by resolved home path that records both
probe results per home so consumers (gh-resolve, the tray) can tell
whether a home is alive before spending work on it.

Design: docs/architecture/provider-switching-and-quota-fallback.md §6.

Schema (version 1)::

    {
      "version": 1,
      "homes": {
        "<resolved-home-path>": {
          "provider": "<provider-id>",
          "model": "<model-id>",
          "state": "ok" | "exhausted" | "unknown",
          "config_probe": {"result": "<model-id>", "at": "<iso8601+offset>"},
          "live_probe":   {"ok": bool, "detail": str, "at": "<iso8601+offset>"},
          "reset_at": "<iso8601+offset>" | null,
          "evidence": {"log": "<path>", "run_id": "<id>"}
        }
      }
    }

Fail closed, everywhere: a missing, unparseable, or wrong-version file
is an error, never a default.  A record whose config_probe passed and
whose live_probe failed reads as ``"exhausted"``, never ``"ok"``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

CURRENT_VERSION = 1

# Type alias for the validated state dict shape.
ProviderState = Dict[str, Any]

# Minimum set of keys every home record must carry.
_HOME_KEYS = frozenset({
    "provider", "model", "state",
    "config_probe", "live_probe",
    "reset_at", "evidence",
})

# Minimum set of keys inside a probe object.
_PROBE_CONFIG_KEYS = frozenset({"result", "at"})
_PROBE_LIVE_KEYS = frozenset({"ok", "detail", "at"})

# Minimum set of keys inside the evidence object.
_EVIDENCE_KEYS = frozenset({"log", "run_id"})


class ProviderStateError(ValueError):
    """The provider-state file is missing, malformed, or wrong version."""


def _validate_state(data: Any) -> Dict[str, Any]:
    """Validate a parsed JSON blob as a version-1 provider-state dict.

    Returns the validated dict (unchanged).  Raises :class:`ProviderStateError`
    on any defect — fail closed.
    """
    if not isinstance(data, dict):
        raise ProviderStateError("provider-state root must be a JSON object")

    version = data.get("version")
    if version != CURRENT_VERSION:
        raise ProviderStateError(
            f"unsupported provider-state version {version!r} "
            f"(expected {CURRENT_VERSION})"
        )

    homes = data.get("homes")
    if not isinstance(homes, dict):
        raise ProviderStateError("provider-state must contain a 'homes' object")

    for path, record in homes.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise ProviderStateError(
                f"home key {path!r} is not an absolute path"
            )
        if not isinstance(record, dict):
            raise ProviderStateError(f"home {path!r} must be an object")

        missing = _HOME_KEYS - record.keys()
        if missing:
            raise ProviderStateError(
                f"home {path!r} missing keys: {sorted(missing)}"
            )

        # Validate probe shapes.
        cp = record["config_probe"]
        if not isinstance(cp, dict) or not _PROBE_CONFIG_KEYS.issubset(cp.keys()):
            raise ProviderStateError(
                f"home {path!r}: config_probe must have 'result' and 'at'"
            )

        lp = record["live_probe"]
        if not isinstance(lp, dict) or not _PROBE_LIVE_KEYS.issubset(lp.keys()):
            raise ProviderStateError(
                f"home {path!r}: live_probe must have 'ok', 'detail', and 'at'"
            )

        ev = record["evidence"]
        if not isinstance(ev, dict) or not _EVIDENCE_KEYS.issubset(ev.keys()):
            raise ProviderStateError(
                f"home {path!r}: evidence must have 'log' and 'run_id'"
            )

    return data


def read_state(path: Path) -> Dict[str, Any]:
    """Read and validate a provider-state file.  Raises on any defect.

    Returns the validated dict with ``version`` and ``homes`` keys.
    """
    path = Path(path)
    if not path.is_file():
        raise ProviderStateError(f"provider-state file not found: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProviderStateError(f"cannot read {path}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderStateError(f"malformed JSON in {path}: {exc}") from exc

    return _validate_state(data)


def write_state(path: Path, state: Dict[str, Any]) -> None:
    """Write a provider-state file.  Validates before writing.

    The caller's dict is not mutated.  Any defect in the input raises
    :class:`ProviderStateError` before the file is touched.
    """
    path = Path(path)
    validated = _validate_state(state)
    path.write_text(
        json.dumps(validated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )