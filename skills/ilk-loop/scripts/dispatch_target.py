#!/usr/bin/env python3
"""Resolve an existing selected plan identity; no independent scheduler/state.

Uses the master's exact registry filenames. Filename identity remains canonical;
frontmatter aliases support legacy generated plans. Multiple matches are refused.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from plan_slug import strip_date_prefix
from plan_status import extract_subplan_files, parse_frontmatter


class TargetError(ValueError):
    pass


def identities(path: Path) -> set[str]:
    fm = parse_frontmatter(path.read_text(encoding='utf-8-sig'))
    return {strip_date_prefix(path.stem), fm.get('plan', '').strip()} - {''}


def resolve(plans: Path, slug: str, *, master: Path | None = None,
            filename: str | None = None) -> dict:
    plans = plans.resolve()
    masters = [master] if master else sorted(plans.glob('MASTER-*.md'))
    matches = []
    for candidate in masters:
        candidate = candidate.resolve()
        if candidate.parent != plans or not candidate.is_file():
            raise TargetError('selected master is missing or outside plans root')
        text = candidate.read_text(encoding='utf-8-sig')
        for name in extract_subplan_files(text):
            path = (plans/name).resolve()
            if path.parent != plans:
                raise TargetError('registry plan escapes plans root')
            if filename and name != filename:
                continue
            if not path.is_file():
                if filename == name:
                    raise TargetError('selected registry plan is missing')
                continue
            if slug in identities(path):
                matches.append((candidate, path, parse_frontmatter(text)))
    # Legacy no-master callers can resolve exact identity, but never choose first.
    if not masters:
        for path in sorted(plans.glob('*.md')):
            if not path.name.startswith('MASTER') and slug in identities(path):
                matches.append((None, path.resolve(), {}))
    if len(matches) != 1:
        raise TargetError(f'selected identity has {len(matches)} matches; expected exactly one')
    selected_master, selected_plan, fm = matches[0]
    return {'master_path': str(selected_master) if selected_master else None,
            'subplan_path': str(selected_plan), 'slug': slug,
            'batch_base_sha': fm.get('base_sha') or None}


def pinned_subplan(plans: Path, slug: str) -> Path | None:
    """Validate and return the driver's selected path, without re-selection."""
    context_path = os.environ.get('ILK_ITER_CONTEXT')
    selected = None
    if context_path:
        try:
            context = json.loads(Path(context_path).read_text())
            if Path(context['plans_dir']).resolve() != plans.resolve():
                raise TargetError('iteration context belongs to another plans root')
            selected = context.get('dispatch')
        except (OSError, ValueError, KeyError) as exc:
            raise TargetError(f'iteration context unreadable or inconsistent: {exc}') from exc
    if selected and selected.get('slug') == slug:
        raw = selected['subplan_path']
        legacy = os.environ.get('ILK_DISPATCH_SUBPLAN')
        if os.environ.get('ILK_DISPATCH_SLUG') == slug and legacy and Path(legacy).resolve() != Path(raw).resolve():
            raise TargetError('legacy dispatch contradicts frozen iteration context')
    else:
        if os.environ.get('ILK_DISPATCH_SLUG') != slug:
            return None
        raw = os.environ.get('ILK_DISPATCH_SUBPLAN', '')
    if not raw:
        raise TargetError('pinned selected plan is absent')
    path = Path(raw).resolve()
    if path.parent != plans.resolve() or not path.is_file() or slug not in identities(path):
        raise TargetError('pinned selected plan is missing, outside plans root or inconsistent')
    return path


def format_brief(identity: dict, step: int, project: str, skills: Path) -> str:
    path = identity['subplan_path']
    master = identity.get('master_path') or 'unknown; use the selected subplan link'
    base = identity.get('batch_base_sha') or 'unknown'
    lines = [
        'DISPATCH CONTEXT (driver-selected work; keep required instruction reads):',
        f'Project: {project}', f'Plans: {Path(path).parent}',
        f'Pinned loop instructions: {skills / "ilk-loop" / "SKILL.md"}',
        f'Master: {master}', f'Subplan: {path}', f'Selected step: {step}',
        f'Frozen batch base (not iteration HEAD): {base}',
        f'Gate entry point: {skills / "ilk-loop" / "scripts" / "run_local_checks.py"} --print-step-gate "{path}" {step}',
        'Read the exact instructions/master/subplan above; use existing fixtures and repository guidance.',
        'Start at this step. Do not scan all plans or rediscover the skill root; group independent reads when supported.',
        'These paths are context, not gate success or permission to change driver-owned proof/state.',
    ]
    from iteration_context import read_context
    context=read_context()
    if context and context.get('recovery_dir'):
        from recovery import brief
        lines.extend(brief(context,identity['slug'],step))
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plans-dir', type=Path, required=True)
    p.add_argument('--slug', required=True)
    p.add_argument('--master', type=Path)
    p.add_argument('--filename')
    p.add_argument('--brief', action='store_true')
    p.add_argument('--step', type=int)
    p.add_argument('--project')
    p.add_argument('--skill-root', type=Path)
    args = p.parse_args()
    try:
        identity = resolve(args.plans_dir,args.slug,master=args.master,filename=args.filename)
        if args.brief:
            if args.step is None or args.step < 0 or not args.project or not args.skill_root:
                raise TargetError('brief requires project, skill root and nonnegative step')
            print(format_brief(identity,args.step,args.project,args.skill_root))
        else:
            print(json.dumps(identity))
    except (TargetError, OSError) as exc:
        print(f'dispatch identity refused: {exc}', file=sys.stderr)
        raise SystemExit(2)


if __name__=='__main__': main()
