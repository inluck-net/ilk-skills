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
import sys
import tempfile
import time
from contextvars import ContextVar
from contextlib import contextmanager
from plan_state import _lock,_sync_parent


class RegistryError(RuntimeError):pass

_active_driver=ContextVar('ilk_active_driver',default=None)


def driver_authority(context):
    """An explicit current driver may finish an older frozen transaction."""
    return _active_driver.get() or context


def require_driver(context):
    """Refuse control-plane mutation after the owning runner dies or changes.

    Supervised hand APIs have no runner roles; production dispatch preparation
    requires both roles before freezing. Partial roles never grant authority.
    """
    _owner()
    owner=driver_authority(context)
    if not owner:return None
    runtime=owner.get('run_registry_dir');pid=owner.get('runner_owner_pid')
    if runtime is None and pid is None:return None
    if runtime is None or pid is None:raise RegistryError('driver ownership roles incomplete')
    row=read(_path(runtime,owner['run_id']));observed=process_identity(pid)
    if row['state']!='running' or row['pid']!=pid or observed!={key:row[key] for key in ('pid','pgid','lstart')}:
        raise RegistryError('owning runner identity is no longer live')
    return observed


@contextmanager
def as_driver(context):
    token=_active_driver.set(context)
    try:
        require_driver(context)
        yield
    finally:_active_driver.reset(token)


def code_sha(root):
    root=Path(root).resolve()
    marker=root/'.ilk-release.json'
    inputs=root.parent/'inputs.json'
    if inputs.exists():
        try:
            pinned=json.loads(inputs.read_text())
            if Path(pinned['code_root']).resolve()!=root:raise RegistryError('private code root contradicts manifest')
            value=pinned['toolkit_sha']
        except (OSError,ValueError,KeyError):raise RegistryError('private code identity unavailable')
    elif marker.exists():
        try:value=json.loads(marker.read_text())['sha']
        except (OSError,ValueError,KeyError):raise RegistryError('release identity unavailable')
    else:
        cp=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True)
        value=cp.stdout.strip() if cp.returncode==0 else None
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{40,64}',value):raise RegistryError('toolkit commit identity unavailable')
    return value


def process_identity(pid):
    return _process_identity(pid,include_zombie=False)


def group_identity(pid):
    """An unreaped zombie still reserves PID/PGID/start for group cleanup.

    It grants no driver authority and is never considered a running worker.
    A reaped/missing/reused leader cannot establish ownership of descendants.
    """
    return _process_identity(pid,include_zombie=True)


def _process_identity(pid,*,include_zombie):
    if os.name=='nt':raise RegistryError('Windows process identity adapter not built')
    if isinstance(pid,bool) or not isinstance(pid,int) or pid<=0:raise RegistryError('invalid process PID')
    try:
        cp=subprocess.run(['ps','-p',str(pid),'-o','pid=','-o','pgid=','-o','stat=','-o','lstart='],capture_output=True,text=True,env=dict(os.environ,LC_ALL='C'))
    except PermissionError:
        # macOS refuses setuid /bin/ps under seatbelt, even with allow-default.
        # Read the same identity through libproc; keep all sandbox restrictions.
        if sys.platform!='darwin':raise
        return _darwin_process_identity(pid,include_zombie=include_zombie)
    if cp.returncode:return None
    fields=cp.stdout.strip().split(None,3)
    if len(fields)!=4 or fields[2].startswith('X') or (not include_zombie and fields[2].startswith('Z')):return None
    return {'pid':int(fields[0]),'pgid':int(fields[1]),'lstart':fields[3]}


