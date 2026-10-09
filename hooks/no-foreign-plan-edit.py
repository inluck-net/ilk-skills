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
  * Skill ``ilk-*`` other than ilk, ilk-loop and ilk-status ⇒ deny (a worker
    does one step through the loop skill, never the planner).

The deny message names the target: "this iteration's sub-plan is <slug>;
<path> belongs to another sub-plan."

The plans directory is the worker's project's (``find_plans_dir`` from the
event's ``cwd``).  With ``ILK_MASTER`` set, a MASTER file other than that one
is foreign too.

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


def _plans_dir(event: dict | None = None) -> Path | None:
    """Resolve the plans directory of the project the worker is working in.

    Order: ``ILK_PLANS_DIR`` env var -> ``ilk_paths.find_plans_dir`` from the
    event's ``cwd`` -> from this process's cwd -> from the hook's own location.
    Returns ``None`` when the directory cannot be determined (the hook allows
    -- it must never wedge a worker).

    The project comes first.  Installed, this file lives under
    ``~/.ilk/releases/<tag>/``, which is not a git repo, so resolving from the
    hook's location alone returned None and allowed every foreign edit
    (gh-resolve run 20261009-212756, 21:41).
    """
    override = os.environ.get("ILK_PLANS_DIR")
    if override:
        p = Path(override)
        if p.is_dir():
            return p

    repo_root = Path(__file__).resolve().parent.parent
    scripts = repo_root / "skills" / "ilk-loop" / "scripts"
    if not scripts.is_dir():
        return None
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        from ilk_paths import find_plans_dir  # type: ignore[import-untyped]
    except Exception:
        return None

    starts: list[Path] = []
    cwd = (event or {}).get("cwd")
    if cwd:
        starts.append(Path(cwd))
    try:
        starts.append(Path.cwd())
    except OSError:
        pass
    starts.append(repo_root)
    for start in starts:
        try:
            result, _source = find_plans_dir(start)
        except Exception:
            continue
        if result is not None:
            return result
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
        # A MASTER file.  With the run pinned to a master, every other
        # master is foreign (run 20261009-212756 edited MASTER-...-09d while
        # pinned to 09b).  Unpinned, masters are not owned by any sub-plan.
        own_master = os.environ.get("ILK_MASTER", "")
        return bool(own_master) and target.name != own_master
    return slug != own_slug


# ── safety-kernel helpers ────────────────────────────────────────────────────


def _find_repo(target: Path) -> Path | None:
    """Walk up from *target* to the nearest ancestor containing ``.git``."""
    probe = target
    if probe.is_file():
        probe = probe.parent
    while probe != probe.parent:
        if (probe / ".git").exists():
            return probe
        probe = probe.parent
    return None


def _resolve_master(plans: Path, own_slug: str) -> dict | None:
    """Resolve the iteration's master frontmatter.

    Order: ``ILK_MASTER`` env var → scan masters for registry listing
    *own_slug*.  Returns the frontmatter dict, or ``None`` if unresolvable.
    """
    scripts = (
        Path(__file__).resolve().parent.parent / "skills" / "ilk-loop" / "scripts"
    )
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

    master_name = os.environ.get("ILK_MASTER")
    if master_name:
        master_path = plans / master_name
        if master_path.exists():
            try:
                from plan_status import parse_frontmatter  # type: ignore[import-untyped]
                return parse_frontmatter(
                    master_path.read_text(encoding="utf-8-sig")
                )
            except Exception:
                pass

    # Fallback: scan masters for a registry row naming this slug.
    try:
        from safety_kernel import _find_master_for_slug  # type: ignore[import-untyped]
        return _find_master_for_slug(own_slug, plans)
    except Exception:
        pass
    return None


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

#: The ilk-* skills a worker may open mid-iteration: the loop skill and the
#: read-only status view.  Measured on ilk-skills logs *202610*: ilk 57,
#: ilk-status 14, ilk-plan 5 (each a misroute).
_WORKER_SKILLS = frozenset({"ilk", "ilk-loop", "ilk-status"})


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

    tool_name = event.get("tool_name", "")

    # ── Skill ────────────────────────────────────────────────────────────
    # A worker runs one step through the loop skill.  The planner and the
    # other ilk-* skills act on whatever master is newest, with no one-step
    # contract (backlog a134fac5: run 20261009-212756 opened ilk-plan and
    # shipped another master's sub-plan).
    if tool_name == "Skill":
        name = str((event.get("tool_input") or {}).get("skill", ""))
        base = name.rsplit(":", 1)[-1]
        if base.startswith("ilk") and base not in _WORKER_SKILLS:
            _deny(
                f"this iteration's sub-plan is {own_slug}; a loop worker "
                f"does not open {name}. Use Skill(ilk) to do the one step "
                f"at current_step, then end your turn."
            )
        return 0

    plans = _plans_dir(event)
    if plans is None:
        return 0  # can't resolve ⇒ allow (must never wedge a worker)

    # ── Edit / Write / MultiEdit / NotebookEdit ──────────────────────────
    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        target = _target_path(event)
        if target is not None and _is_foreign_plan(target, plans, own_slug):
            _deny(
                f"this iteration's sub-plan is {own_slug}; "
                f"{target} belongs to another sub-plan"
            )
            return 0

        # ── safety-kernel tier check ───────────────────────────────────
        if target is not None:
            repo = _find_repo(target)
            if repo is not None:
                kernel_file = (
                    repo / "skills" / "ilk-loop" / "safety-kernel.json"
                )
                if kernel_file.exists():
                    try:
                        scripts = (
                            Path(__file__).resolve().parent.parent
                            / "skills" / "ilk-loop" / "scripts"
                        )
                        if str(scripts) not in sys.path:
                            sys.path.insert(0, str(scripts))
                        from safety_kernel import (  # type: ignore[import-untyped]
                            KernelListError, load, tier_of,
                        )
                        kernel = load(repo)
                        try:
                            rel = str(target.relative_to(repo))
                        except ValueError:
                            rel = str(target)
                        result = tier_of(rel, kernel=kernel)
                        if result is not None:
                            tier, _entry = result
                            if tier == "rules":
                                _deny(
                                    f"{target} is in the safety "
                                    f"kernel's rules tier; no loop "
                                    f"build changes the rules "
                                    f"(design guard 1). Write what "
                                    f"you needed in Findings and "
                                    f"end your turn."
                                )
                                return 0
                            if tier == "kernel":
                                master_fm = _resolve_master(
                                    plans, own_slug,
                                )
                                if (
                                    master_fm is not None
                                    and master_fm.get("auto_planned")
                                    == "true"
                                ):
                                    _deny(
                                        f"{target} is tier-0 and "
                                        f"this is an unattended "
                                        f"build; it cannot touch "
                                        f"the kernel. Write it in "
                                        f"Findings and end your "
                                        f"turn."
                                    )
                                    return 0
                    except KernelListError:
                        print(
                            "safety-kernel.json failed to load, "
                            "allowing edit",
                            file=sys.stderr,
                        )
                    except Exception:
                        pass  # allow (must never wedge a worker)

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