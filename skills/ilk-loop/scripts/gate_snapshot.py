#!/usr/bin/env python3
"""gate_snapshot — the runner's pre-dispatch copy of a sub-plan's gates.

A sub-plan's ``local_checks`` (in frontmatter and per-step `````yaml`` blocks)
belong to the runner, not to a worker.  Nothing enforced that: a worker could
delete a gate, lower its timeout, or change its command, and the driver would
re-read the edited file and either skip the gate or run a weakened version.

The runner takes a snapshot right before it dispatches the agent and checks it
right after the agent returns, before any runner-owned write.  A sub-plan
whose gates changed is restored to the snapshot, and the runner ends the run
``ship_integrity_violation``.

A planner/operator edit **between** runs is allowed: the snapshot is taken at
each dispatch, so a gate changed between runs is the new baseline.

Usage:
  gate_snapshot.py take    --plans-dir D --out F --slug SLUG
  gate_snapshot.py restore --plans-dir D --snapshot F --slug SLUG

``restore`` exits 0 = nothing restored; 3 = gates were restored; 2 = error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_local_checks import (  # noqa: E402
    extract_step_local_checks,
    parse_local_checks_block,
    split_frontmatter,
    steps_section_body,
)

_STEP_HEADING_RE = re.compile(r"^###\s+Step\s+(\d+)", re.MULTILINE)


def _find_subplan(plans_dir: Path, slug: str) -> Path:
    """Locate a sub-plan file by its frontmatter ``plan:`` slug."""
    import glob
    matches = glob.glob(str(plans_dir / f"*-{slug}.md"))
    if not matches:
        raise FileNotFoundError(f"no sub-plan file for slug {slug!r} in {plans_dir}")
    return Path(matches[0])


def _extract_per_step_gates(body: str) -> dict[str, list[dict]]:
    """Extract gates per step: ``{step_n: [{command, timeout}, ...]}``.

    Uses the same parser as ``collect_declared_local_checks`` but keeps
    gates separate by step so deduplication across steps does not hide a
    removed gate.
    """
    fm_text, body_text = split_frontmatter(body)
    scoped = steps_section_body(body_text)

    result: dict[str, list[dict]] = {}

    # Frontmatter gates (step "frontmatter").
    fm_checks = list(parse_local_checks_block(fm_text))
    if fm_checks:
        result["frontmatter"] = [
            {"command": c.get("command", ""), "timeout": c.get("timeout")}
            for c in fm_checks
        ]

    # Per-step gates.
    for step_n in sorted({int(n) for n in _STEP_HEADING_RE.findall(scoped)}):
        checks = list(extract_step_local_checks(scoped, step_n))
        if checks:
            result[str(step_n)] = [
                {"command": c.get("command", ""), "timeout": c.get("timeout")}
                for c in checks
            ]

    return result


def take(plans_dir: Path, slug: str) -> dict:
    """Snapshot the gates of a sub-plan, per-step.

    Returns a dict with ``slug``, ``path``, and ``gates`` (a dict mapping
    step number to list of gate dicts).
    """
    sub_file = _find_subplan(plans_dir, slug)
    body = sub_file.read_text(encoding="utf-8-sig")
    gates = _extract_per_step_gates(body)
    return {
        "slug": slug,
        "path": str(sub_file),
        "gates": gates,
    }


def _norm_gate(g: dict) -> tuple[str, str]:
    """Normalize a gate for comparison: (command stripped, timeout str)."""
    return (g.get("command", "").strip(), str(g.get("timeout", "")))


def restore(plans_dir: Path, slug: str, snapshot: dict) -> bool:
    """Compare current gates with the snapshot; restore if different.

    Returns True if gates were restored (violation detected).
    """
    sub_file = _find_subplan(plans_dir, slug)
    body = sub_file.read_text(encoding="utf-8-sig")
    current_gates = _extract_per_step_gates(body)

    snap_gates = snapshot.get("gates", {})

    # Compare per-step.
    all_steps = set(list(current_gates.keys()) + list(snap_gates.keys()))
    for step in all_steps:
        cur = [_norm_gate(g) for g in current_gates.get(step, [])]
        snap = [_norm_gate(g) for g in snap_gates.get(step, [])]
        if cur != snap:
            # Gates differ — restore the snapshotted gates into the file.
            _restore_gates(sub_file, snap_gates)
            return True

    return False


def _restore_gates(sub_file: Path, snap_gates: dict[str, list[dict]]) -> None:
    """Rewrite the sub-plan's local_checks blocks to match the snapshot.

    Restores the entire yaml fence for each step that has gates in the
    snapshot.  Steps not in the snapshot are left alone.  If a step's
    fence was removed entirely, re-inserts it after the step heading.
    """
    body = sub_file.read_text(encoding="utf-8-sig")

    def _build_fence(step_gates: list[dict]) -> str:
        """Build a yaml fence body from snapshotted gates."""
        lines = ["```yaml", "gate_first: true", "local_checks:"]
        for g in step_gates:
            lines.append(f'  - command: "{g["command"]}"')
            lines.append(f'    timeout: {g["timeout"]}')
        lines.append("```")
        return "\n".join(lines)

    # Split the body at step headings to handle missing fences.
    # Find all step headings and their positions.
    step_pat = re.compile(r"^(###\s+Step\s+(\d+).*?)$", re.MULTILINE)

    parts: list[str] = []
    last_end = 0
    for m in step_pat.finditer(body):
        step_heading = m.group(1)
        step_n = m.group(2)
        heading_start = m.start()
        heading_end = m.end()

        # Add everything before this heading.
        parts.append(body[last_end:heading_start])

        # Check if this step has snapshotted gates.
        step_gates = snap_gates.get(step_n, [])
        if not step_gates:
            # No gates to restore — keep the heading as-is.
            parts.append(step_heading)
            last_end = heading_end
            continue

        # Check if there's already a yaml fence after this heading.
        after = body[heading_end:]
        fence_match = re.match(
            r"\s*```(?:yaml|yml)?\s*\n.*?^```",
            after,
            re.MULTILINE | re.DOTALL,
        )
        if fence_match:
            # Replace the existing fence.
            parts.append(step_heading)
            parts.append("\n\n")
            parts.append(_build_fence(step_gates))
            last_end = heading_end + fence_match.end()
        else:
            # No fence — insert one after the heading.
            parts.append(step_heading)
            parts.append("\n\n")
            parts.append(_build_fence(step_gates))
            last_end = heading_end

    parts.append(body[last_end:])
    sub_file.write_text("".join(parts), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="gate snapshot take/restore")
    sub = parser.add_subparsers(dest="cmd")

    take_p = sub.add_parser("take")
    take_p.add_argument("--plans-dir", required=True, type=Path)
    take_p.add_argument("--out", required=True, type=Path)
    take_p.add_argument("--slug", required=True)

    restore_p = sub.add_parser("restore")
    restore_p.add_argument("--plans-dir", required=True, type=Path)
    restore_p.add_argument("--snapshot", required=True, type=Path)
    restore_p.add_argument("--slug", required=True)

    args = parser.parse_args()

    if args.cmd == "take":
        snap = take(args.plans_dir, args.slug)
        args.out.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    elif args.cmd == "restore":
        try:
            snap = json.loads(args.snapshot.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"error: could not read snapshot: {exc}", file=sys.stderr)
            raise SystemExit(2)
        try:
            restored = restore(args.plans_dir, args.slug, snap)
        except Exception as exc:
            print(f"error: could not restore: {exc}", file=sys.stderr)
            raise SystemExit(2)
        if restored:
            slug = snap.get("slug", args.slug)
            print(f"! [gates] {slug}: gates changed; restored to snapshot")
            raise SystemExit(3)
    else:
        parser.print_help()
        raise SystemExit(1)


if __name__ == "__main__":
    main()