def _darwin_process_identity(pid,*,include_zombie):
    """Read PROC_PIDTBSDINFO; unknown/denied probes never mean a dead owner.

    Layout/constants: macOS SDK sys/proc_info.h, struct proc_bsdinfo and
    PROC_PIDTBSDINFO (3); proc states are defined in sys/proc.h.
    Preserve ps's C-locale, local-time lstart format for frozen registries.
    """
    import ctypes
    import errno
    if pid>2**31-1:raise RegistryError('PID outside Darwin pid_t ABI')
    class BSDInfo(ctypes.Structure):
        _fields_=[(name,ctypes.c_uint32) for name in (
            'pbi_flags','pbi_status','pbi_xstatus','pbi_pid','pbi_ppid',
            'pbi_uid','pbi_gid','pbi_ruid','pbi_rgid','pbi_svuid','pbi_svgid','rfu_1')]+[
            ('pbi_comm',ctypes.c_char*16),('pbi_name',ctypes.c_char*32)]+[
            (name,ctypes.c_uint32) for name in
            ('pbi_nfiles','pbi_pgid','pbi_pjobc','e_tdev','e_tpgid')]+[
            ('pbi_nice',ctypes.c_int32),('pbi_start_tvsec',ctypes.c_uint64),
            ('pbi_start_tvusec',ctypes.c_uint64)]
    try:
        query=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True).proc_pidinfo
        query.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
        query.restype=ctypes.c_int
        info=BSDInfo();ctypes.set_errno(0)
        size=query(pid,3,0,ctypes.byref(info),ctypes.sizeof(info))
    except (OSError,AttributeError) as exc:
        raise RegistryError('native process identity probe unavailable') from exc
    if size!=ctypes.sizeof(info):
        if size==0 and ctypes.get_errno()==errno.ESRCH:return None
        raise RegistryError('native process identity probe denied or incomplete')
    if info.pbi_pid!=pid or info.pbi_pgid<=0 or info.pbi_start_tvsec<=0:
        raise RegistryError('native process identity contradicts PID/start/group')
    if info.pbi_status not in (1,2,3,4,5):
        raise RegistryError('native process state unknown')
    if info.pbi_status==5 and not include_zombie:return None
    start=time.localtime(info.pbi_start_tvsec)
    # Avoid the caller's locale: old ps probes explicitly used LC_ALL=C.
    weekday=('Mon','Tue','Wed','Thu','Fri','Sat','Sun')[start.tm_wday]
    month=('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')[start.tm_mon-1]
    lstart=f'{weekday} {month} {start.tm_mday:2d} {start.tm_hour:02d}:{start.tm_min:02d}:{start.tm_sec:02d} {start.tm_year}'
    return {'pid':pid,'pgid':info.pbi_pgid,'lstart':lstart}


def _path(runtime,run_id):
    if not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',run_id):
        raise RegistryError('invalid run ID')
    return Path(runtime).resolve()/'runs'/(run_id+'.json')


def _owner():
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise RegistryError('worker cannot mutate run registry')


def _write(path,row):
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as stream:
        temporary=Path(stream.name);json.dump(row,stream);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    try:temporary.chmod(0o600);os.replace(temporary,path);_sync_parent(path)
    finally:temporary.unlink(missing_ok=True)


def read(path):
    try:row=json.loads(Path(path).read_text())
    except (OSError,ValueError) as exc:raise RegistryError('unreadable run registry') from exc
    required={'run_id','project_key','state','clone','exec_tree','toolkit_sha','pid','pgid','lstart'}
    if not isinstance(row,dict) or row.get('schema')!=1 or not required<=row.keys() or row['state'] not in {'launching','running'}:
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
    children=row.get('children',[])
    if not isinstance(children,list):raise RegistryError('invalid child registry')
    for child in children:
        if (not isinstance(child,dict) or any(isinstance(child.get(key),bool) or not isinstance(child.get(key),int) or child[key]<=0 for key in ('pid','pgid'))
                or child['pid']!=child['pgid'] or not isinstance(child.get('lstart'),str) or not child['lstart']):
            raise RegistryError('invalid child identity')
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
        if any(item['liveness'] in {'launching','running'} for item in scan(runtime,now=now)):
            raise RegistryError('project already has an active registry identity')
        _write(path,row)
    return row


def bind_running(runtime,run_id,*,pid,probe=process_identity,now=None,allow_expired_lock_owner=False):
    _owner();path=_path(runtime,run_id);now=time.time() if now is None else now
    identity=probe(pid)
    if not identity or identity.get('pid')!=pid or identity.get('pgid')!=pid:
        raise RegistryError('runner must own its process group')
    with _lock(path.parent/'.registry.lock'):
        row=read(path)
        if row['state']!='launching':raise RegistryError('launch reservation expired or already bound')
        if row['expires_at']<=now:
            if not allow_expired_lock_owner:
                raise RegistryError('launch reservation expired or already bound')
            try:holder=json.loads((Path(runtime)/'run.lock').read_text())
            except (OSError,ValueError):raise RegistryError('expired launch reservation has no lock owner')
            if any(holder.get(name)!=identity[name] for name in ('pid','pgid','lstart')):
                raise RegistryError('expired launch reservation is not owned by runner lock')
        row.update(identity,state='running',bound_at=now)
        if os.environ.get('ILK_RUN_INPUTS'):
            from run_inputs import verify_launch_resources
            pinned=verify_launch_resources(Path(os.environ['ILK_RUN_INPUTS']))
            if pinned['toolkit_sha']!=row['toolkit_sha']:raise RegistryError('pinned code contradicts registry')
            row.update(inputs_pinned=True,code_root=pinned['code_root'],worker_home=pinned['worker_home'],inputs_manifest=str(Path(os.environ['ILK_RUN_INPUTS'])/'inputs.json'))
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
        row=read(path)
        children=[]
        for child in row.get('children',[]):
            state=liveness(dict(child,state='running'),probe=probe,now=now)
            # A vanished leader with a still-live group is unknown ownership,
            # not evidence that all worker descendants have exited.
            if state=='dead' and group_exists(child['pgid']):state='unverified-group'
            children.append(dict(child,liveness=state))
        result.append(dict(row,liveness=liveness(row,probe=probe,now=now),children_liveness=children))
    return result


