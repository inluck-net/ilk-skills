#!/usr/bin/env python3
"""Render status_all --json into xbar/SwiftBar menu-bar format.

Pure renderer: reads JSON from a file (``--json-from``) or stdin, writes
xbar text to stdout.  No network, no side-effects.

xbar format:
  - First line  → menu-bar title (what you see in the bar)
  - Second line → ``---`` separator
  - Subsequent lines → menu rows; may carry ``| href=`` / ``| bash=`` params
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Default script paths — resolved once relative to this file's location.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_DEFAULT_RUN_SCRIPT = str(_REPO_ROOT / "skills" / "ilk-runner" / "scripts" / "ilk-run.sh")
_DEFAULT_RESUME_SCRIPT = str(_REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "blacklist_status.py")
_DEFAULT_COPY_SCRIPT = str(_REPO_ROOT / "tools" / "xbar" / "copy_ref.sh")

# Interpreter used to launch shell actions.  Absolute so the row does not
# depend on SwiftBar's PATH, which is not a login shell's PATH.
_BASH = "/bin/bash"


# ── heartbeat fragment ───────────────────────────────────────────────
# Every other progress field on the row bottoms out at a git commit, so
# inside a step the panel is blind by construction.  status_all derives
# these four fields from the run dir; the renderer stays pure and merely
# formats them.  A renderer that stats files is a renderer that can fail,
# and it fails inside a 10-second refresh where nobody sees the traceback.

_HEARTBEAT_FIELDS = ("run_id", "iteration", "iteration_elapsed_s", "heartbeat_s")


def _fmt_elapsed(seconds: int) -> str:
    """Format an elapsed duration compactly: ``42s`` / ``12m`` / ``3h25m``."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _heartbeat_fragment(entry: dict) -> str:
    """Return ``  iter N · Xm · ♥Ys``, or ``""`` when unavailable.

    Silent unless **all four** fields arrive as real integers.  Absent is the
    pre-SP4 payload shape, null is a dead run (status_all suppresses a stale
    heartbeat deliberately — a small number there reads as "working right
    now"), and a partial or malformed subset can arrive because the renderer
    and status_all are installed separately, through a symlinked plugin, and
    a stale checkout can pair a new one with an old one.
    """
    vals = {}
    for f in _HEARTBEAT_FIELDS:
        v = entry.get(f)
        if f == "run_id":
            if not isinstance(v, str) or not v:
                return ""
        elif not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return ""
        vals[f] = v
    return (
        f"  iter {vals['iteration']}"
        f" \u00b7 {_fmt_elapsed(vals['iteration_elapsed_s'])}"
        f" \u00b7 \u2665{vals['heartbeat_s']}s"
    )


