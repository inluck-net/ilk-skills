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


def profile(context: dict, *, temporary: Path, private_home: Path=None, diagnostic_log: Path=None, shell=False) -> str:
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
    # Git commands may read metadata, but workers may not mutate it.
    protected.extend([root/'.ilk-remote-type',root/'.ilk/findings.md'])
    protected.append(root/'.git')
    common=subprocess.run(['git','-C',str(root),'rev-parse','--git-common-dir'],capture_output=True,text=True)
    if common.returncode==0:
        raw=Path(common.stdout.strip());protected.append(raw.resolve() if raw.is_absolute() else (root/raw).resolve())
    rules=['(version 1)','(allow default)','(deny file-write*)',
           _rule('allow','file-write*',root),_rule('allow','file-write*',temporary),
           # CLI shell setup and ordinary redirection need the null device;
           # this grants no writes to filesystem/control-plane artifacts.
           '(allow file-write-data (literal "/dev/null"))','(deny signal)',
           # A test/tool may terminate its own inherited sandbox children;
           # independent driver/peer processes remain outside this target.
           '(allow signal (target same-sandbox))']
    if shell:
        rules.append('(deny network*)')
        if context.get('private_worker_home'):
            rules.append(_rule('deny','file-read*',Path(context['private_worker_home']).resolve()))
        if context.get('worker_inputs_dir'):
            rules.append(_rule('deny','file-read*',Path(context['worker_inputs_dir']).resolve()/'settings'))
    elif private_home:
        home=Path(private_home).resolve(strict=True)
        if _inside(home,root) or any(_inside(home,path) or _inside(path,home) for path in protected
                                  if path not in {Path(context['data_root']).resolve(),Path(context['runtime_dir']).resolve()}):
            raise ContextError('private home overlaps execution/control inputs')
        rules.append(_rule('allow','file-write*',home))
        protected.extend(home/name for name in ('settings.json','hooks','skills','commands'))
    if diagnostic_log and not shell:
        log=Path(diagnostic_log).resolve()
        rules.append(f'(allow file-write* (literal {json.dumps(str(log))}))')
    # Default denial already protects broad roots outside the explicit allowed
    # execution/home directories; retain exact nested control-plane exclusions.
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
