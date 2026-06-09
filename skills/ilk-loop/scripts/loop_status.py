"""Universal ilk-loop status checker.

Walks up from cwd to find a `docs/plans/` directory containing a
`MASTER-*.md` file, parses every sub-plan's YAML front-matter, and reports:

  - per-sub-plan status (pending / in-progress / shipped / blocked)
  - the next pending sub-plan to work on
  - exit code 0 iff every sub-plan is `shipped`

Project-agnostic — same script works in any repo that uses the ilk-loop
convention.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Local import of ilk_paths (sibling module) — used to discover the
# active plans directory under the new ~/.ilk-data convention while
# still supporting legacy in-tree projects.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ilk_paths import (  # noqa: E402
    find_plans_dir as _resolve_plans_dir,
    find_project_root,
    read_meta_manifest,
    MetaManifestError,
)
from plan_status import master_has_nonshipped, reconcile_master_status  # noqa: E402

STATUS_ICONS = {
    "shipped": "[OK]",
    "in-progress": "[..]",
    "ready": "[>>]",
    "pending": "[  ]",
    "blocked": "[XX]",
}


def find_plans_dir(start: Path) -> Path | None:
    """Resolve the active plans dir, preferring ~/.ilk-data over in-tree.

    Thin wrapper around `ilk_paths.find_plans_dir` that returns just the
    path (legacy callers used to get a Path|None and we keep that shape
    for them). The richer (path, source) tuple is available via
    `_resolve_plans_dir`.
    """
    path, _ = _resolve_plans_dir(start)
    return path


def parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML front-matter parser (flat key: value only)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for raw in text[3:end].splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("- "):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def extract_master_order(master_text: str) -> list[str]:
    """Return ordered, deduped list of sub-plan filenames as they appear in
    the master plan body (registry table). Excludes the master itself.

    A sub-plan reference must be a bare filename or sit at the top of
    `docs/plans/`. References under sub-directories (e.g. `findings/...`,
    `legacy/...`, `archive/...`) are intentionally excluded so the master
    can freely cite supporting documents inside Notes columns without
    polluting the sub-plan registry.
    """
    # Capture group is the filename. The lookbehind requires the preceding
    # character to be a non-path character (start-of-line, whitespace,
    # bracket, paren, or `./`) — but specifically NOT `/`, which would
    # mean the filename lives in a subdirectory.
    pattern = re.compile(
        r"(?:^|(?<=[\s(\[|]))(?:\./)?(\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*\.md)",
        re.MULTILINE,
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for f in pattern.findall(master_text):
        if f.startswith("MASTER"):
            continue
        if f in seen:
            continue
        seen.add(f)
        ordered.append(f)
    return ordered


def pick_active_master(masters: list[Path]) -> tuple[Path, dict]:
    """
    Choose the master plan to drive this run, plus a summary of the
    queue for display purposes.

    Selection rules (queue model):
      1. Exactly one master with `status: active` -> that one
      2. Multiple `active` -> data integrity issue: prefer the one
         with the lowest priority key (priority desc, created asc),
         and emit a stderr warning so the operator notices
      3. Zero `active`, at least one `queued` -> peek (do NOT promote)
         the highest-priority queued master so loop_status reflects
         what would run next; the watchdog (or an operator) is the
         one allowed to mutate `status: queued` -> `status: active`
      4. Zero `active` and zero `queued`, fall through to legacy
         behaviour (newest by mtime among everything else, including
         `paused` and `shipped`)
      5. Zero masters with any frontmatter `status` field -> legacy
         layout: pick newest by mtime exactly as before

    Returns (chosen_path, queue_view) where queue_view has counts +
    a list of upcoming queued titles for display.
    """
    parsed: list[tuple[Path, dict]] = []
    plans_dir = masters[0].parent if masters else None
    for p in masters:
        try:
            text = p.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        parsed.append((p, parse_frontmatter(text)))

    # Filter out masters whose registered sub-plans are all shipped.
    # These are terminal and should not be selected for execution.
    if plans_dir is not None:
        parsed = [
            (p, fm) for p, fm in parsed
            if master_has_nonshipped(p, plans_dir)
        ]

    def _prio(fm: dict) -> int:
        try:
            return int(fm.get("priority", 0))
        except (TypeError, ValueError):
            return 0

    def _created(fm: dict, fallback: Path) -> str:
        v = fm.get("created", "")
        if v:
            return str(v)
        # Fallback to mtime ISO so legacy masters still sort sensibly.
        return ""

    def _sortkey(item: tuple[Path, dict]) -> tuple[int, str, float]:
        p, fm = item
        # Higher priority first => negate. Empty/missing created sorts
        # last among same-priority by using mtime as fallback.
        return (-_prio(fm), _created(fm, p) or "~", -p.stat().st_mtime)

    by_status: dict[str, list[tuple[Path, dict]]] = {}
    for item in parsed:
        st = (item[1].get("status") or "").strip().lower() or "(none)"
        by_status.setdefault(st, []).append(item)

    actives = sorted(by_status.get("active", []), key=_sortkey)
    queued = sorted(by_status.get("queued", []), key=_sortkey)
    paused = by_status.get("paused", [])
    shipped = by_status.get("shipped", [])
    legacy = by_status.get("(none)", [])
    draft = by_status.get("draft", [])

    if len(actives) > 1:
        print(
            f"[ilk] WARNING: {len(actives)} masters have status: active. "
            "Only one should be active at a time. Choosing highest priority.",
            file=sys.stderr,
        )

    chosen: Path
    if actives:
        chosen = actives[0][0]
    elif queued:
        chosen = queued[0][0]
        print(
            f"[ilk] no master is `active`; previewing the next queued "
            f"master: {chosen.name}. Mark its status: active to run it, "
            "or let the watchdog promote it on the next clean ship.",
            file=sys.stderr,
        )
    elif legacy:
        # Pure legacy: newest by mtime
        chosen = max(legacy, key=lambda it: it[0].stat().st_mtime)[0]
    elif paused or shipped or draft:
        # All masters are terminal or not-yet-ready (draft) — nothing to do,
        # but pick the most recent so the table renders.
        chosen = max(parsed, key=lambda it: it[0].stat().st_mtime)[0]
    else:
        # Defensive: no parseable masters but glob saw files
        chosen = masters[-1]

    queue_view = {
        "active_count": len(actives),
        "queued_count": len(queued),
        "paused_count": len(paused),
        "shipped_count": len(shipped),
        "legacy_count": len(legacy),
        "draft_count": len(draft),
        "queued_titles": [it[0].name for it in queued],
    }
    return chosen, queue_view


def resolve_status(cwd: Path) -> dict:
    """Resolve all status data and return a structured dict.

    Returns a dict with keys:
      - master: str (filename)
      - subplans: list[dict] with slug, status, current_step, estimated_steps
      - active, queued, shipped: int counts
      - queue_exit: 0 (all shipped), 1 (pending), 2 (error)
      - plans_dir: str
      - next: dict|None with fname, status, cur, est, repo (if any)
    """
    plans_dir, plans_source = _resolve_plans_dir(cwd)
    if not plans_dir:
        return {"error": "no plans dir found", "queue_exit": 2}

    masters = sorted(plans_dir.glob("MASTER-*.md"))
    if not masters:
        return {"error": f"No MASTER-*.md in {plans_dir}", "queue_exit": 2}

    # Reconcile pass: auto-flip any all-shipped master to status: shipped.
    # Idempotent — safe to run every invocation.
    for m in masters:
        reconcile_master_status(m, plans_dir)

    master, queue_view = pick_active_master(masters)
    master_text = master.read_text(encoding="utf-8")
    ordered_files = extract_master_order(master_text)

    rows: list[tuple[str, str, str, str, str]] = []
    tiers: dict[str, str] = {}  # fname -> verification_tier
    next_actionable = None
    next_blocked = None
    next_other = None

    for fname in ordered_files:
        path = plans_dir / fname
        if not path.exists():
            rows.append((fname, "MISSING", "-", "-", ""))
            continue
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        status = fm.get("status", "?")
        cur_step = fm.get("current_step", "?")
        est = fm.get("estimated_steps", "?")
        repo = fm.get("repo", "")
        tiers[fname] = fm.get("verification_tier", "").strip() or "loop-verified"
        rows.append((fname, status, cur_step, est, repo))
        if status == "shipped":
            continue
        row = (fname, status, cur_step, est, repo)
        if status in ("pending", "ready", "in-progress"):
            if next_actionable is None:
                next_actionable = row
        elif status == "blocked":
            if next_blocked is None:
                next_blocked = row
        else:
            if next_other is None:
                next_other = row

    next_pending = next_actionable or next_other or next_blocked

    # Build subplans list
    subplans = []
    for fname, status, cur, est, _repo in rows:
        # Derive slug: strip YYYY-MM-DD- prefix and .md suffix
        slug = fname
        if fname.endswith(".md"):
            slug = fname[:-3]
        m = re.match(r"^\d{4}-\d{2}-\d{2}-(.*)$", slug)
        if m:
            slug = m.group(1)
        subplans.append({
            "fname": fname,
            "slug": slug,
            "status": status,
            "current_step": cur,
            "estimated_steps": est,
            "verification_tier": tiers.get(fname, "loop-verified"),
        })

    # Counts
    active = queue_view["active_count"]
    queued = queue_view["queued_count"]
    shipped = queue_view["shipped_count"]

    # A `draft` master is authored-but-not-yet-released — non-runnable.
    # Force nothing-actionable so the loop runner (which keys off this exit
    # code) never executes it. This is the manual-path complement to
    # scheduler_scan/promote, which already exclude draft.
    master_status = parse_frontmatter(master_text).get("status", "").strip().lower()
    if master_status == "draft":
        next_pending = None

    # queue_exit: 0 = all shipped / nothing actionable, 1 = pending work, 2 = error
    if next_pending is None:
        queue_exit = 0
    else:
        queue_exit = 1

    result = {
        "master": master.name,
        "master_status": master_status or "(none)",
        "plans_dir": str(plans_dir),
        "subplans": subplans,
        "active": active,
        "queued": queued,
        "shipped": shipped,
        "queue_exit": queue_exit,
    }

    if next_pending:
        fname, status, cur, est, repo = next_pending
        result["next"] = {
            "fname": fname,
            "status": status,
            "cur": cur,
            "est": est,
            "repo": repo,
        }
    else:
        result["next"] = None

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="ilk-loop status checker")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of human text")
    args = ap.parse_args()

    cwd = Path.cwd()
    data = resolve_status(cwd)

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return data["queue_exit"]

    # ── text mode (original behaviour) ──────────────────────────────────
    if data["queue_exit"] == 2:
        print(data.get("error", "Unknown error"), file=sys.stderr)
        return 2

    plans_dir = Path(data["plans_dir"])

    # Meta-project detection for the optional `repo` column.
    project_root, project_kind = find_project_root(cwd)
    meta_members: dict[str, Path] = {}
    if project_kind == "meta" and project_root is not None:
        try:
            manifest = read_meta_manifest(project_root)
            meta_members = {r["name"]: r["path"] for r in manifest["repos"]}
        except MetaManifestError as e:
            print(f"[ilk] meta manifest invalid: {e}", file=sys.stderr)

    # Re-read masters for the queue banner (text mode only).
    masters = sorted(plans_dir.glob("MASTER-*.md"))
    master, queue_view = pick_active_master(masters)

    print(f"Plans dir: {plans_dir}")
    print(f"Master:    {master.name}")
    if queue_view["queued_count"] or queue_view["shipped_count"] or queue_view["paused_count"]:
        print(
            "Queue:     "
            f"active={queue_view['active_count']} "
            f"queued={queue_view['queued_count']} "
            f"paused={queue_view['paused_count']} "
            f"shipped={queue_view['shipped_count']}"
        )
        if queue_view["queued_titles"]:
            print("Up next:")
            for t in queue_view["queued_titles"]:
                print(f"  - {t}")
    print()

    # Reconstruct rows from subplans for the text table.
    rows: list[tuple[str, str, str, str, str, str]] = []  # +verification_tier
    for sp in data["subplans"]:
        rows.append((sp["fname"], sp["status"], sp["current_step"], sp["estimated_steps"], "", sp.get("verification_tier", "loop-verified")))

    if not rows:
        print("Master plan contains no sub-plan references.", file=sys.stderr)
        return 2

    def _tier_suffix(tier: str, status: str) -> str:
        # Only flag SHIPPED sub-plans: a non-loop-verified tier is only a
        # "needs human verification" signal once the work has shipped.
        # Marking pending/in-progress rows would dilute that signal.
        #
        # ASCII-only marker on purpose: this script's stdout is machine-
        # critical (the runner keys off its exit code) and must never crash
        # on a non-UTF-8 console. A "⚠" glyph raised UnicodeEncodeError on a
        # zh-CN cp936/GBK console, the script exited 1, and the runner read
        # that as "pending work" → false stuck-no-progress (wechat-relay,
        # run 20260608-104937).
        return f"  (!) needs-verify:{tier}" if tier != "loop-verified" and status == "shipped" else ""

    show_repo = bool(meta_members)
    name_w = max(len(r[0]) for r in rows)
    name_w = max(name_w, len("sub-plan"))
    if show_repo:
        repo_w = max([len(r[4]) for r in rows] + [len("repo"), len("(?)")])
        print(
            f"{'sub-plan'.ljust(name_w)}  {'repo'.ljust(repo_w)}  status            step"
        )
        print(
            f"{'-' * name_w}  {'-' * repo_w}  ----------------  --------"
        )
        for fname, status, cur, est, repo, tier in rows:
            icon = STATUS_ICONS.get(status, "[??]")
            shown = repo if repo else "(?)"
            suffix = _tier_suffix(tier, status)
            print(
                f"{fname.ljust(name_w)}  {shown.ljust(repo_w)}  "
                f"{icon} {status.ljust(13)} {cur}/{est}{suffix}"
            )
    else:
        print(f"{'sub-plan'.ljust(name_w)}  status            step")
        print(f"{'-' * name_w}  ----------------  --------")
        for fname, status, cur, est, _repo, tier in rows:
            icon = STATUS_ICONS.get(status, "[??]")
            suffix = _tier_suffix(tier, status)
            print(f"{fname.ljust(name_w)}  {icon} {status.ljust(13)} {cur}/{est}{suffix}")

    print()
    if data["next"] is None:
        # next is None for two very different reasons — don't conflate them.
        # (a) every sub-plan is genuinely shipped, or (b) the selected master
        # has non-shipped sub-plans but is non-runnable (status: draft/paused),
        # so they're HELD, not shipped. Reporting "all shipped" for case (b)
        # is a lie (the sub-plans are pending) and misled a run report.
        non_shipped = [sp for sp in data["subplans"] if sp["status"] != "shipped"]
        mstatus = data.get("master_status", "(none)")
        if not non_shipped:
            print(f"All {len(rows)} sub-plans shipped -- nothing to do.")
        elif mstatus in ("draft", "paused"):
            print(
                f"Master is '{mstatus}' (held -- not runnable): "
                f"{len(non_shipped)} non-shipped sub-plan(s), nothing to run. "
                f"Set its status to 'queued'/'active' to release it."
            )
        else:
            print(
                f"Nothing runnable: {len(non_shipped)} non-shipped sub-plan(s), "
                f"but no actionable next (master status '{mstatus}')."
            )
        return 0

    nxt = data["next"]
    print(f"Next: {nxt['fname']}  (status={nxt['status']}, step={nxt['cur']}/{nxt['est']})")
    print(f"Path: {plans_dir / nxt['fname']}")
    if show_repo:
        repo = nxt.get("repo", "")
        if not repo:
            print("Repo: (not declared — meta projects require `repo:` frontmatter)")
        elif repo not in meta_members:
            print(f"Repo: {repo}  (UNKNOWN — not in .ilk-meta.json)")
        else:
            print(f"Repo: {repo}  ({meta_members[repo]})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
