#!/usr/bin/env python3
"""
Single-project rich progress dashboard for a ilk-loop run.

Output (deterministic, mechanical only — judgment is the agent's job):

    项目: myproj
    当前: 2026-05-23-audit-orders-and-imports step 8/10

    进度 (3/7 shipped, 1 in-progress, 3 pending)
    [✓] foundation-cleanup-and-radius-audit         4/4   shipped
    [✓] rework-payment-pages                        9/9   shipped
    [✓] audit-home-and-cart                         9/9   shipped
    [▓▓▓▓▓▓▓▓░░] audit-orders-and-imports            8/10  ← here
    [░░░░░░░░░░] audit-wallet-rmb-account            0/10  pending
    [░░░░░░░░░░] audit-user-center-and-bank-card     0/6   pending
    [░░░░░░░░░░] new-mall-orders-member-points       0/10  pending

    节奏 (最近 5 个 step 的平均):  14.2 min/step
    剩余:                          28 步
    ETA (按当前节奏):              ~6h 38min  (今晚 22:35 左右)

Reuses loop_status.py's discovery + frontmatter helpers so ordering stays
identical across the two tools.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HOME = Path(os.path.expanduser("~"))
LOOP_SCRIPTS = HOME / ".cursor" / "skills" / "ilk-loop" / "scripts"
LAUNCHER_DIR = HOME / ".cursor" / "skills" / "ilk-launcher"
PROJECTS_JSON = LAUNCHER_DIR / "projects.json"

# Reuse loop_status.py's helpers to guarantee identical discovery + ordering.
sys.path.insert(0, str(LOOP_SCRIPTS))
from loop_status import find_plans_dir, parse_frontmatter, extract_master_order, pick_active_master  # type: ignore

BAR_WIDTH = 10
PACE_WINDOW = 5  # rolling window of step commits used for "min/step"


# ---------- project resolution -----------------------------------------------


def read_projects_registry() -> list[dict]:
    if not PROJECTS_JSON.exists():
        return []
    try:
        return json.loads(PROJECTS_JSON.read_text(encoding="utf-8")).get("projects", []) or []
    except json.JSONDecodeError:
        return []


def resolve_by_name(name: str) -> Path:
    for entry in read_projects_registry():
        if entry.get("name") == name:
            return Path(entry["path"])
    known = ", ".join(p.get("name", "?") for p in read_projects_registry())
    raise SystemExit(f"Project '{name}' not in projects.json. Known: {known}")


def project_name_for(path: Path) -> str:
    for entry in read_projects_registry():
        if Path(entry.get("path", "")).resolve() == path.resolve():
            return entry.get("name", path.name)
    return path.name


# ---------- sub-plan parsing -------------------------------------------------


def read_subplans(plans_dir: Path) -> tuple[list[dict], Path]:
    """Return (rows, master_path). rows are dicts with slug/status/current/total."""
    masters = sorted(plans_dir.glob("MASTER-*.md"))
    if not masters:
        raise SystemExit(f"No MASTER-*.md in {plans_dir}")
    # Use the queue-aware picker so the dashboard targets the same
    # master as loop_status — single source of truth, even when
    # multiple masters live side-by-side under the queue model.
    master, _ = pick_active_master(masters)
    ordered = extract_master_order(master.read_text(encoding="utf-8"))

    rows: list[dict] = []
    for fname in ordered:
        path = plans_dir / fname
        if not path.exists():
            rows.append({
                "slug": fname.replace(".md", ""),
                "status": "missing",
                "current": 0,
                "total": 0,
            })
            continue
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        slug = fm.get("plan") or fname.replace(".md", "")
        # plan: in front-matter is just the slug; we want the date-prefixed
        # form for visual scanning, so prefer the filename minus .md.
        display_slug = fname.replace(".md", "")
        try:
            current = int(fm.get("current_step", 0))
        except ValueError:
            current = 0
        try:
            total = int(fm.get("estimated_steps", 0))
        except ValueError:
            total = 0
        rows.append({
            "slug": display_slug,
            "status": (fm.get("status") or "pending").strip(),
            "current": current,
            "total": total,
        })
    return rows, master


# ---------- repo + git mining ------------------------------------------------


def find_repos(project_root: Path) -> list[Path]:
    """Mirror run_ilk_loop_claude.ps1's Get-GitRepos: project root itself
    if it has .git, plus immediate child directories with .git."""
    repos: list[Path] = []
    if (project_root / ".git").exists():
        repos.append(project_root)
    try:
        for child in project_root.iterdir():
            if child.is_dir() and (child / ".git").exists():
                if child not in repos:
                    repos.append(child)
    except OSError:
        pass
    return repos


def collect_step_commit_timestamps(repos: list[Path]) -> list[int]:
    """For each repo, run git log --grep for [plan:<slug>#step-N] commits and
    return all unique unix timestamps merged + sorted ascending."""
    seen: set[int] = set()
    for repo in repos:
        try:
            out = subprocess.check_output(
                ["git", "log", "--all",
                 "--grep", r"\[plan:.*#step-",
                 "--pretty=%ct"],
                cwd=str(repo),
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=20,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            continue
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                seen.add(int(line))
    return sorted(seen)


def compute_pace_min_per_step(timestamps: list[int], window: int = PACE_WINDOW) -> float | None:
    """Avg minutes between consecutive step commits in the most recent
    `window` deltas. None if insufficient data."""
    if len(timestamps) < 2:
        return None
    deltas_sec = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
    tail = deltas_sec[-window:]
    if not tail:
        return None
    avg_sec = sum(tail) / len(tail)
    return avg_sec / 60.0


# ---------- rendering -------------------------------------------------------


def bar_for(current: int, total: int, status: str) -> str:
    if status == "shipped":
        return "[" + ("▓" * BAR_WIDTH) + "]"
    if status == "blocked":
        return "[" + ("X" * BAR_WIDTH) + "]"
    if total <= 0:
        return "[" + "?" * BAR_WIDTH + "]"
    filled = int(round((current / total) * BAR_WIDTH))
    filled = max(0, min(BAR_WIDTH, filled))
    return "[" + ("▓" * filled) + ("░" * (BAR_WIDTH - filled)) + "]"


_DATE_PREFIX_RE = __import__("re").compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")


def common_date_prefix(rows: list[dict]) -> str | None:
    """If every row's slug begins with the same YYYY-MM-DD- prefix, return it.
    Otherwise None. Lets us strip the date for compact display."""
    dates: set[str] = set()
    for r in rows:
        m = _DATE_PREFIX_RE.match(r["slug"])
        if not m:
            return None
        dates.add(m.group(1))
    if len(dates) == 1:
        return next(iter(dates))
    return None


def short_slug(slug: str, common_prefix: str | None) -> str:
    if not common_prefix:
        return slug
    if slug.startswith(common_prefix + "-"):
        return slug[len(common_prefix) + 1:]
    return slug


def find_current_in_progress(rows: list[dict]) -> dict | None:
    """First row that is 'in-progress', or first non-shipped row."""
    for r in rows:
        if r["status"] == "in-progress":
            return r
    for r in rows:
        if r["status"] not in ("shipped", "missing"):
            return r
    return None


def format_eta(now: dt.datetime, minutes_ahead: float) -> str:
    target = now + dt.timedelta(minutes=minutes_ahead)
    today = now.date()
    if target.date() == today:
        when = f"今天 {target.strftime('%H:%M')}"
    elif (target.date() - today).days == 1:
        when = f"明天 {target.strftime('%H:%M')}"
    else:
        when = target.strftime("%Y-%m-%d %H:%M")
    return when


def format_duration(minutes: float) -> str:
    if minutes < 60:
        return f"~{minutes:.0f} min"
    h = int(minutes // 60)
    m = int(round(minutes - h * 60))
    if m == 0:
        return f"~{h}h"
    return f"~{h}h {m}min"


def render(
    project_name: str,
    project_root: Path,
    plans_dir: Path,
    master_name: str,
    rows: list[dict],
    pace_min: float | None,
    repos: list[Path],
    step_commit_count: int,
) -> str:
    out: list[str] = []
    out.append(f"项目: {project_name}    路径: {project_root}")
    out.append(f"Master: {master_name}    Plans: {plans_dir}")

    common = common_date_prefix(rows)

    cur = find_current_in_progress(rows)
    if cur:
        out.append(f"当前: {short_slug(cur['slug'], common)} step {cur['current']}/{cur['total']}")
    else:
        out.append("当前: (no in-progress sub-plan)")
    if common:
        out.append(f"批次日期: {common}")
    out.append("")

    shipped = sum(1 for r in rows if r["status"] == "shipped")
    in_prog = sum(1 for r in rows if r["status"] == "in-progress")
    pending = sum(1 for r in rows if r["status"] in ("pending", ""))
    blocked = sum(1 for r in rows if r["status"] == "blocked")
    missing = sum(1 for r in rows if r["status"] == "missing")

    summary_bits = [f"{shipped}/{len(rows)} shipped"]
    if in_prog:  summary_bits.append(f"{in_prog} in-progress")
    if pending:  summary_bits.append(f"{pending} pending")
    if blocked:  summary_bits.append(f"{blocked} blocked")
    if missing:  summary_bits.append(f"{missing} missing")
    out.append("进度 (" + ", ".join(summary_bits) + ")")

    # column widths (after stripping common date prefix)
    display_slugs = [short_slug(r["slug"], common) for r in rows]
    slug_w = max((len(s) for s in display_slugs), default=0)
    slug_w = max(slug_w, 24)
    for r, dslug in zip(rows, display_slugs):
        bar = bar_for(r["current"], r["total"], r["status"])
        ratio = f"{r['current']}/{r['total']}" if r["total"] else "-/-"
        marker = ""
        if cur and r["slug"] == cur["slug"] and r["status"] != "shipped":
            marker = "  ← here"
        out.append(f"{bar} {dslug:<{slug_w}} {ratio:<6} {r['status']}{marker}")

    out.append("")

    remaining = sum(max(0, r["total"] - r["current"]) for r in rows if r["status"] != "shipped")
    out.append(f"剩余 (机械累加，不含已 ship): {remaining} 步")

    if remaining == 0:
        out.append("ETA: all sub-plans shipped — nothing remaining.")
    elif pace_min is None or step_commit_count < 2:
        out.append(
            f"节奏: insufficient data ({step_commit_count} step commits found; need ≥2). "
            "ETA unavailable."
        )
    else:
        out.append(f"节奏 (最近 ≤{PACE_WINDOW} 个 step commit 平均): {pace_min:.1f} min/step  "
                   f"[基于 {step_commit_count} 个 step commit / {len(repos)} 个 repo]")
        eta_min = pace_min * remaining
        when = format_eta(dt.datetime.now(), eta_min)
        out.append(f"ETA (按当前节奏): {format_duration(eta_min)}  ({when})")
    return "\n".join(out)


# ---------- JSON output ------------------------------------------------------


def _read_pid(pid_path: Path) -> int | None:
    """Read a PID file and return the PID, or None if missing/invalid."""
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
        return int(text) if text.isdigit() else None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive (signal 0)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def build_json(
    project_name: str,
    project_root: Path,
    plans_dir: Path,
    master_name: str,
    rows: list[dict],
    pace_min: float | None,
    repos: list[Path],
    step_commit_count: int,
) -> dict[str, Any]:
    """Build the machine-readable JSON structure for --json mode."""
    cur = find_current_in_progress(rows)
    current_block: dict[str, Any] | None = None
    if cur:
        current_block = {
            "slug": cur["slug"],
            "status": cur["status"],
            "current_step": cur["current"],
            "estimated_steps": cur["total"],
        }

    shipped = sum(1 for r in rows if r["status"] == "shipped")
    in_prog = sum(1 for r in rows if r["status"] == "in-progress")
    pending = sum(1 for r in rows if r["status"] in ("pending", ""))
    remaining = sum(max(0, r["total"] - r["current"]) for r in rows if r["status"] != "shipped")
    eta_min = pace_min * remaining if pace_min is not None and remaining > 0 else None

    cur_slug = cur["slug"] if cur else None
    json_rows = []
    for r in rows:
        json_rows.append({
            "slug": r["slug"],
            "status": r["status"],
            "current_step": r["current"],
            "estimated_steps": r["total"],
            "is_current": r["slug"] == cur_slug and r["status"] != "shipped",
        })

    runtime_dir = plans_dir.parent / "runtime"
    launcher_pid = _read_pid(runtime_dir / "launcher" / "running.pid")
    watchdog_pid = _read_pid(runtime_dir / "watchdog" / "watchdog.pid")

    return {
        "project": {
            "name": project_name,
            "root": str(project_root),
        },
        "plans": {
            "dir": str(plans_dir),
            "master": master_name,
        },
        "current": current_block,
        "summary": {
            "shipped": shipped,
            "in_progress": in_prog,
            "pending": pending,
            "remaining_steps": remaining,
            "pace_min_per_step": round(pace_min, 1) if pace_min is not None else None,
            "eta_minutes": round(eta_min, 1) if eta_min is not None else None,
        },
        "processes": {
            "launcher_pid": launcher_pid,
            "launcher_alive": _pid_alive(launcher_pid) if launcher_pid is not None else None,
            "watchdog_pid": watchdog_pid,
            "watchdog_alive": _pid_alive(watchdog_pid) if watchdog_pid is not None else None,
        },
        "rows": json_rows,
    }


# ---------- main -------------------------------------------------------------


def main() -> int:
    # Force UTF-8 stdout so Unicode bar chars render on Windows GBK consoles.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Rich single-project ilk progress dashboard.")
    parser.add_argument("-ProjectPath", "--project-path", dest="project_path", default=None)
    parser.add_argument("-ProjectName", "--project-name", dest="project_name", default=None)
    parser.add_argument("--json", dest="json_mode", action="store_true",
                        help="Emit machine-readable JSON instead of the human-readable dashboard.")
    args = parser.parse_args()

    if args.project_path:
        start = Path(args.project_path).resolve()
    elif args.project_name:
        start = resolve_by_name(args.project_name).resolve()
    else:
        start = Path.cwd().resolve()

    if not start.exists():
        raise SystemExit(f"Project path does not exist: {start}")

    plans_dir = find_plans_dir(start)
    if not plans_dir:
        raise SystemExit(
            f"No docs/plans/MASTER-*.md found walking up from {start}. "
            "Either pass -ProjectPath / -ProjectName, or cd into a project."
        )
    project_root = plans_dir.parent.parent  # plans_dir = <root>/docs/plans
    project_name = args.project_name or project_name_for(project_root)

    rows, master = read_subplans(plans_dir)
    repos = find_repos(project_root)
    timestamps = collect_step_commit_timestamps(repos)
    pace_min = compute_pace_min_per_step(timestamps, PACE_WINDOW)

    if args.json_mode:
        data = build_json(
            project_name=project_name,
            project_root=project_root,
            plans_dir=plans_dir,
            master_name=master.name,
            rows=rows,
            pace_min=pace_min,
            repos=repos,
            step_commit_count=len(timestamps),
        )
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render(
            project_name=project_name,
            project_root=project_root,
            plans_dir=plans_dir,
            master_name=master.name,
            rows=rows,
            pace_min=pace_min,
            repos=repos,
            step_commit_count=len(timestamps),
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
