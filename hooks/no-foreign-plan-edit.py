#!/usr/bin/env python3
"""PreToolUse guard: a loop worker edits and commits only its iteration's sub-plan.

Active only when ``ILK_ITERATION_SUBPLAN`` is set (exported by the runner).
With it unset, allows everything — an interactive session is never blocked.

Scope:
  * Edit / Write / MultiEdit / NotebookEdit whose target is a ``*.md`` inside
    the project's plans directory (resolved via ``ilk_paths.py``) whose
    filename (stem) does not match the iteration's sub-plan slug ⇒ deny.
  * Bash ``git commit`` whose message contains ``[plan:<other>#…]`` ⇒ deny.
  * Bash ``git reset`` / ``git rebase`` ⇒ deny (a worker never rewrites
    history).

The deny message names the target: "this iteration's sub-plan is <slug>;
<path> belongs to another sub-plan."

Env override: ``ILK_PLANS_DIR`` — if set, used as the plans directory instead
of resolving via ``ilk_paths.find_plans_dir``.  Exists for test isolation.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path


# ── helpers ──────────────────────────────────────────────────────────────────


def _plans_dir() -> Path | None:
    """Resolve the plans directory.

    Order: ``ILK_PLANS_DIR`` env var → ``ilk_paths.find_plans_dir`` from the
    hook's own location.  Returns ``None`` when the directory cannot be
    determined (the hook allows — it must never wedge a worker).
    """
    override = os.environ.get("ILK_PLANS_DIR")
    if override:
        p = Path(override)
        if p.is_dir():
            return p

    # Derive from this file's location: hooks/no-foreign-plan-edit.py → repo
    # root → ilk_paths.find_plans_dir
    repo_root = Path(__file__).resolve().parent.parent
    scripts = repo_root / "skills" / "ilk-loop" / "scripts"
    if scripts.is_dir():
        sys.path.insert(0, str(scripts))
        try:
            from ilk_paths import find_plans_dir  # type: ignore[import-untyped]
            result, _source = find_plans_dir(repo_root)
            if result is not None:
                return result
        except Exception:
            pass
    return None


def _target_path(event: dict) -> Path | None:
    """Return the resolved target path for Edit/Write/MultiEdit/NotebookEdit."""
    tool_input = event.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return None
    p = Path(os.path.expanduser(raw))
    # Resolve through symlinks, handling not-yet-existing intermediate dirs
    probe = p
    suffix: list[str] = []
    while not probe.exists() and probe != probe.parent:
        suffix.insert(0, probe.name)
        probe = probe.parent
    return probe.resolve().joinpath(*suffix)


def _slug_of(path: Path) -> str | None:
    """Extract the plan slug from a plans-dir filename.

    ``2026-10-02d-my-plan.md`` → ``my-plan``.
    ``MASTER-2026-10-02d.md`` → ``None`` (master files are not a sub-plan).
    """
    name = path.stem  # e.g. "2026-10-02d-my-plan"
    # Skip MASTER files
    if name.startswith("MASTER"):
        return None
    # Date prefix: YYYY-MM-DD-<slug>  (the date part is 10 chars + hyphen)
    parts = name.split("-", 3)
    if len(parts) >= 4:
        return parts[3]  # everything after the date
    return name


def _is_foreign_plan(target: Path, plans: Path, own_slug: str) -> bool:
    """True if *target* is a ``*.md`` in *plans* that belongs to another slug."""
    try:
        target.relative_to(plans)
    except ValueError:
        return False
    if target.suffix != ".md":
        return False
    slug = _slug_of(target)
    if slug is None:
        return False  # MASTER files are not owned by any sub-plan
    return slug != own_slug


# ── Bash checks ──────────────────────────────────────────────────────────────


def _commit_has_foreign_slug(cmd: str, own_slug: str) -> bool:
    """True if a ``git commit`` message contains ``[plan:<other>#…]``."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens or "git" not in tokens:
        return False
    idx = tokens.index("git")
    args = tokens[idx + 1:]
    if "commit" not in args:
        return False

    for i, tok in enumerate(args):
        # -m "message" or -am "message"
        if tok in ("-m", "-am") and i + 1 < len(args):
            msg = args[i + 1]
            if _foreign_plan_tag(msg, own_slug):
                return True
        # --message="message" or --message "message"
        if tok.startswith("--message="):
            msg = tok.split("=", 1)[1]
            if _foreign_plan_tag(msg, own_slug):
                return True
        if tok == "--message" and i + 1 < len(args):
            if _foreign_plan_tag(args[i + 1], own_slug):
                return True
        # -F <file>
        if tok == "-F" and i + 1 < len(args):
            try:
                first_line = Path(args[i + 1]).read_text(
                    encoding="utf-8", errors="replace"
                ).split("\n", 1)[0]
                if _foreign_plan_tag(first_line, own_slug):
                    return True
            except OSError:
                pass
    return False


def _foreign_plan_tag(msg: str, own_slug: str) -> bool:
    """True if *msg* contains ``[plan:<other>#…]`` where <other> ≠ own_slug."""
    import re
    for m in re.finditer(r"\[plan:([^#\]]+)#[^\]]+\]", msg):
        if m.group(1) != own_slug:
            return True
    return False


_HISTORY_REWRITE = frozenset({"reset", "rebase"})


def _rewrites_history(cmd: str) -> bool:
    """True if the Bash command contains ``git reset`` or ``git rebase``."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens or "git" not in tokens:
        return False
    idx = tokens.index("git")
    args = tokens[idx + 1:]
    return any(a in _HISTORY_REWRITE for a in args)


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    # garbage stdin ⇒ allow, exit 0, no stdout
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    # Not in an iteration ⇒ allow everything (interactive session)
    own_slug = os.environ.get("ILK_ITERATION_SUBPLAN")
    if not own_slug:
        return 0

    plans = _plans_dir()
    if plans is None:
        return 0  # can't resolve ⇒ allow (must never wedge a worker)

    tool_name = event.get("tool_name", "")

    # ── Edit / Write / MultiEdit / NotebookEdit ──────────────────────────
    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        target = _target_path(event)
        if target is not None and _is_foreign_plan(target, plans, own_slug):
            _deny(
                f"this iteration's sub-plan is {own_slug}; "
                f"{target} belongs to another sub-plan"
            )
            return 0

    # ── Bash ─────────────────────────────────────────────────────────────
    elif tool_name == "Bash":
        tool_input = event.get("tool_input") or {}
        cmd = tool_input.get("command", "")
        if cmd:
            if _commit_has_foreign_slug(cmd, own_slug):
                _deny(
                    f"this iteration's sub-plan is {own_slug}; "
                    "a commit carrying another plan's tag is not allowed"
                )
                return 0
            if _rewrites_history(cmd):
                _deny(
                    "a worker never rewrites history; "
                    "git reset and git rebase are forbidden"
                )
                return 0

    return 0


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


if __name__ == "__main__":
    sys.exit(main())