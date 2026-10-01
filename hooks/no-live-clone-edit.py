#!/usr/bin/env python3
"""PreToolUse guard: a loop worker never writes into the LIVE ilk toolkit clone.

Repo-tracked replacement for the stop-gap that was installed 2026-09-28 by
session ilk-skills-54 (and still lives at ~/.claude-worker/hooks/).

Why: the worker home's skills are symlinks into the live clone, so a worker
that Edits ~/.claude/skills/... or ~/.claude-worker/skills/... edits the
code every loop on the host is running.  The runner detects it only after the
iteration ([selfmod] LIVE CLONE TOUCHED), refuses the merge, and leaves the
edit in place.

Scope: Edit / Write / MultiEdit / NotebookEdit whose target resolves (after
symlinks) inside the clone root.  A self-modifying batch edits its selfmod
worktree under ~/.ilk-data, which is not inside the clone, so it is unaffected.
Bash writes that target the clone are also denied (best-effort verb list, not a
full shell parser — the runner's post-iteration LIVE CLONE TOUCHED check stays
the backstop).

The clone root is derived, not hard-coded: the realpath of this home's
skills/ilk-loop, two levels up.  If it cannot be derived, the hook allows
(it must never block a home with no ilk install).
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path


# ── helpers ──────────────────────────────────────────────────────────────────


def clone_root() -> Path | None:
    home = os.environ.get("CLAUDE_CONFIG_DIR") or str(
        Path(__file__).resolve().parent.parent
    )
    link = Path(home) / "skills" / "ilk-loop"
    try:
        real = link.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return real.parent.parent


def target_of(event: dict) -> str | None:
    tool_input = event.get("tool_input") or {}
    return tool_input.get("file_path") or tool_input.get("notebook_path")


def resolve_target(raw: str) -> Path:
    p = Path(os.path.expanduser(raw))
    probe = p
    suffix: list[str] = []
    while not probe.exists() and probe != probe.parent:
        suffix.insert(0, probe.name)
        probe = probe.parent
    return probe.resolve().joinpath(*suffix)


def _is_inside(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


# ── Bash write detection ─────────────────────────────────────────────────────

_WRITE_VERBS = frozenset({
    "commit", "checkout", "switch", "reset", "stash", "apply",
    "am", "merge", "rebase", "cherry-pick", "restore",
})

_FILE_VERBS = frozenset({"cp", "mv", "rm", "touch", "mkdir", "ln"})


def _bash_writes_into_clone(cmd: str, root: Path) -> bool:
    """Best-effort check: does this Bash command write into *root*?

    The rule is a best-effort list of write verbs, not a shell parser.
    The runner's post-iteration LIVE CLONE TOUCHED check stays the backstop.
    If a command cannot be parsed, allow (the hook must never wedge a worker).
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens:
        return False

    # ── redirection targets ──────────────────────────────────────────────
    for i, tok in enumerate(tokens):
        if tok in (">", ">>") and i + 1 < len(tokens):
            if _is_inside(resolve_target(tokens[i + 1]), root):
                return True

    # ── tee ──────────────────────────────────────────────────────────────
    if "tee" in tokens:
        idx = tokens.index("tee")
        for arg in tokens[idx + 1 :]:
            if arg.startswith("-"):
                continue
            if _is_inside(resolve_target(arg), root):
                return True
            break

    # ── sed -i ───────────────────────────────────────────────────────────
    if "sed" in tokens:
        idx = tokens.index("sed")
        args = tokens[idx + 1 :]
        if "-i" in args:
            pos = args.index("-i")
            # -i SUFFIX  (macOS)  or  -i  (GNU)
            rest = args[pos + 1 :]
            # skip optional suffix (empty string or extension) — the suffix
            # is the first arg after -i if it does not look like a flag or
            # a sed expression (starts with s/ / / etc.)
            if rest and not rest[0].startswith("-") and not rest[0].startswith("s/"):
                rest = rest[1:]
            # next non-flag arg is the filename (after the expression)
            for a in rest:
                if not a.startswith("-"):
                    if _is_inside(resolve_target(a), root):
                        return True

    # ── cp / mv / rm / touch / mkdir / ln ────────────────────────────────
    for verb in _FILE_VERBS:
        if verb in tokens:
            idx = tokens.index(verb)
            args = tokens[idx + 1 :]
            non_flags = [a for a in args if not a.startswith("-")]
            if non_flags:
                # cp/mv/ln: target is the last arg (destination).
                # rm/touch/mkdir: target is the first arg.
                if verb in ("cp", "mv", "ln"):
                    target = non_flags[-1]
                else:
                    target = non_flags[0]
                if _is_inside(resolve_target(target), root):
                    return True

    # ── git -C <clone> <verb> ────────────────────────────────────────────
    if "git" in tokens:
        idx = tokens.index("git")
        args = tokens[idx + 1 :]
        clone_path: str | None = None
        rest_args: list[str] = []
        i = 0
        while i < len(args):
            if args[i] == "-C" and i + 1 < len(args):
                clone_path = args[i + 1]
                i += 2
            elif args[i].startswith("-C") and len(args[i]) > 2:
                clone_path = args[i][2:]
                i += 1
            else:
                rest_args.append(args[i])
                i += 1

        if clone_path and _is_inside(resolve_target(clone_path), root):
            if rest_args:
                verb = rest_args[0]
                if verb in _WRITE_VERBS:
                    return True

    return False


