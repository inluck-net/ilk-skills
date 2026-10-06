#!/usr/bin/env python3
"""PreToolUse guard: a worker or manager session never touches the sealed slice.

RSI design point 2 (Chad, Oct 6): 4-6 held-out mutations live outside the
repo under ``$ILK_DATA_HOME/sealed/`` and only the release train runs them
(``safety_case.py`` component ``sealed``). An RSI build that could read them
could write code that passes them without being correct, so on the worker and
manager homes every tool call whose input names the sealed dir is denied:
Read / Grep / Glob / LS / Edit / Write / MultiEdit / NotebookEdit / Bash.

Matching is substring-based on the whole tool input, not a path resolver:
  - any known sealed dir (each data root + ``/sealed``, raw and realpath), or
  - ``ilk-data`` and ``sealed`` both present anywhere in the input (catches
    ``cd ~/.ilk-data && cat sealed/...`` and a Glob of ``sealed/**`` under
    ``~/.ilk-data``).
It is a speed bump, not a sandbox; the design adds a separate OS user only if
an audit finds sealed-path reads. Authors are the owner session or Chad, whose
interactive home does not install this hook.

Garbage stdin allows (exit 0, no output), like the other ilk hooks.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _sealed_dirs() -> set[str]:
    roots = {os.environ.get("ILK_DATA_HOME"), os.environ.get("ILK_DATA_DIR"),
             str(Path.home() / ".ilk-data")}
    out: set[str] = set()
    for r in roots:
        if not r:
            continue
        p = Path(r).expanduser() / "sealed"
        out.add(str(p))
        try:
            out.add(str(p.resolve()))
        except OSError:
            pass
    return out


def touches_sealed(text: str) -> bool:
    if "ilk-data" in text and "sealed" in text:
        return True
    return any(d in text for d in _sealed_dirs())


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(event, dict):
        return 0
    text = json.dumps(event.get("tool_input", {}))
    if touches_sealed(text):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "the sealed held-out slice is release-train only; "
                    "a worker or manager session may not read or change it."
                ),
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