def finish(runtime,run_id,*,identity):
    _owner();path=_path(runtime,run_id)
    with _lock(path.parent/'.registry.lock'):
        row=read(path)
        if row['state']!='running' or any(row[key]!=identity.get(key) for key in ('pid','pgid','lstart')):
            raise RegistryError('finish identity mismatch')
        for child in row.get('children',[]):
            observed=process_identity(child['pid'])
            if observed=={key:child[key] for key in ('pid','pgid','lstart')} or (observed is None and group_exists(child['pgid'])):
                raise RegistryError('owned child or unverified group still live')
        path.unlink()
        _sync_parent(path)


def project_status(runtime, *, project=None, probe=process_identity, now=None, locked=None):
    """Fail busy for unowned legacy state; never discover ownership via argv."""
    runtime=Path(runtime);rows=scan(runtime,probe=probe,now=now)
    if project and any(row['clone']!=str(Path(project).resolve()) for row in rows):
        raise RegistryError('registry belongs to another project')
    active=[row for row in rows if row['liveness'] in {'launching','running'} or any(
        child['liveness'] in {'running','unverified-group'} for child in row['children_liveness'])]
    if active:return {'busy':True,'source':'registry','runs':active}
    if locked is None:
        locked=False
        lockfile=runtime/'run.lock'
        if lockfile.exists():
            if os.name=='nt':raise RegistryError('Windows lock observation adapter unavailable')
            import fcntl
            with lockfile.open('rb') as stream:
                try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:locked=True
                else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    if locked:return {'busy':True,'source':'unowned-lock','runs':[]}
    legacy=runtime/'running.pid'
    if legacy.exists():
        raw=legacy.read_text().strip()
        if not raw.isdigit() or int(raw)<=0:raise RegistryError('legacy PID is invalid')
        if probe(int(raw)) is not None:return {'busy':True,'source':'unowned-legacy-pid','runs':[]}
    return {'busy':False,'source':'registry','runs':[]}


def host_blockers(*, projects=None, clone=None, exclude_group=None, data_root=None):
    """Migration/merge readers share registry truth, including legacy unknowns.

    Until complete input pinning is recorded, every active host run can depend
    on the mutable toolkit. No argv guess relaxes a merge guard.
    """
    from ilk_paths import ilk_data_root
    root=Path(data_root) if data_root else ilk_data_root()
    wanted={str(Path(path).resolve()) for path in projects} if projects else None
    result=[]
    for runtime in sorted((root/'projects').glob('*/runtime/launcher')):
        status=project_status(runtime)
        if not status['busy']:continue
        if not status['runs']:
            # Unknown old ownership cannot be scoped by guessing argv.
            result.append(-1);continue
        for row in status['runs']:
            if wanted and row['clone'] not in wanted:continue
            if row['liveness']=='running' and row['pgid']==exclude_group:continue
            if clone and row.get('inputs_pinned') is True and row.get('code_root'):
                target=Path(clone).resolve()
                working_roots=[Path(row[name]).resolve() for name in ('clone','exec_tree')]
                overlaps=any(root.is_relative_to(target) or target.is_relative_to(root)
                             for root in working_roots)
                # A private code pin removes a reader dependency, not the
                # running batch's write/landing dependency on its checkout.
                if not overlaps and not Path(row['code_root']).resolve().is_relative_to(target):
                    continue
            result.append(row['pid'] if row['liveness']=='running' else -1)
    return sorted(set(result))