def render_xbar(
    entries: list[dict],
    *,
    run_script: str = _DEFAULT_RUN_SCRIPT,
    resume_script: str = _DEFAULT_RESUME_SCRIPT,
) -> str:
    """Convert a status_all JSON array into xbar text.

    Parameters
    ----------
    entries:
        Each dict matches the ``resolve_project_status`` schema from
        ``status_all.py``: ``project_key``, ``sentinel.alive``, ``step``, etc.

    Returns
    -------
    str
        Multi-line xbar text ready for stdout.
    """
    if not entries:
        return "ilk !\n---\nNo projects found"

    alive_count = sum(
        1 for e in entries
        if e.get("sentinel", {}).get("alive") and not e.get("orphaned")
    )
    total = len(entries)

    # Title
    if alive_count > 0:
        title = f"ilk {alive_count}*"
    else:
        title = "ilk ok"

    lines = [title, "---"]

    # Global worker-model line (operator, 2026-09-20): one line, not one per
    # row — concurrent loops share a model in practice, and the per-row copy
    # cost width that the menu then truncated.  Derived purely from the
    # payload's per-iteration ``model`` fields (what iterations ACTUALLY ran
    # — when config and reality diverge, reality is the thing worth seeing),
    # never read from disk here: the renderer stays pure by contract.
    live_models = [
        e.get("model")
        for e in entries
        if e.get("sentinel", {}).get("alive")
        and e.get("model")
        and not e.get("orphaned")
    ]
    if live_models:
        uniq = sorted(set(live_models))
        shown = uniq[0] if len(uniq) == 1 else " | ".join(uniq)
        lines.append(f"worker model: {shown}")

    # ── Models section: one row per registry role ─────────────────────
    # Design: provider-switching-and-quota-fallback.md §10, AC2-AC5.
    # The roles block comes from the first non-orphaned entry that has it
    # (the registry is shared across all projects).  The providers block
    # comes from the same entry.
    roles = []
    providers = []
    for e in entries:
        if e.get("orphaned"):
            continue
        if e.get("roles"):
            roles = e["roles"]
            providers = e.get("providers", [])
            break

    if roles:
        lines.append("--Models | size=14")
        for role in roles:
            name = role.get("name", "?")
            configured_model = role.get("model", "")
            provider_host = role.get("provider_host", "")
            auth = role.get("auth", "custom")

            # Build the row: "role: model" or "role: model (live_model)"
            # when configured and live models diverge.
            row = f"--{name}: {configured_model}"
            if provider_host and provider_host != "(unset)":
                row += f"  [{provider_host}]"
            if auth == "official":
                row += "  (official)"

            # Divergence: if any live entry is running this role's home
            # with a different model, show both.
            home = role.get("home", "")
            for e in entries:
                if (e.get("sentinel", {}).get("alive")
                        and e.get("model")
                        and home
                        and home in (e.get("path") or "")):
                    live_model = e.get("model")
                    if live_model and live_model != configured_model:
                        row += f"  (live: {live_model})"
                    break

            # Provider submenu: each provider invokes
            # `ilk-worker-model use <role> <provider>` with no --now.
            # "applies from next iteration" — literally true per §2.2.
            lines.append(f"{row} | size=12")
            for prov in providers:
                prov_id = prov.get("id", "")
                prov_name = prov.get("name", "")
                prov_model = prov.get("model", "")
                if not prov_id:
                    continue
                # Mark the current provider with a checkmark.
                if prov_name == role.get("provider", ""):
                    prefix = "✓"
                else:
                    prefix = " "
                lines.append(
                    f"--  {prefix} {prov_name} ({prov_model})"
                    f" | bash=ilk-worker-model"
                    f" param1=use"
                    f" param2={name!r}"
                    f" param3={prov_id!r}"
                    " terminal=false refresh=true"
                )
            lines.append(
                f"--  applies from next iteration | size=11 color=gray"
            )

        # Last switch result: if any entry has a switch_result field,
        # show it.  The action writes a result line, and the panel
        # renders the last outcome.
        for e in entries:
            if e.get("switch_result"):
                lines.append(f"--Last switch: {e['switch_result']} | size=11")
                break

    for e in entries:
        # ── Orphan filter: the source repo is gone ───────────────────
        # status_all marks a project orphaned when its resolved repo_path no
        # longer exists on disk.  Such an entry can only report history, and
        # a sentinel it left at state=running renders as a permanent "!"
        # that no action can clear.  Hidden before the icon is computed so
        # the blocked/alive branches below never see it.
        if e.get("orphaned"):
            continue

        key = e.get("project_key", "?")
        sent = e.get("sentinel", {})
        is_alive = sent.get("alive", False)
        state = sent.get("state", "none")
        step = e.get("step", "")
        next_sp = e.get("next_subplan", "")
        batch = e.get("batch", "")
        sp_idx = e.get("subplan_index", 0)
        sp_cnt = e.get("subplan_count", 0)
        pending = e.get("pending_batches", 0)

        # Status icon
        if e.get("blocked"):
            icon = "!"
        elif is_alive:
            icon = "*"
        elif state == "running" and not is_alive:
            icon = "!"
        elif state in ("error", "errored"):
            icon = "!"
        else:
            icon = "-"

        # ── Idle filter: skip pure-idle entries ──────────────────────
        # Hide iff idle AND not manually_runnable AND not blocked.
        # Mirrors render_tray.py's idle filter.
        if icon == "-" and not e.get("manually_runnable") and not e.get("blocked"):
            continue

        # ── Residue filter: blocked but owing nothing ─────────────────
        # A finished project (every master shipped, pending == 0) shows a
        # blocked row only as residue — typically a verification run that
        # died without writing terminal state, re-flagging it on every
        # retry. Hidden by operator request 2026-09-20. Two classes stay
        # visible on purpose: a blocked project that still owes a batch
        # (real signal), and a parked one (human-held work whose Resume
        # action lives in this very row).
        if icon == "!" and pending == 0 and not e.get("parked"):
            continue

        # Row text: icon + queue badge + SHORT key + batch context.
        model = e.get("model") or ""
        # Short display key: the source repo's directory name
        # ("users-chad-projects-keyreply-kira-cloudflare" → "kira-cloudflare").
        # Derived from repo_path, not parsed out of the flattened key — the
        # key's '/'→'-' flattening loses dir boundaries ("inluck-net" is one
        # dir, "gh-resolve" is two dashes).  The full key moves to the row's
        # submenu info block.  macOS menus truncate without wrapping, and the
        # full keys alone ran ~40 chars per row (operator, 2026-09-20).
        short_key = key
        rp = (e.get("repo_path") or "").replace("\\", "/").rstrip("/")
        if rp:
            # Worktree-keyed project: the parent dir's basename contains
            # "worktrees" or "scratch-worktrees" — either as a nested
            # directory (…/kira-cloudflare/worktrees/pv-5611) or as a
            # hyphenated composite (…/kira-cloudflare-worktrees/pv-5611,
            # the form the resolver actually creates on disk).  Display
            # as "repo-name [worktree-name]" — the bare worktree dir
            # (e.g. "pv-5611") is uninformative on its own.
            rp_path = Path(rp)
            parent = rp_path.parent.name
            wt = None
            # Case 1: nested dir (…/kira-cloudflare/worktrees/pv-5611 or
            # …/kira-cloudflare/scratch-worktrees/resolver).  Checked FIRST
            # because "scratch-worktrees" ends with "-worktrees" — the suffix
            # case would strip to "scratch" instead of using the grandparent.
            if parent in ("worktrees", "scratch-worktrees"):
                wt = rp_path.parent.parent.name
            # Case 2: hyphenated composite (…/kira-cloudflare-worktrees/pv-5611)
            if wt is None:
                for suffix in ("-scratch-worktrees", "-worktrees"):
                    if parent.endswith(suffix):
                        wt = parent[: -len(suffix)].rstrip("-") or parent
                        break
            if wt:
                short_key = f"{wt} [{rp_path.name}]"
            else:
                short_key = rp.rsplit("/", 1)[-1]
        # Queue badge, AHEAD of the project name (operator spec 2026-09-20):
        # "+N" = total batches the project still owes (active or queued,
        # current included), rendered only when N > 1 — at N=1 the row's own
        # batch name already says everything the badge would.
        badge = f"+{pending} " if pending > 1 else ""
        row = f"{icon} {badge}{short_key}"
        # Batch M/N then sub-plan then step.  The batch fragment ("pv5 3/7")
        # reads as "sub-plan 3 of 7 of batch pv5" and sits AHEAD of the
        # sub-plan name by operator request (2026-09-20) — the sub-plan's
        # own step count ("0/4") would otherwise be mistaken for the batch
        # position.  Sub-plan then step matches /ilk-status ("<slug> 3/5");
        # the reverse order read as "3/5 a-draft-is-checked-…", which parses
        # as a step count applied to nothing.
        if batch and sp_cnt:
            row += f"  {batch} {sp_idx}/{sp_cnt}"
        if next_sp:
            row += f"  {next_sp}"
        if step:
            row += f"  {step}"

        # State suffix for non-obvious states.  The model no longer prefixes
        # the running state: concurrent loops share one model in practice, so
        # it repeated on every row (width, 2026-09-20) — it lives in the
        # submenu info block; the `*` icon plus the heartbeat fragment carry
        # "running".
        if state not in ("running", "none"):
            row += f"  ({state})"

        # Sub-step liveness, last: it is the fastest-changing part of the row
        # and the eye tracks a trailing field better than an interior one.
        row += _heartbeat_fragment(e)

        # Every row carries a trivial action so SwiftBar/AppKit keeps it
        # ENABLED. Actionless items with no attached submenu are disabled by
        # AppKit, and a disabled item does not track its submenu — which is
        # why running rows' sub-panels would not open (2026-09-20): their
        # submenu attach raced the enable decision. An item with BOTH an
        # action and a submenu opens the submenu on click; the refresh
        # action itself never fires for such items, and would be harmless
        # (a panel refresh) if it did.
        row += " | refresh=true"

        lines.append(row)

        # ── Info sub-items: everything the compact top line gave up ──────
        if short_key != key:
            lines.append(f"--key: {key}")
        if model:
            lines.append(f"--model: {model}")
        if pending:
            lines.append(f"--batches owed: {pending}")
        # Why a parked row is parked (violation-park or operator park): the
        # row line carries position, the submenu carries the sentence.  Read
        # via .get — payloads from an older status_all predate the field.
        if e.get("parked_reason"):
            lines.append(f"--parked: {e['parked_reason']}")

        # ── Copy reference: a pastable ilk-ref for this row ─────────────
        # Grammar: ilk-ref:<project-key>/<master-file>/<subplan-file> —
        # space-free by necessity (bare or quoted params alike end at
        # spaces, and pipes would split the params blob). Mirrors the
        # Start-now action's quoting, the one invocation shape proven to
        # fire in this panel.
        master_file = e.get("active_master") or ""
        sub_file = e.get("next_subplan_file") or ""
        if (
            master_file
            and sub_file
            and all(" " not in p and "|" not in p for p in (key, master_file, sub_file))
            and os.path.isfile(_DEFAULT_COPY_SCRIPT)
        ):
            ref = f"ilk-ref:{key}/{master_file}/{sub_file}"
            lines.append(
                f"--Copy reference | bash={_BASH!r}"
                f" param1={_DEFAULT_COPY_SCRIPT!r} param2={ref!r}"
                " terminal=false refresh=false"
            )

        # ── Action sub-items: Start now / Resume ─────────────────────
        # Start now: manually_runnable & not running — dispatchable work exists.
        # Resume: parked/blacklisted — needs resolve-ack.
        # NOTE: the two actions take DIFFERENT paths.
        #  - Start now → ilk-run.sh, which resolves a project root from the
        #    SOURCE repo path (repo_path). The `path` field is the ~/.ilk-data
        #    data dir, which ilk-run.sh cannot resolve — used only as fallback.
        #  - Resume → blacklist_status.py --project, which expects the data dir.
        data_path = e.get("path", "")
        run_path = e.get("repo_path") or data_path
        if e.get("manually_runnable"):
            # Invoke through an interpreter rather than exec'ing the script.
            #
            # SwiftBar's `bash=<path>` execs the target directly, so a script
            # committed 0644 fails with 126 — and because the row carries
            # `terminal=false`, that failure is invisible: no window, no error,
            # the menu just refreshes.  Observed 2026-08-26: "Start now" did
            # nothing at all, while `bash ilk-run.sh` from a shell worked,
            # because reading a file as bash input needs no execute bit.
            #
            # This matches how the launchd plist already invokes scheduler.sh
            # (`/bin/bash <script> --poll-min ...`), and how the Resume row
            # below already invokes python3 — Resume was never affected.
            if not os.path.isfile(run_script):
                lines.append(
                    f"--Start now unavailable (missing {os.path.basename(run_script)})"
                    " | color=red"
                )
            else:
                lines.append(
                    f"--Start now | bash={_BASH!r}"
                    f" param1={run_script!r} param2={run_path!r}"
                    " terminal=false refresh=true"
                )
        if e.get("parked"):
            lines.append(
                f"--Resume | bash=python3"
                f" param1={resume_script!r}"
                " param2=ack"
                " param3=--project"
                f" param4={data_path!r} terminal=false refresh=true"
            )

    # Separator + actions
    lines.append("---")
    lines.append("Open log | bash=ilk-status")
    lines.append("Refresh | refresh=true")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Render status_all JSON as xbar text")
    ap.add_argument("--json-from", type=str, default=None,
                    help="Path to JSON file (default: read stdin)")
    args = ap.parse_args()

    if args.json_from:
        with open(args.json_from, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    sys.stdout.write(render_xbar(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
