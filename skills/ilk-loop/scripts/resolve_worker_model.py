"""Resolve the actual worker model from flag, env, or settings.json.

Pure helper — no side effects, no subprocess calls.  Used by both
run_ilk_loop_claude.ps1 and .sh to populate the display ``Model:`` line
and the JSONL ``model`` field.

Resolution order (first non-empty wins):
  1. explicit ``model_flag``  → source = ``"flag"``
  2. explicit ``env_model``   → source = ``"env"``
  3. ``ANTHROPIC_MODEL`` in ``<config_dir>/settings.json`` env block
                                 → source = ``"settings"``
  4. nothing anywhere           → source = ``"unknown"``
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple


def resolve_model(
    model_flag: str,
    env_model: str,
    config_dir: str | Path,
) -> Tuple[str, str]:
    """Return ``(model, source)`` where source ∈ {flag, env, settings, unknown}.

    Parameters
    ----------
    model_flag:
        Value passed via the runner's ``-Model`` / ``--model`` CLI flag.
    env_model:
        Value of ``ANTHROPIC_MODEL`` already in the shell environment.
    config_dir:
        Path to the Claude config directory (e.g. ``~/.claude-worker``).
        The function reads ``<config_dir>/settings.json``.
    """
    if model_flag:
        return (model_flag, "flag")
    if env_model:
        return (env_model, "env")

    # Try settings.json env block
    try:
        settings_path = Path(config_dir) / "settings.json"
        text = settings_path.read_text(encoding="utf-8-sig")
        data = json.loads(text)
        model = data.get("env", {}).get("ANTHROPIC_MODEL", "")
        if model:
            return (model, "settings")
    except Exception:
        pass

    return ("", "unknown")
