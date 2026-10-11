#!/usr/bin/env python3
"""Observe frozen worker write scope; native enforcement is a later opt-in.

Not a shell sandbox: Bash classification is an indicator only. No production
registration is added until run inputs are pinned and consumer observations
are reviewed. The log contains categories/hashes, never raw shell commands.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time

FILE_WRITES={'Write','Edit','MultiEdit','NotebookEdit'}
GIT_MUTATIONS={'commit','push','reset','rebase','merge','cherry-pick','am','apply','stash','checkout','switch','restore','add','update-ref'}


def inside(path,root):
    try: path.relative_to(root);return True
    except ValueError: return False


def classify(event,context):
    tool=event.get('tool_name');payload=event.get('tool_input') or {}
    row={'tool':tool,'write_observed':False,'scope':'not-a-native-write','shell_classification':'not-shell'}
    root=Path(context['exec_tree']).resolve()
    if tool in FILE_WRITES:
        row['write_observed']=True
        raw=payload.get('file_path') or payload.get('notebook_path')
        if not isinstance(raw,str) or not raw:
            row['scope']='unknown-target';return row
        path=Path(os.path.expanduser(raw))
        if not path.is_absolute(): path=Path(event.get('cwd') or str(root))/path
        path=path.resolve()
        protected=[('plans',Path(context['plans_dir']).resolve()),
                   ('data',Path(context['data_root']).resolve()),
                   ('runtime',Path(context['runtime_dir']).resolve()),
                   ('git-metadata',root/'.git')]
        row['scope']='worktree' if inside(path,root) else 'outside-worktree'
        for name,protected_root in protected:
            # Selfmod worktrees can live beneath the data/runtime root. Their
            # explicit execution scope is an exception to the broad parent.
            if name in {'data','runtime'} and inside(root,protected_root):
                continue
            if inside(path,protected_root): row['scope']=name;break
        row['target_sha256']=hashlib.sha256(str(path).encode()).hexdigest()
    elif tool=='Bash':
        command=payload.get('command','')
        if not isinstance(command,str): row['shell_classification']='invalid';return row
        row['command_sha256']=hashlib.sha256(command.encode()).hexdigest()
        try: tokens=shlex.split(command)
        except ValueError: row['shell_classification']='unparsed';return row
        # Hints are deliberately not a verdict about arbitrary shell code.
        row['shell_classification']='unclassified'
        if any(Path(token).name=='git' for token in tokens) and GIT_MUTATIONS.intersection(tokens):
            row['shell_classification']='git-mutation-hint'
        elif any(token in {'>','>>','tee','cp','mv','rm','touch','mkdir','ln'} for token in tokens):
            row['shell_classification']='filesystem-mutation-hint'
    return row


def record(path,row):
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
        stream.write(json.dumps(row,separators=(',',':'))+'\n')
        stream.flush()


def main():
    mode=os.environ.get('ILK_WRITE_SCOPE_MODE','observe')
    raw=os.environ.get('ILK_ITER_CONTEXT')
    if not raw or not os.environ.get('ILK_WORKER_SESSION','').strip(): return 0
    try:
        if mode not in {'observe','enforce-native'}: raise ValueError('unknown observation mode')
        context=json.loads(Path(raw).read_text(encoding='utf-8-sig'))
        sys.path.insert(0,str(Path(context['code_root'])/'skills/ilk-loop/scripts'))
        from iteration_context import read_context
        context=read_context(raw)
        event=json.load(sys.stdin)
        row=classify(event,context)
        row.update(run_id=context['run_id'],iteration=context['iteration'],mode=mode,
                   monotonic=time.monotonic(),diagnostic_only=True)
        record(Path(raw).parent/f"write-scope-{context['iteration']}.jsonl",row)
        if mode=='enforce-native' and row['write_observed'] and row['scope']!='worktree':
            print('Worker native write is outside its code scope: '+row['scope'],file=sys.stderr)
            return 2
        return 0
    except Exception as exc:
        # Observe-only cannot alter the current worker protocol.
        print('Write-scope observation unavailable: '+type(exc).__name__,file=sys.stderr)
        return 2 if mode!='observe' else 0


if __name__=='__main__':raise SystemExit(main())
