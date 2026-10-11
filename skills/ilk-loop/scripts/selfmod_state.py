#!/usr/bin/env python3
"""Reconcile selfmod lifecycle from the worktree and its existing defer marker."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def common_dir(root: Path) -> Path:
    value=subprocess.check_output(['git','-C',str(root),'rev-parse','--git-common-dir'],text=True,stderr=subprocess.DEVNULL).strip()
    return (root/value).resolve()


def reconcile(clone: Path, worktree: Path, state_file: Path) -> dict:
    from iteration_context import read_context
    from run_registry import require_driver
    from plan_state import _lock
    require_driver(read_context())
    state_file.parent.mkdir(parents=True,exist_ok=True)
    with _lock(state_file.with_name(state_file.name+'.lock')):
        return _reconcile(clone,worktree,state_file)


def _reconcile(clone: Path, worktree: Path, state_file: Path) -> dict:
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
    if worktree.exists():
        if worktree==clone or common_dir(worktree)!=common_dir(clone):
            raise ValueError('selfmod worktree does not belong to the clone')
        state='MERGE_DEFERRED' if (worktree/'.ilk-merge-deferred').exists() else 'ISOLATED'
        worktree_head=subprocess.check_output(['git','-C',str(worktree),'rev-parse','HEAD'],text=True).strip()
    else:
        worktree_head=previous.get('worktree_head')
        state='NONE'
        if previous['state']!='NONE':
            if not worktree_head:
                raise ValueError('removed isolation has no persisted commit identity')
            proof=subprocess.run(['git','-C',str(clone),'merge-base','--is-ancestor',worktree_head,'HEAD'],capture_output=True)
            if proof.returncode:
                raise ValueError('isolated worktree disappeared before its commits landed')
            state='MERGED'
    current=dict(schema_version=1,clone=str(clone),worktree=str(worktree),state=state,
                 previous_state=previous['state'],worktree_head=worktree_head)
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
