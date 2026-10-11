#!/usr/bin/env python3
"""The pinned Bash entry: subprocess writes are scoped and network is denied."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from iteration_context import read_context,ContextError
from worker_sandbox import profile


def canonical_command(context: dict, context_path: Path, command: str) -> str:
    interpreter=context.get('worker_shell_python')
    if not isinstance(interpreter,str) or not Path(interpreter).is_absolute():
        raise ContextError('pinned shell interpreter absent')
    wrapper=Path(context['code_root'])/'skills/ilk-loop/scripts/worker_shell.py'
    return shlex.join([interpreter,str(wrapper),'--context',str(context_path.resolve()),'--command',command])


def admitted_command(raw: str, context: dict, context_path: Path) -> bool:
    try:tokens=shlex.split(raw)
    except (ValueError,TypeError):return False
    if len(tokens)!=6 or tokens[2]!='--context' or tokens[4]!='--command':return False
    try:return raw==canonical_command(context,context_path,tokens[5])
    except ContextError:return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,required=True)
    parser.add_argument('--command',required=True)
    args=parser.parse_args();context=read_context(args.context)
    if not context or context.get('worker_sandbox_adapter')!='macos-two-layer':
        parser.error('explicit frozen shell boundary required')
    directory=context.get('worker_tmp_dir')
    if not isinstance(directory,str) or not Path(directory).is_absolute():
        parser.error('private frozen temporary directory required')
    temporary=Path(directory).resolve(strict=True)
    root=Path(context['exec_tree']).resolve()
    if not Path.cwd().resolve().is_relative_to(root):
        parser.error('shell cwd is outside execution tree')
    policy=profile(context,temporary=temporary,shell=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=temporary,delete=False) as stream:
        policy_path=Path(stream.name);stream.write(policy)
    try:
        env={key:value for key,value in os.environ.items() if not key.startswith('ANTHROPIC_')}
        env['TMPDIR']=str(temporary)
        cp=subprocess.run(['/usr/bin/sandbox-exec','-f',str(policy_path),'/bin/bash','-c',args.command],env=env)
        return cp.returncode
    finally:policy_path.unlink(missing_ok=True)


if __name__=='__main__':raise SystemExit(main())
