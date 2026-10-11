#!/usr/bin/env python3
"""Persist resolved iteration inputs, without selecting plans or proving gates."""
from __future__ import annotations
import argparse
import hashlib
import re
import json
import os
from pathlib import Path
import subprocess
import tempfile


class ContextError(ValueError):
    pass


def read_context(path: Path | None = None) -> dict | None:
    raw=path or os.environ.get('ILK_ITER_CONTEXT')
    if not raw: return None
    try:
        data=json.loads(Path(raw).read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict) or data.get('schema_version')!=1:
            raise ValueError('unsupported context schema')
        for name in ('clone','exec_tree','plans_dir','runtime_dir','data_root','code_root'):
            if not isinstance(data.get(name),str) or not Path(data[name]).is_absolute():
                raise ValueError(f'invalid context {name}')
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise ContextError(f'iteration context invalid: {exc}') from exc


def root_for_plan(context: dict, subplan: Path) -> Path:
    from plan_status import parse_frontmatter
    subplan=subplan.resolve()
    if subplan.parent!=Path(context['plans_dir']) or subplan.name not in context.get('plans',{}):
        raise ContextError('plan is outside the frozen iteration registry')
    member=context['plans'][subplan.name].get('repo')
    if member:
        key=str((Path(context['clone'])/member).resolve())
        if key not in context['observe_tree']:
            raise ContextError('plan repo is outside frozen observation map')
        return Path(context['observe_tree'][key])
    return Path(context['exec_tree'])


