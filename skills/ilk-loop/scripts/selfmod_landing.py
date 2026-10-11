#!/usr/bin/env python3
"""Land one exact accepted selfmod transaction; historical unmerged bases refuse.

No daemon bounce, worktree removal, worker dispatch or gate outcome reuse.
Git operations and lock readiness use the caller's existing deadline. The
worktree stays available through integrity/close projection and owner recovery.
"""
from __future__ import annotations
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import driver_step
from iteration_context import ContextError
from iteration_clock import monotonic
from run_registry import require_driver,host_blockers


@contextmanager
def _merge_lock(context,deadline):
    if os.name!='posix':raise ContextError('selfmod landing lock host adapter unavailable')
    import fcntl
    path=Path(context['runtime_dir'])/'selfmod-merge.lock'
    require_driver(context);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        while True:
            require_driver(context)
            if monotonic()>=deadline:raise ContextError('selfmod landing lock reached existing deadline')
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                # 20ms readiness is a responsiveness judgment reused from the
                # owned cleanup adapter, not a renewed timeout. Sustained CPU
                # or slow valid lock release would falsify this interval.
                threading.Event().wait(min(.02,max(deadline-monotonic(),0)))
        try:yield
        finally:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def land(context,accepted,*,deadline):
    token=driver_step._deadline.set(deadline)
    owner_token=driver_step._operation_context.set(context)
    try:
        require_driver(context);driver_step.remaining()
        if context.get('mode')!='selfmod':raise ContextError('exact selfmod context required')
        clone=Path(context['clone']).resolve();root=Path(context['exec_tree']).resolve()
        if clone==root:raise ContextError('selfmod landing cannot use live clone as execution tree')
        frozen=context.get('clone_base_commit');ref=context.get('clone_ref')
        if not frozen or not ref or context.get('clone_entry_dirty') is not False:
            raise ContextError('clean frozen clone base/branch required for landing')
        if frozen!=context['base_commit']:
            raise ContextError('historical unmerged selfmod base adapter unavailable')
        git=driver_step.git
        def common(tree):
            raw=Path(git(tree,'rev-parse','--git-common-dir'))
            return raw.resolve() if raw.is_absolute() else (tree/raw).resolve()
        if common(clone)!=common(root):raise ContextError('selfmod trees do not share the frozen Git store')
        if accepted.get('accepted') is not True:raise ContextError('selfmod landing needs an accepted transaction')
        verified=driver_step.recover_acceptance(context,deadline=deadline)
        if not verified or any(accepted.get(key)!=verified.get(key) for key in ('tree','head_commit','commits')):
            raise ContextError('selfmod landing differs from durable accepted receipt')
        target=driver_step._transaction_head(context,verified)
        if driver_step.working_tree(root)!=accepted['tree']:
            raise ContextError('accepted selfmod worktree changed before landing')
        with _merge_lock(context,deadline):
            identity=require_driver(context)
            blockers=host_blockers(clone=clone,exclude_group=identity['pgid'] if identity else None,
                                   data_root=context['data_root'])
            if blockers:raise ContextError('selfmod landing blocked by live or unverified toolkit inputs')
            if git(clone,'symbolic-ref','-q','HEAD')!=ref:
                raise ContextError('clone branch changed before selfmod landing')
            before=git(clone,'rev-parse','HEAD')
            if before not in {frozen,accepted['head_commit'],target}:
                raise ContextError('clone moved outside this exact accepted transaction')
            if git(clone,'status','--porcelain'):
                raise ContextError('clone became dirty before selfmod landing')
            if driver_step._transaction_head(context,accepted)!=target:
                raise ContextError('accepted transaction moved while waiting for landing')
            if before!=target:git(clone,'merge','--ff-only','--no-edit',target)
            require_driver(context)
            if (git(clone,'symbolic-ref','-q','HEAD')!=ref or git(clone,'rev-parse','HEAD')!=target
                    or git(clone,'rev-parse','HEAD^{tree}')!=accepted['tree']
                    or driver_step.working_tree(clone)!=accepted['tree']):
                raise ContextError('clone does not contain exact clean accepted landing')
            from run_registry import as_driver,driver_authority
            from selfmod_state import reconcile
            with as_driver(driver_authority(context)):
                reconcile(clone,root,Path(context['runtime_dir'])/'launcher/selfmod-state.json',landed_head=target)
            marker=root/'.ilk-merge-deferred'
            if marker.exists():
                require_driver(context);marker.unlink()
                from plan_state import _sync_parent
                _sync_parent(marker)
            return dict(outcome='merged',clone=str(clone),head_before=before,head_after=target,
                        accepted_tree=accepted['tree'],execution_worktree_retained=True)
    finally:
        driver_step._operation_context.reset(owner_token);driver_step._deadline.reset(token)


def main():
    import argparse,json
    from iteration_context import read_context
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--accepted',type=Path,required=True)
    args=parser.parse_args();context=read_context(args.context)
    require_driver(context)
    result=land(context,json.loads(args.accepted.read_text()),deadline=context['iteration_deadline_monotonic'])
    print(json.dumps(result))


if __name__=='__main__':main()
