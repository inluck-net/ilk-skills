#!/usr/bin/env python3
"""Reconcile selfmod lifecycle from the worktree and its existing defer marker."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def _git(root,*args):
    from iteration_context import read_context
    from run_registry import require_driver
    from driver_step import _deadline
    from iteration_clock import monotonic
    context=read_context();require_driver(context)
    deadline=_deadline.get() or (context or {}).get('iteration_deadline_monotonic')
    if deadline is None:
        return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
    from bounded_run import run
    from iteration_context import ContextError
    remaining=deadline-monotonic()
    if remaining<=0:raise ContextError('selfmod state reached existing iteration deadline')
    code,out,err,timed_out=run(['git','-C',str(root),*args],cwd=root,timeout=remaining)
    if timed_out:raise ContextError('selfmod state Git reached existing iteration deadline')
    return subprocess.CompletedProcess(args,code,out,err)


def _value(root,*args):
    result=_git(root,*args)
    if result.returncode:raise ValueError('cannot verify selfmod Git identity: '+result.stderr[-1000:])
    return result.stdout.strip()


def common_dir(root: Path) -> Path:
    value=_value(root,'rev-parse','--git-common-dir')
    return (root/value).resolve()


def reconcile(clone: Path, worktree: Path, state_file: Path, *, landed_head=None) -> dict:
    from iteration_context import read_context
    from run_registry import require_driver
    from plan_state import _lock
    context=read_context();require_driver(context)
    from driver_step import _deadline
    deadline=_deadline.get() or (context or {}).get('iteration_deadline_monotonic')
    state_file.parent.mkdir(parents=True,exist_ok=True)
    with _lock(state_file.with_name(state_file.name+'.lock'),deadline=deadline,guard=lambda:require_driver(context)):
        return _reconcile(clone,worktree,state_file,landed_head=landed_head)


def _reconcile(clone: Path, worktree: Path, state_file: Path, *, landed_head=None) -> dict:
    from iteration_context import read_context
    from run_registry import require_driver
    from plan_state import _sync_parent
    require_driver(read_context())
    clone=clone.resolve();worktree=worktree.resolve()
    try:
        previous=json.loads(state_file.read_text())
    except FileNotFoundError:
        previous={'state':'NONE'}
    if previous.get('state') not in {'NONE','ISOLATED','MERGE_DEFERRED','MERGED'}:
        raise ValueError('unrecognized persisted selfmod state')
    if previous.get('clone') and previous['clone']!=str(clone):
        raise ValueError('persisted selfmod state belongs to another clone')
    if previous.get('worktree') and previous['worktree']!=str(worktree):
        raise ValueError('persisted selfmod state belongs to another worktree')
    worktree_identity=None
    if worktree.exists():
        # A recreated worktree at the same path/commit is new isolation.
        # Retain MERGED only for the actual retained Git indirection inode.
        metadata=(worktree/'.git').stat()
        worktree_identity=[metadata.st_dev,metadata.st_ino,metadata.st_ctime_ns]
        if worktree==clone or common_dir(worktree)!=common_dir(clone):
            raise ValueError('selfmod worktree does not belong to the clone')
        state='MERGE_DEFERRED' if (worktree/'.ilk-merge-deferred').exists() else 'ISOLATED'
        worktree_head=_value(worktree,'rev-parse','HEAD')
        dirty=bool(_value(worktree,'status','--porcelain'))
        if landed_head is not None:
            clone_head=_value(clone,'rev-parse','HEAD')
            if landed_head!=worktree_head or landed_head!=clone_head or dirty or _value(clone,'status','--porcelain'):
                raise ValueError('landed state differs from exact clone/worktree commit')
            state='MERGED'
        elif (previous['state']=='MERGED' and previous.get('worktree_head')==worktree_head
              and previous.get('worktree_identity')==worktree_identity and not dirty):
            proof=_git(clone,'merge-base','--is-ancestor',worktree_head,'HEAD')
            if proof.returncode==0:state='MERGED'
            elif proof.returncode!=1:raise ValueError('cannot verify retained landed worktree state')

    else:
        worktree_head=previous.get('worktree_head')
        state='NONE'
        if previous['state']!='NONE':
            if not worktree_head:
                raise ValueError('removed isolation has no persisted commit identity')
            proof=_git(clone,'merge-base','--is-ancestor',worktree_head,'HEAD')
            if proof.returncode:
                raise ValueError('isolated worktree disappeared before its commits landed')
            state='MERGED'
    current=dict(schema_version=1,clone=str(clone),worktree=str(worktree),state=state,
                 previous_state=previous['state'],worktree_head=worktree_head,
                 worktree_identity=worktree_identity)
    state_file.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=state_file.parent,delete=False) as f:
        tmp=Path(f.name);json.dump(current,f);f.write('\n');f.flush();os.fsync(f.fileno())
    try:
        tmp.chmod(0o600)
        require_driver(read_context())
        os.replace(tmp,state_file);_sync_parent(state_file)
    finally:tmp.unlink(missing_ok=True)
    return current


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--clone',type=Path,required=True)
    p.add_argument('--worktree',type=Path,required=True)
    p.add_argument('--state-file',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(reconcile(a.clone,a.worktree,a.state_file)))
if __name__=='__main__':main()
