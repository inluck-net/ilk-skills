"""Machine-readable safety kernel: tiers, path checks and release-range check.

Part of guard 1 of the RSI safety case.  A single ``safety-kernel.json``
holds two tiers — ``rules`` (no loop build may change) and ``kernel`` (an
unattended build may not change) — read only through this module.

Consumer contract (2026-10-03h parity):
  - ``touches_kernel(path)`` returns the entry hit or None; semantics
    match h's PROTECTED_KERNEL exactly.
  - ``tier_of(path)`` returns ``(tier, entry)`` or None; ``rules`` wins
    over ``kernel`` when a path matches both.
  - ``check_range`` judges by the list at base, not HEAD.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from plan_status import extract_subplan_files, parse_frontmatter  # noqa: E402


class KernelListError(Exception):
    """Raised when the safety-kernel.json is missing or malformed."""


def _git_output(args: list[str], cwd: Path) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if cp.returncode != 0:
        return ""
    return cp.stdout.strip()


def load(repo: Path | None = None) -> dict:
    """Load and validate ``safety-kernel.json``.

    Returns the parsed dict with ``rules``, ``kernel`` and
    ``kernel_basenames`` keys.  Raises ``KernelListError`` when the file
    is missing, not valid JSON, has the wrong ``schema``, or contains an
    entry without ``path`` or ``why``.
    """
    if repo is None:
        repo = _HERE.parents[2]
    kfile = repo / "skills" / "ilk-loop" / "safety-kernel.json"
    try:
        text = kfile.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise KernelListError(f"safety-kernel.json not found at {kfile}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise KernelListError(f"safety-kernel.json is not valid JSON: {exc}")
    if data.get("schema") != 1:
        raise KernelListError(
            f"expected schema 1, got {data.get('schema')}")
    for tier_name in ("rules", "kernel"):
        for entry in data.get(tier_name, []):
            if "path" not in entry or "why" not in entry:
                raise KernelListError(
                    f"{tier_name} entry missing 'path' or 'why': {entry}")
    return data


def normalise(path: str, repo: Path | None = None) -> str:
    """Strip noise from *path* so it can be compared against entries.

    Removes backticks, ``"`` / ``'`` quotes, leading ``./``, trailing
    ``*`` / ``**`` globs, and a repo-absolute prefix when *repo* is
    given.
    """
    p = path.strip().strip('`"\'')
    if p.startswith("./"):
        p = p[2:]
    if repo:
        rp = str(repo).rstrip("/") + "/"
        if p.startswith(rp):
            p = p[len(rp):]
    while p.endswith("*"):
        p = p[:-1]
    p = p.rstrip("/")
    return p


def _match_entry(path: str, entry_path: str) -> bool:
    """Return True when *path* matches *entry_path*.

    Matching semantics:
    - Exact file equality (both normalised).
    - Entry is a directory (ends with ``/``) and *path* is a file or
      directory under it.
    - *path* is a directory (no extension) and the entry lies under it
      — a listed kernel file inside that directory.
    """
    npath = normalise(path)
    nentry = normalise(entry_path)

    # Exact match.
    if npath == nentry:
        return True

    # Entry is a directory: path must be under it.
    if entry_path.endswith("/"):
        prefix = nentry + "/"
        if npath.startswith(prefix) or npath == nentry:
            return True

    # Path is a directory and the entry is a kernel file under it.
    # Only hits when the entry itself is under the path directory,
    # NOT when the path is under the entry's directory.
    if not os.path.splitext(npath)[1]:
        prefix = npath + "/"
        if nentry.startswith(prefix):
            return True

    return False


def tier_of(path: str, *, kernel: dict | None = None) -> tuple[str, str] | None:
    """Return ``(tier, entry_path)`` for *path*, or ``None``.

    ``rules`` wins over ``kernel`` when a path matches both tiers.
    A basename ``conftest.py`` always hits ``kernel``.
    """
    if kernel is None:
        kernel = load()
    npath = normalise(path)

    # Check rules first (rules wins).
    for entry in kernel.get("rules", []):
        if _match_entry(npath, entry["path"]):
            return ("rules", entry["path"])

    # Then kernel.
    for entry in kernel.get("kernel", []):
        if _match_entry(npath, entry["path"]):
            return ("kernel", entry["path"])

    # Basename conftest.py always hits kernel.
    basename = os.path.basename(npath)
    for bn in kernel.get("kernel_basenames", []):
        if bn.get("name") == basename:
            return ("kernel", f"basename:{basename}")

    return None


def touches_kernel(path: str, *, kernel: dict | None = None) -> str | None:
    """Return the entry hit when *path* is in either tier, else ``None``.

    Semantics match h's PROTECTED_KERNEL: exact file, directory
    containment, or basename ``conftest.py``.
    """
    result = tier_of(path, kernel=kernel)
    if result is None:
        return None
    return result[1]


def kernel_entries(tier: str | None = None) -> list[str]:
    """Return the list of entry paths, optionally filtered by *tier*."""
    kernel = load()
    entries: list[str] = []
    if tier is None or tier == "rules":
        entries.extend(e["path"] for e in kernel.get("rules", []))
    if tier is None or tier == "kernel":
        entries.extend(e["path"] for e in kernel.get("kernel", []))
        entries.extend(e["name"] for e in kernel.get("kernel_basenames", []))
    return entries


def check_paths(paths: list[str], *, kernel: dict | None = None) -> list[dict]:
    """Check which *paths* hit the kernel, returning one dict per hit."""
    if kernel is None:
        kernel = load()
    results: list[dict] = []
    for p in paths:
        result = tier_of(p, kernel=kernel)
        if result is not None:
            results.append({"path": p, "tier": result[0], "entry": result[1]})
    return results


def _git_diff_paths(repo: Path, base: str, head: str) -> list[str]:
    """Return the file paths changed between base and head."""
    out = _git_output(["diff", "--name-only", base, head], repo)
    if not out:
        return []
    return out.splitlines()


def _is_loop_build(sha: str, repo: Path) -> bool:
    """A commit is a loop build when its message carries ``[plan:<slug>#``."""
    msg = _git_output(["log", "-1", "--format=%B", sha], repo)
    return "[plan:" in msg and "#" in msg


def _read_points(repo: Path, plans_dir: Path,
                 ledger_dir: Path | None = None) -> list[dict]:
    """Read points.jsonl rows if the file exists."""
    if ledger_dir is None:
        # Check conventional in-repo path first (works for test repos).
        conventional = repo / ".ilk-data" / "runtime"
        if (conventional / "points.jsonl").exists():
            ledger_dir = conventional
        else:
            # Resolve from suite_ledger if available.
            try:
                from suite_ledger import ledger_dir as _ld  # type: ignore[import-untyped]
                ledger_dir = _ld(repo)
            except Exception:
                return []
    p = ledger_dir / "points.jsonl"
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _point_range_covers(sha: str, repo: Path, point: dict) -> bool:
    """Return True when *sha* lies in a point row's ``before..after`` range."""
    before = point.get("before", "")
    after = point.get("after", "")
    if not before or not after:
        return False
    out = _git_output(["rev-list", f"{before}..{after}"], repo)
    if not out:
        return False
    return sha in out.splitlines()


def _find_master_for_slug(slug: str, plans_dir: Path) -> dict | None:
    """Scan masters in plans_dir for a registry row naming *slug*."""
    for master_file in sorted(plans_dir.glob("MASTER-*.md")):
        text = master_file.read_text(encoding="utf-8-sig")
        fm = parse_frontmatter(text)
        subplans = extract_subplan_files(text)
        # Match: exact filename, filename ending with slug, or slug itself.
        for sp in subplans:
            if sp == f"{slug}.md" or sp == slug or sp.endswith(f"-{slug}.md"):
                return fm
    return None


def check_range(
    repo: Path,
    base: str,
    head: str,
    *,
    plans_dir: Path,
    ledger_dir: Path | None = None,
) -> list[dict]:
    """Return the kernel-range violations in ``base..head``.

    Each violation is a dict with keys: ``sha``, ``path``, ``tier``,
    ``entry``, ``slug``, ``master``, ``reason``.

    The kernel list is judged by the one **at base** (``git show
    <base>:skills/ilk-loop/safety-kernel.json``).  No list at base means
    no violations.
    """
    # Read the kernel list at base.
    raw = _git_output(
        ["show", f"{base}:skills/ilk-loop/safety-kernel.json"], repo)
    if not raw:
        return []
    try:
        kernel = json.loads(raw)
    except json.JSONDecodeError:
        raise KernelListError("corrupt safety-kernel.json at base")
    if kernel.get("schema") != 1:
        raise KernelListError("wrong schema at base")

    commits = _git_output(["rev-list", f"{base}..{head}"], repo)
    points = _read_points(repo, plans_dir, ledger_dir)
    violations: list[dict] = []

    for sha in (commits.splitlines() if commits else []):
        # Determine if this is a loop build: trailer or points.jsonl range.
        is_loop = _is_loop_build(sha, repo)
        if not is_loop:
            # Check points.jsonl for untrailered commits in a range.
            for pt in points:
                if _point_range_covers(sha, repo, pt):
                    is_loop = True
                    break
        if not is_loop:
            continue
        paths = _git_diff_paths(repo, f"{sha}~1", sha)
        for p in paths:
            result = tier_of(p, kernel=kernel)
            if result is None:
                continue
            tier, entry = result

            # Resolve slug and master.
            slug, master_fm = None, None
            for pt in points:
                if _point_range_covers(sha, repo, pt):
                    slug = pt.get("slug")
                    master_fm = _find_master_for_slug(
                        slug, plans_dir) if slug else None
                    break
            if slug is None:
                msg = _git_output(["log", "-1", "--format=%B", sha], repo)
                m = re.search(r"\[plan:([^#\]]+)#", msg)
                if m:
                    slug = m.group(1)
                    master_fm = _find_master_for_slug(slug, plans_dir)

            # Hand commit: no slug, no point match.
            if slug is None:
                continue

            # Determine reason.
            if tier == "rules":
                reason = "rules-edit-by-loop-build"
            else:
                # kernel tier: fail closed if master unresolvable.
                if master_fm is None:
                    reason = "kernel-edit-unresolved-master"
                elif master_fm.get("auto_planned") == "true":
                    reason = "kernel-edit-by-unattended-build"
                else:
                    reason = "kernel-edit-unresolved-master"

            violations.append({
                "sha": sha, "path": p, "tier": tier, "entry": entry,
                "slug": slug,
                "master": (master_fm.get("master_plan", "")
                           if master_fm else ""),
                "reason": reason,
            })

    return violations


def main() -> None:
    ap = argparse.ArgumentParser(
        description="safety-kernel CLI: tiers, path checks, range check")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("tier", help="Print tier of each path as JSON")
    sub.add_parser("check-paths", help="Print hits as JSON (exit 1 on any)")

    cr = sub.add_parser("check-range",
                        help="Print violations in a commit range")
    cr.add_argument("--repo", required=True)
    cr.add_argument("--base", required=True)
    cr.add_argument("--head", required=True)
    cr.add_argument("--plans-dir", required=True)
    cr.add_argument("--ledger-dir", default=None)
    cr.add_argument("--json", dest="json_out", action="store_true")

    args = ap.parse_args()
    if args.cmd is None:
        ap.print_help()
        sys.exit(2)

    if args.cmd == "tier":
        paths = [a for a in sys.argv[2:] if not a.startswith("-")]
        if not paths:
            ap.error("tier requires at least one path")
        kernel = load()
        results = []
        for p in paths:
            r = tier_of(p, kernel=kernel)
            results.append({
                "path": p,
                "tier": r[0] if r else None,
                "entry": r[1] if r else None,
            })
        print(json.dumps(results, indent=2))
        sys.exit(0)

    if args.cmd == "check-paths":
        paths = [a for a in sys.argv[2:] if not a.startswith("-")]
        if not paths:
            ap.error("check-paths requires at least one path")
        hits = check_paths(paths)
        print(json.dumps(hits, indent=2))
        sys.exit(1 if hits else 0)

    if args.cmd == "check-range":
        repo = Path(args.repo)
        plans = Path(args.plans_dir)
        ledger = Path(args.ledger_dir) if args.ledger_dir else None
        try:
            violations = check_range(
                repo, args.base, args.head,
                plans_dir=plans, ledger_dir=ledger,
            )
        except KernelListError as exc:
            print(json.dumps({"error": str(exc)}, indent=2))
            sys.exit(2)
        if args.json_out:
            print(json.dumps(violations, indent=2))
        else:
            if violations:
                for v in violations:
                    print(f"{v['sha']} {v['path']} [{v['tier']}] "
                          f"{v['reason']}")
            else:
                print("clean")
        sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()