def git_value(root: Path, name: str) -> str | None:
    try:
        return subprocess.check_output(['git','-C',str(root),'rev-parse',name],text=True,stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        return None # Installed release roots and meta-project containers lack .git.


def plan_snapshot(path: Path) -> dict:
    from plan_status import parse_frontmatter
    from plan_slug import strip_date_prefix
    try:
        text=path.read_text(encoding='utf-8-sig')
    except OSError as exc:
        return {'path':str(path),'error':type(exc).__name__}
    fm=parse_frontmatter(text)
    immutable=re.split(r'^## Findings\b',text,maxsplit=1,flags=re.MULTILINE)[0]
    if immutable.startswith('---'):
        parts=immutable.split('---',2)
        if len(parts)==3:
            parts[1]=re.sub(r'^(?:current_step|status|last_updated):[^\n]*\n?', '',parts[1],flags=re.MULTILINE)
            immutable='---'.join(parts)
    return dict(path=str(path),slug=strip_date_prefix(path.stem),
                status=fm.get('status'),step=fm.get('current_step'),repo=fm.get('repo'),estimated_steps=fm.get('estimated_steps'),
                fingerprint=hashlib.sha256(immutable.encode()).hexdigest())


def plan_status_from_context(context: dict) -> dict:
    subplans=[]
    for name,row in context.get('plans',{}).items():
        current=plan_snapshot(Path(row['path']))
        subplans.append(dict(fname=name,slug=row.get('slug'),status=current.get('status'),
                             current_step=current.get('step'),estimated_steps=current.get('estimated_steps')))
    return {'master':(context.get('dispatch') or {}).get('master_path'),'subplans':subplans}


def capture_execution(context: dict) -> dict:
    root=Path(context['exec_tree'])
    head=git_value(root,'HEAD')
    result={'head_commit':head,'head_tree':git_value(root,'HEAD^{tree}'),
            'commits':None,'step_to':{name:plan_snapshot(Path(row['path']))
                                    for name,row in context.get('plans',{}).items()}}
    base=context.get('base_commit')
    if not base or not head:
        result['capture_error']='execution tree has no available git identity'
        return result
    ancestor=subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',base,head],capture_output=True)
    if ancestor.returncode:
        result['capture_error']='iteration base is not an ancestor of final execution HEAD'
        return result
    result['commits']=subprocess.check_output(['git','-C',str(root),'rev-list','--reverse',f'{base}..{head}'],text=True).splitlines()
    return result


def resolve_context(*, clone, exec_tree, observe_tree, code_root, plans_dir,
                    runtime_dir, data_root, dispatch, run_id, iteration, mode):
    if mode not in {'none','selfmod','work_tree'}:
        raise ValueError('unknown execution mode')
    def directory(raw):
        p=Path(raw).resolve(strict=True)
        if not p.is_dir(): raise ValueError(f'not a directory: {p}')
        return str(p)
    clone=directory(clone);exec_tree=directory(exec_tree)
    code_root=directory(code_root);plans_dir=directory(plans_dir)
    observed={directory(k):directory(v) for k,v in observe_tree.items()}
    if clone not in observed:
        observed[clone]=exec_tree
    if observed[clone]!=exec_tree:
        raise ValueError('clone observation differs from execution tree')
    if mode=='selfmod' and clone==exec_tree:
        raise ValueError('selfmod worker would run in live clone')
    if dispatch:
        selected=Path(dispatch['subplan_path']).resolve(strict=True)
        if selected.parent!=Path(plans_dir):
            raise ValueError('dispatch belongs to another plans root')
        master=dispatch.get('master_path')
        if master and Path(master).resolve(strict=True).parent!=Path(plans_dir):
            raise ValueError('master belongs to another plans root')
    snapshots={}
    master_snapshot=None
    if dispatch:
        from plan_status import extract_subplan_files
        master_path=dispatch.get('master_path')
        if master_path:
            mp=Path(master_path)
            master_snapshot=plan_snapshot(mp)
            for name in extract_subplan_files(mp.read_text(encoding='utf-8-sig')):
                path=(Path(plans_dir)/name).resolve()
                if path.parent!=Path(plans_dir): raise ValueError('registered path escapes plans root')
                snapshots[name]=plan_snapshot(path)
        else:
            snapshots[selected.name]=plan_snapshot(selected)
    runtime=Path(runtime_dir).resolve()
    recovery_identity={'master':(dispatch or {}).get('master_path'),
                       'base':(dispatch or {}).get('batch_base_sha'),
                       'master_fingerprint':(master_snapshot or {}).get('fingerprint'),
                       'plan_fingerprint':snapshots.get(Path((dispatch or {}).get('subplan_path','')).name,{}).get('fingerprint')}
    recovery_key=hashlib.sha256(json.dumps(recovery_identity,sort_keys=True).encode()).hexdigest()
    return dict(schema_version=1,run_id=run_id,iteration=int(iteration),
                clone=clone,exec_tree=exec_tree,observe_tree=observed,mode=mode,
                plans_dir=plans_dir,runtime_dir=str(runtime),
                ledger_path=str(runtime/'launcher/ship-proof.jsonl'),
                registry_path=None, # D4 has no canonical registry yet.
                recovery_dir=str(runtime/'launcher/recovery'/recovery_key),
                data_root=str(Path(data_root).resolve()),code_root=code_root,
                toolkit_sha=git_value(Path(code_root),'HEAD'),
                base_commit=git_value(Path(exec_tree),'HEAD'),
                base_tree=git_value(Path(exec_tree),'HEAD^{tree}'),
                dispatch=dispatch or None, plans=snapshots, master_snapshot=master_snapshot)


def write_context(path: Path, context: dict):
    # Never silently replace an iteration's previously frozen identity.
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False,encoding='utf-8') as f:
        tmp=Path(f.name)
        json.dump(context,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
    try:
        tmp.chmod(0o400)
        os.link(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sources=p.add_mutually_exclusive_group(required=True)
    sources.add_argument('--input',type=Path)
    sources.add_argument('--capture',type=Path)
    p.add_argument('--out',type=Path)
    a=p.parse_args()
    if a.capture:
        print(json.dumps(capture_execution(json.loads(a.capture.read_text(encoding='utf-8-sig')))))
    else:
        if not a.out: p.error('--out required with --input')
        write_context(a.out,resolve_context(**json.loads(a.input.read_text(encoding='utf-8-sig'))))

if __name__=='__main__': main()
