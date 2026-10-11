#!/usr/bin/env python3
"""The pinned Bash entry: subprocess writes are scoped and network is denied."""
from __future__ import annotations
import argparse
import json
import socket
from pathlib import Path
import shlex
import sys
from iteration_context import read_context,ContextError


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
    if not context or context.get('worker_sandbox_adapter')!='macos-brokered-shell':
        parser.error('explicit frozen brokered shell boundary required')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.connect(context['shell_broker_path'])
        request={'op':'exec','nonce':context['shell_broker_nonce'],'command':args.command}
        connection.sendall(json.dumps(request).encode()+b'\n')
        with connection.makefile('rb') as stream:result=json.loads(stream.readline())
    if result.get('stdout'):sys.stdout.write(result['stdout'])
    if result.get('stderr'):sys.stderr.write(result['stderr'])
    if result.get('error'):print(result['error'],file=sys.stderr)
    return result['exit_code']


if __name__=='__main__':raise SystemExit(main())
