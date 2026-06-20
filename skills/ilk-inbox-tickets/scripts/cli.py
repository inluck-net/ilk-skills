"""CLI entry point for the ilk-inbox-tickets skill.

Usage:
  python3 cli.py list [--status STATUS] [--project SLUG | --all] [--json]
                      [--inbox PATH] [--registry PATH]
  python3 cli.py show <slug> [--inbox PATH]
  python3 cli.py update <slug> --status STATUS [--plan PATH] [--inbox PATH]
  python3 cli.py archive <slug> [--archive PATH] [--inbox PATH]
  python3 cli.py resolve [--inbox PATH] [--registry PATH]

Per-subcommand flags for hermetic testing:
  --inbox PATH       Path to inbox file (default: ~/Documents/handoffs/_inbox.md)
  --registry PATH    Path to project registry (default: ~/.ilk-data/inbox-projects.json)

All commands print human-readable output to stdout by default.
Use --json on `list` or `resolve` for machine-readable output.
Errors go to stderr with non-zero exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Force UTF-8 stdout/stderr for cross-platform consistency
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# allow running as `python3 cli.py ...` regardless of cwd
sys.path.insert(0, str(Path(__file__).parent))

import inbox_parser  # noqa: E402
import project_registry  # noqa: E402
from inbox_parser import Entry, parse_inbox, group_by_project, is_ilk_eligible  # noqa: E402
from project_registry import load_registry, needs_mapping, resolve, UNRESOLVED, NOT_PLANNABLE  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print(obj):
    """Print a JSON object to stdout."""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _resolve_inbox_path(args) -> Path:
    """Resolve the inbox file path from args or default."""
    return Path(args.inbox) if args.inbox else Path.home() / "Documents" / "handoffs" / "_inbox.md"


def _resolve_registry_path(args) -> Path | None:
    """Resolve the registry file path from args or default."""
    return Path(args.registry) if args.registry else None


def _cwd_project_slug(registry: dict) -> str | None:
    """Try to determine which registry project the cwd belongs to."""
    cwd = Path.cwd().resolve()
    projects = registry.get("projects", {})
    for slug, info in projects.items():
        if not isinstance(info, dict):
            continue
        p = info.get("path")
        if not p:
            continue
        try:
            repo_root = Path(p).resolve()
            if cwd == repo_root or cwd.is_relative_to(repo_root):
                return slug
        except (OSError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args):
    """List eligible inbox entries."""
    inbox_path = _resolve_inbox_path(args)
    reg_path = _resolve_registry_path(args)

    if not inbox_path.exists():
        print(f"ERROR: inbox file not found: {inbox_path}", file=sys.stderr)
        sys.exit(1)

    registry = load_registry(reg_path)
    entries = parse_inbox(inbox_path)
    status_filter = args.status or "pending"

    if args.all:
        # Group by project, all resolvable projects
        grouped = group_by_project(entries, registry, status=status_filter)
        if args.json:
            output = {}
            for repo_root, group_entries in grouped.items():
                output[repo_root] = [
                    {"slug": e.slug, "date": e.date, "fields": e.fields}
                    for e in group_entries
                ]
            _print(output)
        else:
            if not grouped:
                print(f"No eligible {status_filter} entries found.")
                return
            for repo_root, group_entries in grouped.items():
                print(f"\n=== {repo_root} ===")
                for e in group_entries:
                    print(f"  {e.date} — {e.slug}")
                    if e.fields.get("Scope"):
                        print(f"    Scope: {e.fields['Scope']}")
    else:
        # Filter to one project
        project_slug = args.project
        if not project_slug:
            project_slug = _cwd_project_slug(registry)

        if not project_slug:
            print(
                "ERROR: could not determine project from cwd. "
                "Use --project <slug> or --all.",
                file=sys.stderr,
            )
            sys.exit(1)

        resolved = resolve(project_slug, registry)
        if resolved is UNRESOLVED:
            print(f"ERROR: project '{project_slug}' not found in registry.", file=sys.stderr)
            sys.exit(1)
        if resolved is NOT_PLANNABLE:
            print(f"ERROR: project '{project_slug}' is marked not_plannable.", file=sys.stderr)
            sys.exit(1)

        # Filter entries to this project
        project_entries = [
            e for e in entries
            if e.fields.get("Project") == project_slug
            and e.status.get("state") == status_filter
            and is_ilk_eligible(e, registry)
        ]

        if args.json:
            _print([
                {"slug": e.slug, "date": e.date, "fields": e.fields}
                for e in project_entries
            ])
        else:
            if not project_entries:
                print(f"No eligible {status_filter} entries for '{project_slug}'.")
                return
            print(f"\n=== {project_slug} ({resolved}) ===")
            for e in project_entries:
                print(f"  {e.date} — {e.slug}")
                if e.fields.get("Scope"):
                    print(f"    Scope: {e.fields['Scope']}")


def cmd_show(args):
    """Show one entry's full fields + body."""
    inbox_path = _resolve_inbox_path(args)

    if not inbox_path.exists():
        print(f"ERROR: inbox file not found: {inbox_path}", file=sys.stderr)
        sys.exit(1)

    entries = parse_inbox(inbox_path)
    target = None
    for e in entries:
        if e.slug == args.slug:
            target = e
            break

    if not target:
        print(f"ERROR: entry '{args.slug}' not found in inbox.", file=sys.stderr)
        sys.exit(1)

    # Print fields
    output = {
        "slug": target.slug,
        "date": target.date,
        "fields": target.fields,
        "status": target.status,
        "body": target.body,
    }

    # Resolve Tier-2 related handoff
    if target.related_handoff:
        output["related_handoff"] = str(target.related_handoff)
        if target.related_handoff.exists():
            output["related_handoff_content"] = target.related_handoff.read_text(encoding="utf-8")
        else:
            output["related_handoff_content"] = None
            output["related_handoff_note"] = "file not found"

    _print(output)


