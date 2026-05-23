"""init_meta_project — generate `.ilk-meta.json` for a polyrepo parent dir.

Scans the immediate children of `--root` (one level deep) for directories
that contain `.git` (file or dir) — i.e. git repos and git worktrees —
and writes a `.ilk-meta.json` marker enumerating them as members.

When the parent already has `.ilk-meta.json`:
  --merge   add only missing members; keep existing entries untouched
  --force   overwrite outright
  (neither) refuse and exit 2

This is a one-shot bootstrap. The resulting file is intended to be
hand-edited (reorder, rename, exclude non-shipping helper repos, etc.)
before the first ilk-plan run. Re-running with --merge after adding new
sub-repos is safe.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def scan_members(root: Path) -> list[dict]:
    """Return [{'name', 'path'} ...] for every child of root that has .git.

    Sorted by name for stable output. `path` is stored relative to root
    so the manifest stays portable across machines / clones.
    """
    members: list[dict] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if not (child / ".git").exists():
            continue
        members.append({"name": child.name, "path": child.name})
    return members


def load_existing(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


def merge_members(existing: list[dict], discovered: list[dict]) -> tuple[list[dict], list[str]]:
    """Append discovered entries that don't share a name with anything
    in `existing`. Returns (merged_list, names_added)."""
    seen = {e.get("name") for e in existing if isinstance(e, dict)}
    added: list[str] = []
    merged = list(existing)
    for d in discovered:
        if d["name"] not in seen:
            merged.append(d)
            added.append(d["name"])
            seen.add(d["name"])
    return merged, added


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=Path,
                    help="parent dir to mark as a meta project (must exist)")
    ap.add_argument("--name", default=None,
                    help="logical project name (default: --root's basename)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing .ilk-meta.json")
    ap.add_argument("--merge", action="store_true",
                    help="merge newly-discovered members into existing file")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the manifest to stdout without writing")
    args = ap.parse_args(argv)

    if args.force and args.merge:
        print("--force and --merge are mutually exclusive", file=sys.stderr)
        return 2

    root = args.root.resolve()
    if not root.is_dir():
        print(f"--root is not a directory: {root}", file=sys.stderr)
        return 2

    if (root / ".git").exists():
        print(
            f"refusing to mark {root}: it is itself a git repo. Meta "
            "projects are NON-git parent dirs containing sibling git "
            "repos. Use ilk-loop's normal single-repo mode instead.",
            file=sys.stderr,
        )
        return 2

    discovered = scan_members(root)
    if not discovered:
        print(f"no .git children found under {root}. Nothing to mark.", file=sys.stderr)
        return 2

    target = root / ".ilk-meta.json"
    existing = load_existing(target)

    if existing is not None and not args.force and not args.merge:
        print(
            f"{target} already exists. Use --merge to add new members or "
            "--force to overwrite.",
            file=sys.stderr,
        )
        return 2

    if existing is not None and args.merge:
        ex_repos = existing.get("repos", []) if isinstance(existing.get("repos"), list) else []
        merged, added = merge_members(ex_repos, discovered)
        out_doc = dict(existing)
        out_doc["repos"] = merged
        if args.name:
            out_doc["name"] = args.name
        elif "name" not in out_doc:
            out_doc["name"] = root.name
        action = f"merged ({len(added)} added: {', '.join(added) or '—'})"
    else:
        out_doc = {
            "name": args.name or root.name,
            "repos": discovered,
        }
        action = "overwrite" if existing is not None else "create"

    rendered = json.dumps(out_doc, indent=2, ensure_ascii=False) + "\n"
    if args.dry_run:
        print(rendered, end="")
        print(f"# would {action} → {target}", file=sys.stderr)
        return 0

    target.write_text(rendered, encoding="utf-8")
    print(f"{action}: {target}")
    print(f"members: {[m['name'] for m in out_doc['repos']]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