def attach(runtime,run_id,*,pid,project,key,toolkit_sha,ttl,probe=process_identity):
    """Lock winner binds a launch reservation or creates its direct-run record."""
    _owner();runtime=Path(runtime);path=_path(runtime,run_id)
    identity=probe(pid)
    if not identity:raise RegistryError('runner process identity unavailable')
    if not path.exists():
        # Direct launch must prove this PID owns the legacy single-instance lock.
        try:holder=json.loads((runtime/'run.lock').read_text())
        except (OSError,ValueError):raise RegistryError('direct runner lock identity absent')
        if any(holder.get(name)!=identity[name] for name in ('pid','pgid','lstart')):
            raise RegistryError('direct runner does not own the recorded lock')
        reserve(runtime,run_id=run_id,project_key=key,clone=project,exec_tree=project,
                toolkit_sha=toolkit_sha,expires_at=time.time()+ttl)
    row=read(path)
    if row['clone']!=str(Path(project).resolve()) or row['toolkit_sha']!=toolkit_sha:
        raise RegistryError('launch reservation contradicts runner inputs')
    # A launcher reservation can expire while the lock-owning runner performs
    # pre-dispatch discovery.  The process identity persisted by the lock
    # helper is the durable proof that this exact runner, rather than a stale
    # or foreign process, may finish binding the exact reserved run ID.
    return bind_running(runtime,run_id,pid=pid,probe=probe,allow_expired_lock_owner=True)


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','reserve','attach','finish','stop','code-sha','child'])
    parser.add_argument('--runtime',type=Path)
    parser.add_argument('--code-root',type=Path)
    parser.add_argument('--run-id');parser.add_argument('--project',type=Path)
    parser.add_argument('--key');parser.add_argument('--toolkit-sha')
    parser.add_argument('--pid',type=int);parser.add_argument('--ttl',type=float)
    parser.add_argument('--signal',choices=['TERM','KILL'],default='TERM')
    parser.add_argument('--owner-pid',type=int)
    args=parser.parse_args()
    try:
        if args.action=='code-sha':print(code_sha(args.code_root));return 0
        if not args.runtime:raise RegistryError('runtime required')
        if args.action=='status':
            result=project_status(args.runtime,project=args.project);print(json.dumps(result))
            return 0 if result['busy'] else 1
        if args.action=='reserve':
            if not args.ttl or not math.isfinite(args.ttl) or args.ttl<=0:raise RegistryError('explicit positive launch TTL required')
            if project_status(args.runtime,project=args.project)['busy']:raise RegistryError('project is busy')
            result=reserve(args.runtime,run_id=args.run_id,project_key=args.key,clone=args.project,exec_tree=args.project,
                toolkit_sha=args.toolkit_sha,expires_at=time.time()+args.ttl)
        elif args.action=='attach':
            if not args.ttl or not math.isfinite(args.ttl) or args.ttl<=0:raise RegistryError('explicit positive launch TTL required')
            result=attach(args.runtime,args.run_id,pid=args.pid,project=args.project,key=args.key,toolkit_sha=args.toolkit_sha,ttl=args.ttl)
        elif args.action=='child':result=register_child(args.runtime,args.run_id,pid=args.pid,owner_pid=args.owner_pid)
        elif args.action=='finish':
            identity=process_identity(args.pid)
            if not identity:raise RegistryError('finishing process identity unavailable')
            finish(args.runtime,args.run_id,identity=identity);result={'finished':True}
        else:result=signal_run(args.runtime,args.run_id,signum=getattr(signal,'SIG'+args.signal))
        print(json.dumps(result));return 0
    except (RegistryError,OSError,ValueError,TypeError,KeyError) as exc:
        import sys
        print(str(exc),file=sys.stderr);return 2


def signal_run(runtime,run_id,*,probe=process_identity,send=os.killpg,signum=signal.SIGTERM):
    """Signal only a freshly verified recorded group. Caller controls waiting."""
    _owner();row=read(_path(runtime,run_id))
    parent_state=liveness(row,probe=probe)
    if parent_state!='running' and not any(liveness(dict(child,state='running'),probe=probe)=='running' for child in row.get('children',[])):
        raise RegistryError('recorded process identity is no longer live')
    if row['pgid']==os.getpgrp():raise RegistryError('refusing caller process group')
    if signum not in {signal.SIGTERM,signal.SIGKILL}:raise RegistryError('unsupported stop signal')
    for child in row.get('children',[]):
        state=liveness(dict(child,state='running'),probe=probe)
        if state=='running':
            if child['pgid']==os.getpgrp():raise RegistryError('refusing caller child process group')
            send(child['pgid'],signum)
        elif state=='dead' and group_exists(child['pgid']):
            if group_identity(child['pid'])=={key:child[key] for key in ('pid','pgid','lstart')}:
                send(child['pgid'],signum)
            else:raise RegistryError('child group ownership unavailable; no unverified signal')
    if parent_state=='running':send(row['pgid'],signum)
    return row


