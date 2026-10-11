"""Resolve the selected launch's settings/dependency root before preparation."""
from pathlib import Path
import argparse
from iteration_context import ContextError
from ilk_paths import find_plans_dir
from loop_status import pick_active_master
from work_tree import resolve_work_tree


def resolve(project, *, master=None):
    project=Path(project).resolve(strict=True)
    plans,_=find_plans_dir(project)
    if plans is None:
        if master:raise ContextError("pinned launch master has no plans root")
        return project
    plans=Path(plans).resolve()
    pinned=None
    if master:
        pinned=(plans/Path(master)).resolve(strict=True)
        if pinned.parent!=plans or not pinned.name.startswith('MASTER-') or pinned.suffix!='.md':
            raise ContextError('launch master is outside plans root')
    masters=sorted(plans.glob('MASTER-*.md'))
    if not masters and pinned is None:return project
    chosen,_=pick_active_master(masters,json_mode=True,pinned_master=pinned)
    result=resolve_work_tree(plans,project,pinned_master=chosen)
    if result['error']:raise ContextError(result['error'])
    return Path(result['path']).resolve(strict=True) if result['path'] else project


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--master')
    args=parser.parse_args()
    print(resolve(args.project,master=args.master))


if __name__=='__main__':main()
