#!/usr/bin/env python3
"""Build a Mac write boundary from frozen context; shell children also deny network.

Candidate boundary API. It is not enabled in production. Other hosts require
an explicit adapter; lack of sandbox support must not silently weaken scope.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
import subprocess
import sys
from iteration_context import ContextError


def _inside(path,root):
    try:path.relative_to(root);return True
    except ValueError:return False


def _rule(verb,operation,path):
    return f'({verb} {operation} (subpath {json.dumps(str(path))}))'


def profile(context: dict, *, temporary: Path, private_home: Path=None, diagnostic_log: Path=None, shell=False, deadline=None) -> str:
    if sys.platform!='darwin': raise ContextError('worker sandbox host adapter unavailable')
    root=Path(context['exec_tree']).resolve(strict=True)
    temporary=Path(temporary).resolve(strict=True)
    broad=[(key,Path(context[key]).resolve()) for key in ('plans_dir','runtime_dir','data_root','code_root')]
    protected=[]
    for key,path in broad:
        if _inside(root,path):
            if key in {'data_root','runtime_dir'}: continue
            raise ContextError('execution tree overlaps plans or pinned code')
        protected.append(path)
    if temporary==root or any(_inside(temporary,path) or _inside(path,temporary) for path in protected+[root]):
        raise ContextError('private temporary directory overlaps execution/control inputs')
    if context['mode']=='selfmod':protected.append(Path(context['clone']).resolve())
    kernel=context.get('kernel_list') or {}
    kernel_paths=[]
    if kernel.get('loop_kernel_policy')=='owner-only':
        for entry in kernel.get('rules',[])+kernel.get('kernel',[]):
            relative=Path(entry['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ContextError('kernel path escapes execution root')
            kernel_paths.append(root/relative)
        protected.extend(kernel_paths)
    # Frozen instruction aliases and resolved targets are control inputs.
    for row in context.get('instruction_input_inventory',[]):
        for key in ('path','resolved_path'):
            if row.get(key):
                path=Path(row[key]);protected.append(path)
                if _inside(path,root):kernel_paths.append(path)
    # Git commands may read metadata, but workers may not mutate it.
    protected.extend([root/'.ilk-remote-type',root/'.ilk/findings.md',root/'.ilk-merge-deferred'])
    protected.append(root/'.git')
    git_limits={}
    if deadline is None:
        from run_registry import driver_authority
        deadline=(driver_authority(context) or {}).get('iteration_deadline_monotonic')
    if deadline is not None:
        import math
        from iteration_clock import monotonic
        if isinstance(deadline,bool) or not math.isfinite(deadline) or deadline<=monotonic():
            raise ContextError('sandbox metadata deadline unavailable')
        git_limits['timeout']=deadline-monotonic()
    common=subprocess.run(['git','-C',str(root),'rev-parse','--git-common-dir'],capture_output=True,text=True,**git_limits)
    if common.returncode==0:
        raw=Path(common.stdout.strip());protected.append(raw.resolve() if raw.is_absolute() else (root/raw).resolve())
    rules=['(version 1)','(allow default)','(deny file-write*)',
           _rule('allow','file-write*',root),_rule('allow','file-write*',temporary),
           # Moving the entire root into writable scratch would move its
           # protected metadata/instructions away from their frozen paths.
           f'(deny file-write-unlink (literal {json.dumps(str(root))}))',
           f'(deny file-write-create (literal {json.dumps(str(root))}))',
           # CLI shell setup and ordinary redirection need the null device;
           # this grants no writes to filesystem/control-plane artifacts.
           '(allow file-write-data (literal "/dev/null"))','(deny signal)',
           # A test/tool may terminate its own inherited sandbox children;
           # independent driver/peer processes remain outside this target.
           '(allow signal (target same-sandbox))']
    if shell:
        rules.append('(deny network*)')
        if context.get('private_worker_home'):
            home=Path(context['private_worker_home']).resolve(strict=True)
            rules.append(_rule('deny','file-read*',home))
            for row in context.get('global_instruction_inventory',[]):
                path=Path(row['path'])
                try:relative=path.relative_to(home)
                except ValueError:raise ContextError('global instruction escapes private home')
                permitted=str(relative) in {'CLAUDE.md','CLAUDE.local.md'} or (
                    relative.parts[0]=='rules' and len(relative.parts)>1 and relative.suffix=='.md')
                if context.get('global_instruction_inventory_protocol')==2 and row.get('relative_import') is True:
                    permitted=(path.suffix=='.md' and relative.parts[0] not in {'settings.json','hooks','skills','commands','settings'})
                if not permitted or path.resolve(strict=True)!=path or not path.is_file():
                    raise ContextError('global instruction adapter unavailable')
                import hashlib
                if hashlib.sha256(path.read_bytes()).hexdigest()!=row.get('sha256'):
                    raise ContextError('global instruction changed')
                rules.append(f'(allow file-read* (literal {json.dumps(str(path))}))')
                for parent in path.parents:
                    if not _inside(parent,home):break
                    rules.append(f'(allow file-read-metadata (literal {json.dumps(str(parent))}))')
        if context.get('worker_inputs_dir'):
            rules.append(_rule('deny','file-read*',Path(context['worker_inputs_dir']).resolve()/'settings'))
    elif private_home:
        home=Path(private_home).resolve(strict=True)
        if _inside(home,root) or any(_inside(home,path) or _inside(path,home) for path in protected
                                  if path not in {Path(context['data_root']).resolve(),Path(context['runtime_dir']).resolve()}
                                  and str(path) not in {row['path'] for row in context.get('global_instruction_inventory',[])}):
            raise ContextError('private home overlaps execution/control inputs')
        rules.append(_rule('allow','file-write*',home))
        protected.extend(home/name for name in ('settings.json','hooks','skills','commands','CLAUDE.md','CLAUDE.local.md','rules'))
    if diagnostic_log and not shell:
        log=Path(diagnostic_log).resolve()
        rules.append(f'(allow file-write* (literal {json.dumps(str(log))}))')
    # Default denial already protects broad roots outside the explicit allowed
    # execution/home directories; retain exact nested control-plane exclusions.
    # Protect existing and newly created named instructions, including nested
    # directories. A copied input is not permission to rewrite its contract.
    rules.append('(deny file-write* (require-all (subpath '+json.dumps(str(root))+') '
                 '(regex "(^|/)(AGENTS[.]md|CLAUDE([.]local)?[.]md)$")))')
    rules.append('(deny file-write* (require-all (subpath '+json.dumps(str(root))+') '
                 '(regex "(^|/)[.]claude/rules(/|$)")))')
    rules.extend(_rule('deny','file-write*',path) for path in protected
                 if any(_inside(path,allowed) for allowed in [root,temporary]+([Path(private_home).resolve()] if private_home and not shell else [])))
    for path in kernel_paths:
        # Renaming a writable ancestor must not move protected files out of
        # their frozen pathname. Ordinary sibling files remain writable.
        for ancestor in path.parents:
            if not _inside(ancestor,root):break
            rules.append(f'(deny file-write-unlink (literal {json.dumps(str(ancestor))}))')
    if kernel.get('loop_kernel_policy')=='owner-only':
        for entry in kernel.get('kernel_basenames',[]):
            # Include a basename directly beneath the execution root too.
            pattern='^'+re.escape(str(root))+'/(.*/)?'+re.escape(entry['name'])+'$'
            rules.append(f'(deny file-write* (regex {json.dumps(pattern)}))')
    return '\n'.join(rules)+'\n'