def cmd_update(args):
    """Update an entry's Status line and add/refresh a Plan line."""
    inbox_path = _resolve_inbox_path(args)

    if not inbox_path.exists():
        print(f"ERROR: inbox file not found: {inbox_path}", file=sys.stderr)
        sys.exit(1)

    text = inbox_path.read_text(encoding="utf-8")
    entries = parse_inbox(inbox_path)

    target = None
    for e in entries:
        if e.slug == args.slug:
            target = e
            break

    if not target:
        print(f"ERROR: entry '{args.slug}' not found in inbox.", file=sys.stderr)
        sys.exit(1)

    # Find the entry block boundaries in the raw text
    import re
    heading_pattern = re.compile(r"^## \d{4}-\d{2}-\d{2} — " + re.escape(args.slug) + r"$", re.MULTILINE)
    match = heading_pattern.search(text)
    if not match:
        print(f"ERROR: could not locate heading for '{args.slug}'.", file=sys.stderr)
        sys.exit(1)

    # Find the end of this entry block (next ## heading or EOF)
    next_heading = re.compile(r"^## \d{4}-\d{2}-\d{2} — ", re.MULTILINE)
    end_match = next_heading.search(text, match.end())
    block_end = end_match.start() if end_match else len(text)

    block = text[match.start():block_end]

    # Replace **Status** line
    new_status_line = f"**Status**: {args.status}"
    block = re.sub(r"^\*\*Status\*\*:.*$", new_status_line, block, count=1, flags=re.MULTILINE)

    # Add or replace **Plan** line
    if args.plan:
        new_plan_line = f"**Plan**: {args.plan}"
        if "**Plan**:" in block:
            block = re.sub(r"^\*\*Plan\*\*:.*$", new_plan_line, block, count=1, flags=re.MULTILINE)
        else:
            # Insert after **Status** line
            block = re.sub(
                r"(^\*\*Status\*\*:.*$)",
                r"\1\n" + new_plan_line,
                block,
                count=1,
                flags=re.MULTILINE,
            )

    # Atomic write: temp file + replace
    import tempfile
    import os

    new_text = text[:match.start()] + block + text[block_end:]

    fd, tmp = tempfile.mkstemp(dir=str(inbox_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.replace(tmp, str(inbox_path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    _print({"ok": True, "slug": args.slug, "status": args.status, "plan": args.plan})


def cmd_archive(args):
    """Move an entry from the inbox to the archive file."""
    inbox_path = _resolve_inbox_path(args)
    archive_path = Path(args.archive) if args.archive else inbox_path.parent / "_inbox-archive.md"

    if not inbox_path.exists():
        print(f"ERROR: inbox file not found: {inbox_path}", file=sys.stderr)
        sys.exit(1)

    text = inbox_path.read_text(encoding="utf-8")
    entries = parse_inbox(inbox_path)

    target = None
    for e in entries:
        if e.slug == args.slug:
            target = e
            break

    if not target:
        print(f"ERROR: entry '{args.slug}' not found in inbox.", file=sys.stderr)
        sys.exit(1)

    # Find the entry block boundaries
    import re
    heading_pattern = re.compile(r"^## \d{4}-\d{2}-\d{2} — " + re.escape(args.slug) + r"$", re.MULTILINE)
    match = heading_pattern.search(text)
    if not match:
        print(f"ERROR: could not locate heading for '{args.slug}'.", file=sys.stderr)
        sys.exit(1)

    next_heading = re.compile(r"^## \d{4}-\d{2}-\d{2} — ", re.MULTILINE)
    end_match = next_heading.search(text, match.end())
    block_end = end_match.start() if end_match else len(text)

    block = text[match.start():block_end]

    # Remove from inbox (atomic)
    import tempfile
    import os

    new_inbox_text = text[:match.start()] + text[block_end:]

    fd, tmp = tempfile.mkstemp(dir=str(inbox_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_inbox_text)
        os.replace(tmp, str(inbox_path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # Append to archive
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with open(archive_path, "a", encoding="utf-8") as fh:
        fh.write(block)

    _print({"ok": True, "slug": args.slug, "archived_to": str(archive_path)})


def cmd_resolve(args):
    """Print the needs-mapping report (entries with unresolved projects)."""
    inbox_path = _resolve_inbox_path(args)
    reg_path = _resolve_registry_path(args)

    if not inbox_path.exists():
        print(f"ERROR: inbox file not found: {inbox_path}", file=sys.stderr)
        sys.exit(1)

    registry = load_registry(reg_path)
    entries = parse_inbox(inbox_path)
    unmapped = needs_mapping(entries, registry)

    if args.json:
        _print([
            {"slug": e.slug, "date": e.date, "project": e.fields.get("Project", "")}
            for e in unmapped
        ])
    else:
        if not unmapped:
            print("All entries have resolved projects.")
        else:
            print(f"Unresolved entries ({len(unmapped)}):")
            for e in unmapped:
                proj = e.fields.get("Project", "(none)")
                print(f"  {e.date} — {e.slug}  (Project: {proj})")

    sys.exit(1 if unmapped else 0)


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def _add_testing_flags(parser):
    """Add --inbox and --registry flags for hermetic testing."""
    parser.add_argument("--inbox", help="Path to inbox file (default: ~/Documents/handoffs/_inbox.md)")
    parser.add_argument("--registry", help="Path to project registry (default: ~/.ilk-data/inbox-projects.json)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ilk-inbox-tickets")

    sub = p.add_subparsers(dest="cmd", required=True)

    # list
    sp = sub.add_parser("list", help="List eligible inbox entries")
    _add_testing_flags(sp)
    sp.add_argument("--status", default="pending", help="Filter by status (default: pending)")
    sp.add_argument("--project", help="Filter to a specific project slug")
    sp.add_argument("--all", action="store_true", help="Show all resolvable projects, grouped")
    sp.add_argument("--json", action="store_true", help="Output as JSON")
    sp.set_defaults(func=cmd_list)

    # show
    sp = sub.add_parser("show", help="Show one entry's full fields + body")
    _add_testing_flags(sp)
    sp.add_argument("slug", help="Entry slug")
    sp.set_defaults(func=cmd_show)

    # update
    sp = sub.add_parser("update", help="Update an entry's status and plan")
    _add_testing_flags(sp)
    sp.add_argument("slug", help="Entry slug")
    sp.add_argument("--status", required=True, help="New status value")
    sp.add_argument("--plan", help="Plan path or URL to add/refresh")
    sp.set_defaults(func=cmd_update)

    # archive
    sp = sub.add_parser("archive", help="Move an entry to the archive file")
    _add_testing_flags(sp)
    sp.add_argument("slug", help="Entry slug")
    sp.add_argument("--archive", help="Archive file path (default: _inbox-archive.md)")
    sp.set_defaults(func=cmd_archive)

    # resolve
    sp = sub.add_parser("resolve", help="Show entries with unresolved projects")
    _add_testing_flags(sp)
    sp.add_argument("--json", action="store_true", help="Output as JSON")
    sp.set_defaults(func=cmd_resolve)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