def _denies_ship_marker(cmd: str) -> bool:
    """Deny a Bash ``git commit`` whose message contains ``#ship]``.

    Covers ``-m``, ``-am``, ``--message=``, and ``-F <file>`` (reads the
    file's first line).  Independent of the clone root — applies in any repo.
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens or "git" not in tokens:
        return False
    idx = tokens.index("git")
    args = tokens[idx + 1 :]
    if "commit" not in args:
        return False

    for i, tok in enumerate(args):
        # -m "message" or -am "message"
        if tok in ("-m", "-am") and i + 1 < len(args):
            if "#ship]" in args[i + 1]:
                return True
        # --message="message" or --message "message"
        if tok.startswith("--message="):
            if "#ship]" in tok:
                return True
        if tok == "--message" and i + 1 < len(args):
            if "#ship]" in args[i + 1]:
                return True
        # -F <file>
        if tok == "-F" and i + 1 < len(args):
            try:
                first_line = Path(args[i + 1]).read_text(
                    encoding="utf-8", errors="replace"
                ).split("\n", 1)[0]
                if "#ship]" in first_line:
                    return True
            except OSError:
                pass
    return False


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    # garbage stdin ⇒ allow, exit 0, no stdout
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = event.get("tool_name", "")
    root = clone_root()

    # can't derive clone root ⇒ allow (must never block a home with no ilk)
    if root is None:
        return 0

    # ── Edit / Write / MultiEdit / NotebookEdit ──────────────────────────
    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        raw = target_of(event)
        if raw is None:
            return 0
        if _is_inside(resolve_target(raw), root):
            _deny(
                f"Refused: {raw} resolves into the LIVE ilk toolkit clone "
                f"({root}), which every loop on this host is running.  Edit "
                "the copy in your own working tree instead: for a "
                "self-modifying ilk-skills batch that is the selfmod worktree "
                "(`git rev-parse --show-toplevel` must end in "
                "/runtime/launcher/worktrees/selfmod-batch); use a relative "
                "path from there."
            )
            return 0

    # ── Bash ─────────────────────────────────────────────────────────────
    elif tool_name == "Bash":
        tool_input = event.get("tool_input") or {}
        cmd = tool_input.get("command", "")
        if not cmd:
            return 0
        if _denies_ship_marker(cmd):
            _deny(
                "a #ship marker is written only by ship_transition.py "
                "(D-447); run it instead of committing the marker by hand"
            )
            return 0
        if _bash_writes_into_clone(cmd, root):
            _deny(
                f"Refused: Bash command writes into the LIVE ilk toolkit "
                f"clone ({root}).  Use the selfmod worktree instead "
                "(`git rev-parse --show-toplevel` must end in "
                "/runtime/launcher/worktrees/selfmod-batch)."
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