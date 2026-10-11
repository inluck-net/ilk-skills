#!/usr/bin/env python3
"""Register a group before exec: no parent/fast-child registration race."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
from run_registry import driver_authority,require_driver,register_child


def wrap_command(argv,context,*,worker=False):
    owner=driver_authority(context)
    if not owner or not owner.get('run_registry_dir'):return argv
    require_driver(context)
    authority={key:owner[key] for key in ('run_registry_dir','run_id','runner_owner_pid')}
    authority['exec_worker']=worker
    return [sys.executable,str(Path(__file__).resolve()),json.dumps(authority,separators=(',',':')),*argv]


def main():
    owner=json.loads(sys.argv[1]);command=sys.argv[2:]
    if not command:raise ValueError('owned command missing')
    require_driver(owner)
    register_child(Path(owner['run_registry_dir']),owner['run_id'],pid=os.getpid(),owner_pid=owner['runner_owner_pid'])
    if owner.get('exec_worker'):os.environ['ILK_WORKER_SESSION']='1'
    os.execvpe(command[0],command,os.environ)


if __name__=='__main__':main()
