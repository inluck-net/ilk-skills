#!/usr/bin/env python3
"""Additive run identity registry candidate; legacy artifacts stay untouched.

POSIX identity uses PID, process start and PGID together. Windows needs its
native adapter before activation. No argv scan and no unknown-process signals.
Launch expiry is caller-supplied, not a new runner default.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from plan_state import _lock


class RegistryError(RuntimeError):pass


def process_identity(pid):
    if os.name=='nt':raise RegistryError('Windows process identity adapter not built')
    if isinstance(pid,bool) or not isinstance(pid,int) or pid<=0:raise RegistryError('invalid process PID')
    cp=subprocess.run(['ps','-p',str(pid),'-o','pid=','-o','pgid=','-o','lstart='],capture_output=True,text=True)
    if cp.returncode:return None
    fields=cp.stdout.strip().split(None,2)
    if len(fields)!=3:return None
    return {'pid':int(fields[0]),'pgid':int(fields[1]),'lstart':fields[2]}


def _path(runtime,run_id):
    if not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',run_id):
        raise RegistryError('invalid run ID')
    return Path(runtime).resolve()/'runs'/(run_id+'.json')


def _owner():
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise RegistryError('worker cannot mutate run registry')


def _write(path,row):
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as stream:
        temporary=Path(stream.name);json.dump(row,stream);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    try:temporary.chmod(0o600);os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def read(path):
    try:row=json.loads(Path(path).read_text())
    except (OSError,ValueError) as exc:raise RegistryError('unreadable run registry') from exc
    required={'run_id','project_key','state','clone','exec_tree','toolkit_sha','pid','pgid','lstart'}
    if row.get('schema')!=1 or not required<=row.keys() or row['state'] not in {'launching','running'}:
        raise RegistryError('unknown registry schema/state')
    if row['state']=='launching':
        expiry=row.get('expires_at')
        if isinstance(expiry,bool) or not isinstance(expiry,(int,float)) or not math.isfinite(expiry) or expiry<=0:
            raise RegistryError('launch expiry required')
    else:
        if any(isinstance(row[key],bool) or not isinstance(row[key],int) or row[key]<=0 for key in ('pid','pgid')):
            raise RegistryError('running PID/PGID required')
        if not isinstance(row['lstart'],str) or not row['lstart']:raise RegistryError('process start required')
    if _path(Path(path).parent.parent,row['run_id'])!=Path(path).resolve():
        raise RegistryError('registry filename contradicts run identity')
    return row


def reserve(runtime,*,run_id,project_key,clone,exec_tree,toolkit_sha,expires_at,now=None):
    _owner();now=time.time() if now is None else now
    if isinstance(expires_at,bool) or not isinstance(expires_at,(int,float)) or not math.isfinite(expires_at) or expires_at<=now:
        raise RegistryError('future launch expiry required')
    path=_path(runtime,run_id);path.parent.mkdir(parents=True,exist_ok=True)
    row=dict(schema=1,run_id=run_id,project_key=project_key,state='launching',
             clone=str(Path(clone).resolve()),exec_tree=str(Path(exec_tree).resolve()),toolkit_sha=toolkit_sha,
             pid=None,pgid=None,lstart=None,expires_at=expires_at,created_at=now)
    with _lock(path.parent/'.registry.lock'):
        if path.exists():raise RegistryError('run identity already reserved')
        _write(path,row)
    return row


def bind_running(runtime,run_id,*,pid,probe=process_identity,now=None):
    _owner();path=_path(runtime,run_id);now=time.time() if now is None else now
    identity=probe(pid)
    if not identity or identity.get('pid')!=pid:raise RegistryError('runner identity unavailable')
    with _lock(path.parent/'.registry.lock'):
        row=read(path)
        if row['state']!='launching' or row['expires_at']<=now:raise RegistryError('launch reservation expired or already bound')
        row.update(identity,state='running',bound_at=now)
        _write(path,row)
    return row


def liveness(row,*,probe=process_identity,now=None):
    now=time.time() if now is None else now
    if row['state']=='launching':return 'launching' if row['expires_at']>now else 'expired'
    identity=probe(row['pid'])
    if identity is None:return 'dead'
    return 'running' if all(identity.get(key)==row[key] for key in ('pid','pgid','lstart')) else 'reused-or-moved'


def scan(runtime,*,probe=process_identity,now=None):
    result=[]
    for path in sorted((Path(runtime)/'runs').glob('*.json')):
        row=read(path);result.append(dict(row,liveness=liveness(row,probe=probe,now=now)))
    return result


def finish(runtime,run_id,*,identity):
    _owner();path=_path(runtime,run_id)
    with _lock(path.parent/'.registry.lock'):
        row=read(path)
        if row['state']!='running' or any(row[key]!=identity.get(key) for key in ('pid','pgid','lstart')):
            raise RegistryError('finish identity mismatch')
        path.unlink()


def signal_run(runtime,run_id,*,probe=process_identity,send=os.killpg):
    """Signal only a freshly verified recorded group. Caller controls waiting."""
    _owner();row=read(_path(runtime,run_id))
    if liveness(row,probe=probe)!='running':raise RegistryError('recorded process identity is no longer live')
    if row['pgid']==os.getpgrp():raise RegistryError('refusing caller process group')
    send(row['pgid'],signal.SIGTERM)
    return row