def group_exists(pgid):
    if os.name=='nt':raise RegistryError('Windows group observation adapter unavailable')
    try:os.killpg(pgid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def register_child(runtime,run_id,*,pid,owner_pid,probe=process_identity):
    _owner();path=_path(runtime,run_id)
    child=probe(pid)
    if not child or child.get('pid')!=pid or child.get('pgid')!=pid:
        raise RegistryError('owned child group leader unavailable')
    with _lock(path.parent/'.registry.lock'):
        row=read(path)
        if owner_pid!=row['pid'] or liveness(row,probe=probe)!='running':raise RegistryError('child owner identity unavailable')
        existing=row.setdefault('children',[])
        if child not in existing:existing.append(child)
        _write(path,row)
        _child_event(runtime,run_id,'registered',child)
    return child


def _child_event(runtime,run_id,event,identity):
    path=Path(runtime)/'child-events.jsonl'
    with path.open('a') as stream:
        stream.write(json.dumps(dict(run_id=run_id,event=event,identity=identity,
                                     observed_at=time.time()),separators=(',',':'))+'\n')
        stream.flush();os.fsync(stream.fileno())
    path.chmod(0o600);_sync_parent(path)


def retire_child(context,pid):
    """Remove only a reaped, absent group; retain its durable identity event.

    Unknown descendants stay registered. No termination, reused-PID guess or
    elapsed-time inference is used to make the live registry smaller.
    """
    owner=driver_authority(context)
    if not owner or not owner.get('run_registry_dir'):return True
    require_driver(context);runtime=Path(owner['run_registry_dir']);path=_path(runtime,owner['run_id'])
    with _lock(path.parent/'.registry.lock'):
        row=read(path)
        if row['pid']!=owner['runner_owner_pid'] or process_identity(row['pid'])!={key:row[key] for key in ('pid','pgid','lstart')}:
            raise RegistryError('child retirement owner changed')
        matches=[child for child in row.get('children',[]) if child['pid']==pid]
        if len(matches)!=1:return False
        child=matches[0]
        if group_identity(pid) is not None or group_exists(child['pgid']):return False
        _child_event(runtime,owner['run_id'],'closed',child)
        row['children']=[item for item in row['children'] if item!=child]
        _write(path,row)
    return True


def cleanup_children(runtime,run_id,*,owner,owner_exited=False,probe=process_identity,send=os.killpg):
    """An owned observer closes only freshly verified sibling groups.

    No parent signal, own-group signal, argv search or PID reuse inference.
    Unknown groups remain recorded and block a new launch.
    """
    _owner();path=_path(runtime,run_id)
    with _lock(path.parent/'.registry.lock'):
        row=read(path)
        if {key:row[key] for key in ('pid','pgid','lstart')}!=owner:
            raise RegistryError('owner-exit identity differs from registered run')
        observed_owner=probe(owner['pid'])
        if (owner_exited and observed_owner is not None) or (not owner_exited and observed_owner!=owner):
            raise RegistryError('child cleanup requires live exact owner or verified vanished owner')
        children=list(row.get('children',[]))
    refused=[]
    for child in children:
        if child['pgid']==os.getpgrp():continue # the observer exits itself
        observed=probe(child['pid'])
        if observed=={key:child[key] for key in ('pid','pgid','lstart')}:
            try:send(child['pgid'],signal.SIGKILL)
            except ProcessLookupError:pass
        elif observed is not None:refused.append(child['pid'])
        elif group_exists(child['pgid']):
            if group_identity(child['pid'])=={key:child[key] for key in ('pid','pgid','lstart')}:
                try:send(child['pgid'],signal.SIGKILL)
                except ProcessLookupError:pass
            else:refused.append(child['pid'])
    return refused


def cleanup_children_after_owner_exit(runtime,run_id,*,owner,probe=process_identity,send=os.killpg):
    return cleanup_children(runtime,run_id,owner=owner,owner_exited=True,probe=probe,send=send)


if __name__=='__main__':raise SystemExit(main())
