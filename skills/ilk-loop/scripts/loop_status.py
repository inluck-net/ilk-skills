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
        r"(?:^|(?<=[\s(\[]))(?:\./)?(\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*\.md)",
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
    for p in masters:
        try:
            text = p.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        parsed.append((p, parse_frontmatter(text)))

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
    elif paused or shipped:
        # All masters are terminal — nothing to do, but pick the most
        # recent so the table renders.
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
        "queued_titles": [it[0].name for it in queued],
    }
    return chosen, queue_view


def main() -> int:
    plans_dir, plans_source = _resolve_plans_dir(Path.cwd())
    if not plans_dir:
        print("No MASTER-*.md found in ~/.ilk-data, in-tree docs/plans/, or any ancestor.", file=sys.stderr)
        print("Run this command from inside a project that uses the ilk-loop convention.", file=sys.stderr)
        return 2
    if plans_source != "external":
        # Soft nudge — does not block, but tells users they're on the
        # legacy in-tree layout and could migrate.
        print(
            f"[ilk] plans dir: {plans_dir} (source: {plans_source}). "
            "Consider migrating to ~/.ilk-data — see migrate-plans-to-external.py.",
            file=sys.stderr,
        )

    # Meta-project detection: drives the optional `repo` column.
    project_root, project_kind = find_project_root(Path.cwd())
    meta_members: dict[str, Path] = {}
    if project_kind == "meta" and project_root is not None:
        try:
            manifest = read_meta_manifest(project_root)
            meta_members = {r["name"]: r["path"] for r in manifest["repos"]}
        except MetaManifestError as e:
            print(f"[ilk] meta manifest invalid: {e}", file=sys.stderr)

    masters = sorted(plans_dir.glob("MASTER-*.md"))
    if not masters:
        print(f"No MASTER-*.md in {plans_dir}.", file=sys.stderr)
        return 2

    master, queue_view = pick_active_master(masters)
    master_text = master.read_text(encoding="utf-8")
    ordered_files = extract_master_order(master_text)

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

    # Each row is (fname, status, cur_step, est, repo). `repo` is "" in
    # single-mode projects and the resolved member name in meta-mode.
    rows: list[tuple[str, str, str, str, str]] = []
    # Prefer actionable work: pending / in-progress. Blocked plans (external
    # deps) should not prevent `/ilk` from advancing later sub-plans that
    # are still machine-verifiable on the current host.
    next_actionable: tuple[str, str, str, str, str] | None = None
    next_blocked: tuple[str, str, str, str, str] | None = None
    next_other: tuple[str, str, str, str, str] | None = None

    unknown_repos: set[str] = set()

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
        # In meta mode, surface mismatched repo names so the operator
        # notices typos / stale member lists at status time.
        if meta_members and repo and repo not in meta_members:
            unknown_repos.add(repo)
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

    if not rows:
        print("Master plan contains no sub-plan references.", file=sys.stderr)
        return 2

    # In meta mode, render an extra `repo` column. Width is computed to
    # accommodate the widest member name AND the column header.
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
        for fname, status, cur, est, repo in rows:
            icon = STATUS_ICONS.get(status, "[??]")
            shown = repo if repo else "(?)"
            print(
                f"{fname.ljust(name_w)}  {shown.ljust(repo_w)}  "
                f"{icon} {status.ljust(13)} {cur}/{est}"
            )
    else:
        print(f"{'sub-plan'.ljust(name_w)}  status            step")
        print(f"{'-' * name_w}  ----------------  --------")
        for fname, status, cur, est, _repo in rows:
            icon = STATUS_ICONS.get(status, "[??]")
            print(f"{fname.ljust(name_w)}  {icon} {status.ljust(13)} {cur}/{est}")

    if unknown_repos:
        print()
        print(
            f"[ilk] WARNING: sub-plans reference unknown meta repos: "
            f"{sorted(unknown_repos)}. Known: {sorted(meta_members)}.",
            file=sys.stderr,
        )

    print()
    next_pending = next_actionable or next_other or next_blocked
    if next_pending is None:
        print(f"All {len(rows)} sub-plans shipped -- nothing to do.")
        return 0

    fname, status, cur, est, repo = next_pending
    print(f"Next: {fname}  (status={status}, step={cur}/{est})")
    print(f"Path: {plans_dir / fname}")
    if show_repo:
        if not repo:
            print("Repo: (not declared — meta projects require `repo:` frontmatter)")
        elif repo not in meta_members:
            print(f"Repo: {repo}  (UNKNOWN — not in .ilk-meta.json)")
        else:
            print(f"Repo: {repo}  ({meta_members[repo]})")
    if next_blocked and next_actionable and next_blocked[0] != next_pending[0]:
        bname, _bstat, bcur, best, _brepo = next_blocked
        print()
        print(f"(Parked blocked: {bname} step {bcur}/{best} - resume when unblocked.)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
