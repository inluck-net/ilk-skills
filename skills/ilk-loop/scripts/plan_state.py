#!/usr/bin/env python3
"""Compare-and-set plan frontmatter with a durable transition journal.

All migrated writers use the plans directory's state-transitions.jsonl. A
write-ahead intent lets the next call finish logging an interrupted replacement
without inventing a successful transition. Unmigrated writers still exist.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from plan_status import parse_frontmatter


class TransitionError(RuntimeError):
    pass


@contextmanager
def _lock(path, *, deadline=None, guard=None, non_blocking=False):
    if guard:guard()
    with path.open('a+b') as stream:
        if deadline is not None or guard is not None or non_blocking:
            import threading
            from iteration_clock import monotonic
            while True:
                if guard:guard()
                if deadline is not None and monotonic()>=deadline:
                    raise TransitionError('state lock reached existing iteration deadline')
                try:
                    if os.name=='nt':
                        import msvcrt
                        if path.stat().st_size==0:stream.write(b'0');stream.flush()
                        stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except OSError as error:
                    import errno
                    if error.errno not in (errno.EACCES,errno.EAGAIN):raise
                    if non_blocking:raise TransitionError('state lock busy; observation unavailable') from error
                    # 20ms readiness judgment matches owned child cleanup;
                    # sustained CPU or slow valid release falsifies it.
                    threading.Event().wait(.02 if deadline is None else min(.02,max(deadline-monotonic(),0)))
        elif os.name=='nt':
            import msvcrt
            if path.stat().st_size==0: stream.write(b'0');stream.flush()
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
        try:
            if guard:guard()
            yield
        finally:
            if os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def _driver_lock_options(context):
    if not context or context.get('worker_sandbox_adapter')!='macos-brokered-shell':return {}
    from driver_step import _deadline
    from run_registry import require_driver
    deadline=_deadline.get() or context.get('iteration_deadline_monotonic')
    return dict(deadline=deadline,guard=lambda:require_driver(context))


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def journal_cursor(plans):
    log=Path(plans)/'state-transitions.jsonl'
    raw=log.read_bytes() if log.exists() else b''
    return {'bytes':len(raw),'sha256':_digest(raw)}


def audit_context(context):
    """Explain owned state from the frozen snapshot plus completed transitions."""
    plans=Path(context['plans_dir']);cursor=context.get('state_journal_cursor')
    if not isinstance(cursor,dict):raise TransitionError('state journal cursor absent')
    snapshots=list(context.get('plans',{}).values())
    if context.get('master_snapshot'):snapshots.append(context['master_snapshot'])
    with _lock(plans/'.state-transitions.lock',**_driver_lock_options(context)):
        log=plans/'state-transitions.jsonl';raw=log.read_bytes() if log.exists() else b''
        length=cursor.get('bytes')
        if isinstance(length,bool) or not isinstance(length,int) or length<0 or length>len(raw) or _digest(raw[:length])!=cursor.get('sha256'):
            raise TransitionError('state journal prefix changed')
        expected={row['path']:dict(row['owned_state']) for row in snapshots}
        issues=[];pending={}
        for line in raw[length:].splitlines():
            try:row=json.loads(line)
            except ValueError:raise TransitionError('state journal is corrupt')
            if not isinstance(row,dict) or row.get('phase') not in {'intent','applied','recovered-applied','recovered-not-applied'}:
                raise TransitionError('state journal record invalid')
            ident=row.get('id');path=row.get('path')
            if row['phase']=='intent':
                if ident in pending:raise TransitionError('duplicate state intent')
                pending[ident]=row;continue
            original=pending.pop(ident,None)
            if original is None or any(row.get(key)!=original.get(key) for key in ('path','before_sha256','after_sha256','from_','to')):
                raise TransitionError('state completion has no matching intent')
            if row['phase']=='recovered-not-applied' or path not in expected:continue
            state=expected[path]
            for key,value in row.get('from_',{}).items():
                want=None if value is None else str(value)
                if key in state and state[key]!=want:
                    issues.append({'path':path,'field':key,'reason':'transition starts from unexplained state'})
            for key,value in row.get('to',{}).items():
                if key in state:
                    state[key]=None if value is None else parse_frontmatter('---\n'+key+': '+str(value)+'\n---\n').get(key)
        if pending:raise TransitionError('state journal contains an unclosed transition')
        for path,state in expected.items():
            observed=parse_frontmatter(Path(path).read_text(encoding='utf-8-sig'))
            for key,want in state.items():
                if observed.get(key)!=want:issues.append({'path':path,'field':key,'reason':'state change not explained by journal'})
        return {'explained':not issues,'violations':issues,'observed_plans':len(expected)}


def _append(log,row):
    existed=log.exists()
    with log.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(row,separators=(',',':'))+'\n')
        stream.flush();os.fsync(stream.fileno())
    if not existed:_sync_parent(log)


def _sync_parent(path):
    if os.name=='nt':return # Windows has no portable directory fsync adapter.
    descriptor=os.open(str(path.parent),os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def _recover(log,path,content):
    if not log.exists(): return
    pending={}
    for line in log.read_text(encoding='utf-8').splitlines():
        try: row=json.loads(line)
        except ValueError: raise TransitionError('transition journal is corrupt')
        if row.get('path')!=str(path): continue
        if row.get('phase')=='intent': pending[row['id']]=row
        else: pending.pop(row.get('id'),None)
    for row in pending.values():
        actual=_digest(content)
        if actual==row['after_sha256']: phase='recovered-applied'
        elif actual==row['before_sha256']: phase='recovered-not-applied'
        else: raise TransitionError('unexplained plan change after interrupted transition')
        _append(log,dict(row,phase=phase))


def transition(path: Path, from_: dict, to: dict, *, actor: str, reason: str, guards=None,
               findings_note=None,release_auto_block=False,expected_digest=None) -> bool:
    """Change only requested scalar state fields if every expected value matches.

    Human holds cannot be lifted or progressed by driver calls. The worker
    session flag refuses mutation regardless of the actor it supplies.
    """
    if os.environ.get('ILK_WORKER_SESSION','').strip() or actor=='worker':
        raise TransitionError('workers cannot mutate plan state')
    if not actor or not reason or not from_ or not to:
        raise TransitionError('expected state, requested state, actor and reason required')
    if findings_note is not None and (not isinstance(findings_note,str) or '\x00' in findings_note):
        raise TransitionError('invalid finding text')
    path=Path(path).resolve(strict=True)
    guards={Path(guard).resolve(strict=True):expected for guard,expected in (guards or {}).items()}
    if any(guard.parent!=path.parent for guard in guards):
        raise TransitionError('guard must share the state journal directory')
    from iteration_context import read_context
    context=read_context()
    if context:
        if actor!='human' and context.get('worker_sandbox_adapter')=='macos-brokered-shell':
            from run_registry import require_driver
            require_driver(context)
        allowed={Path(row['path']).resolve() for row in context.get('plans',{}).values()}
        master=(context.get('dispatch') or {}).get('master_path')
        if master: allowed.add(Path(master).resolve())
        if path not in allowed: raise TransitionError('plan is outside frozen registry')
        if not set(guards)<=allowed:raise TransitionError('guard is outside frozen registry')
    allowed_fields={'status','current_step','parked_at','parked_reason','pre_park_status','yield','hold','auto_block_fails','last_updated'}
    if not set(to)<=allowed_fields or not set(from_)<=allowed_fields:
        raise TransitionError('transition cannot edit plan contract fields')
    for value in to.values():
        if value is not None and (not isinstance(value,(str,int)) or isinstance(value,bool) or '\n' in str(value) or '\r' in str(value)):
            raise TransitionError('state values must be single-line scalars')
    log=path.parent/'state-transitions.jsonl'
    with _lock(path.parent/'.state-transitions.lock',**(_driver_lock_options(context) if actor!='human' else {})):
        if context and actor!='human' and context.get('worker_sandbox_adapter')=='macos-brokered-shell':
            require_driver(context)
        for guard,expected in guards.items():
            raw=guard.read_bytes();_recover(log,guard,raw)
            observed=parse_frontmatter(raw.decode('utf-8-sig'))
            if any(observed.get(key)!=str(value) if value is not None else key in observed for key,value in expected.items()):
                raise TransitionError('guard state changed since dispatch')
            if (observed.get('status') in {'draft','parked','blocked'} or observed.get('hold')=='human') and actor!='human':
                raise TransitionError('human hold on guard cannot be progressed')
        content=path.read_bytes();_recover(log,path,content)
        if expected_digest is not None and _digest(content)!=expected_digest:
            raise TransitionError('plan bytes changed before transition')
        text=content.decode('utf-8-sig');state=parse_frontmatter(text)
        if any(state.get(key)!=str(value) if value is not None else key in state for key,value in from_.items()):
            raise TransitionError('state changed since dispatch; compare-and-set refused')
        auto_release=(release_auto_block and state.get('status')=='blocked' and state.get('hold')!='human'
                      and str(state.get('auto_block_fails','')).isdigit() and int(state['auto_block_fails'])>0
                      and from_.get('status')=='blocked' and str(from_.get('auto_block_fails'))==state['auto_block_fails']
                      and to=={'status':'in-progress','auto_block_fails':0})
        if release_auto_block and not auto_release:raise TransitionError('automatic block release is not justified')
        desired_hold=state.get('hold') if 'hold' not in to else parse_frontmatter('---\nhold: '+str(to['hold'])+'\n---\n').get('hold')
        repair=(actor=='integrity-repair' and set(to)<={'status','hold','yield','parked_at','parked_reason'}
                and (state.get('hold')!='human' or desired_hold=='human'))
        if actor=='integrity-repair' and not repair:raise TransitionError('integrity repair cannot advance pointers or erase human holds')
        if (state.get('status') in {'draft','parked','blocked'} or state.get('hold')=='human') and actor!='human' and not auto_release and not repair:
            raise TransitionError('human hold cannot be changed by driver')
        if not text.startswith('---') or (end:=text.find('\n---',3))<0:
            raise TransitionError('frontmatter missing')
        head=text[3:end]
        for key,value in to.items():
            pattern=re.compile(r'^'+re.escape(key)+r':[^\n]*(?:\n|$)',re.M)
            hits=list(pattern.finditer(head))
            if len(hits)>1: raise TransitionError('duplicate state field')
            replacement='' if value is None else f'{key}: {value}\n'
            if hits: head=pattern.sub(lambda _:replacement,head,count=1)
            elif value is not None: head=head.rstrip('\n')+'\n'+replacement
        projected='---'+head.rstrip('\n')+text[end:]
        if findings_note is not None:
            markers=list(re.finditer(r'^## Findings[ \t]*$',projected,re.M))
            if markers:
                start=markers[-1].end()
                following=re.search(r'^## ',projected[start:],re.M)
                position=start+following.start() if following else len(projected)
                projected=projected[:position].rstrip()+'\n\n'+findings_note+'\n\n'+projected[position:].lstrip('\n')
            else:projected=projected.rstrip()+'\n\n## Findings\n\n'+findings_note+'\n'
        # State transactions preserve the source encoding/newline convention.
        if b'\r\n' in content:
            projected=projected.replace('\r\n','\n').replace('\n','\r\n')
        new=projected.encode('utf-8')
        if content.startswith(b'\xef\xbb\xbf'):new=b'\xef\xbb\xbf'+new
        if new==content: return False
        row=dict(id=str(uuid.uuid4()),path=str(path),actor=actor,reason=reason,
                 from_=from_,to=to,before_sha256=_digest(content),after_sha256=_digest(new),phase='intent')
        _append(log,row)
        with tempfile.NamedTemporaryFile(mode='wb',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);stream.write(new);stream.flush();os.fsync(stream.fileno())
        try:
            temporary.chmod(path.stat().st_mode & 0o777);os.replace(temporary,path);_sync_parent(path)
        finally: temporary.unlink(missing_ok=True)
        _append(log,dict(row,phase='applied'))
        return True


def replace_legacy_contract(path, expected: bytes, replacement: str, *, reason, return_binding=None):
    """Journal a legacy verification contract revision with exact-byte CAS.

    Owned dispatch never revises its frozen contract. This adapter retains the
    older explicit return-red operation while preventing stale writes and holds.
    """
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise TransitionError('workers cannot revise plan contracts')
    from iteration_context import read_context
    context=read_context()
    if context and context.get('worker_sandbox_adapter')=='macos-brokered-shell':
        raise TransitionError('owned dispatch cannot revise its frozen plan contract')
    path=Path(path).resolve(strict=True)
    if context and path not in {Path(row['path']).resolve() for row in context.get('plans',{}).values()}:
        raise TransitionError('contract revision is outside frozen registry')
    if not isinstance(expected,bytes) or not isinstance(replacement,str) or not reason:
        raise TransitionError('expected bytes, replacement and reason required')
    if b'\r\n' in expected:replacement=replacement.replace('\r\n','\n').replace('\n','\r\n')
    new=replacement.encode('utf-8')
    if expected.startswith(b'\xef\xbb\xbf'):new=b'\xef\xbb\xbf'+new
    log=path.parent/'state-transitions.jsonl'
    with _lock(path.parent/'.state-transitions.lock'):
        content=path.read_bytes();_recover(log,path,content)
        if content!=expected:raise TransitionError('plan bytes changed before contract revision')
        before=parse_frontmatter(content.decode('utf-8-sig'))
        after=parse_frontmatter(new.decode('utf-8-sig'))
        if before.get('status') in {'draft','parked','blocked'} or before.get('hold')=='human':
            raise TransitionError('human hold cannot be changed by contract revision')
        changed={key for key in set(before)|set(after) if before.get(key)!=after.get(key)}
        if not changed<={'status','current_step'} or not before.get('status'):
            raise TransitionError('legacy contract revision changed unrelated frontmatter')
        if new==content:return False
        keys=changed or {'status'}
        row=dict(id=str(uuid.uuid4()),path=str(path),actor='legacy-verification-return',reason=reason,
            from_={key:before.get(key) for key in keys},to={key:after.get(key) for key in keys},
            contract_revision=True,return_binding=return_binding,before_sha256=_digest(content),after_sha256=_digest(new),phase='intent')
        _append(log,row)
        with tempfile.NamedTemporaryFile(mode='wb',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);stream.write(new);stream.flush();os.fsync(stream.fileno())
        try:temporary.chmod(path.stat().st_mode & 0o777);os.replace(temporary,path);_sync_parent(path)
        finally:temporary.unlink(missing_ok=True)
        _append(log,dict(row,phase='applied'))
        return True


def recover_legacy_contract(path, *, before_sha256, after_sha256, reason, return_binding=None):
    """Recognize only an exact durable legacy revision, never infer a gate pass."""
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise TransitionError('workers cannot recover plan contracts')
    from iteration_context import read_context
    context=read_context()
    if context and context.get('worker_sandbox_adapter')=='macos-brokered-shell':
        raise TransitionError('owned dispatch cannot revise its frozen plan contract')
    path=Path(path).resolve(strict=True)
    if context and path not in {Path(row['path']).resolve() for row in context.get('plans',{}).values()}:
        raise TransitionError('contract recovery is outside frozen registry')
    log=path.parent/'state-transitions.jsonl'
    with _lock(path.parent/'.state-transitions.lock'):
        content=path.read_bytes();_recover(log,path,content)
        if _digest(content)!=after_sha256:return False
        if not log.exists():return False
        pending={};matched=False
        for line in log.read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            if not isinstance(row,dict):raise TransitionError('invalid state journal record')
            ident=row.get('id')
            if row.get('phase')=='intent':pending[ident]=row;continue
            original=pending.pop(ident,None)
            if original is None:continue
            if (row.get('phase') in {'applied','recovered-applied'} and
                all(row.get(key)==original.get(key) for key in ('path','before_sha256','after_sha256','actor','reason','from_','to','return_binding')) and
                row.get('path')==str(path) and row.get('contract_revision') is True and
                row.get('actor')=='legacy-verification-return' and row.get('reason')==reason and
                row.get('return_binding')==return_binding and
                row.get('before_sha256')==before_sha256 and row.get('after_sha256')==after_sha256):
                matched=True
        return matched


def append_findings(path, note, *, reason):
    """Append an idempotent driver note without racing a changed plan body."""
    path=Path(path);raw=path.read_bytes();text=raw.decode('utf-8-sig')
    if note in text:return False
    state=parse_frontmatter(text)
    if 'status' not in state:raise TransitionError('finding target has no state')
    return transition(path,{'status':state['status']},{'status':state['status']},
        actor='integrity-repair',reason=reason,findings_note=note,expected_digest=_digest(raw))


def rollback_ship(path, *, step=None, history=None, slug=None, reason):
    """Retract only a current ship; only verified red history lowers a pointer."""
    path=Path(path);raw=path.read_bytes();state=parse_frontmatter(raw.decode('utf-8-sig'))
    if state.get('status')!='shipped':raise TransitionError('rollback target is not shipped')
    failed=[]
    if history:
        for line in Path(history).read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            try:row=json.loads(line)
            except ValueError:raise TransitionError('rollback gate history is corrupt')
            value=row.get('step')
            if row.get('slug')==slug and row.get('outcome') in {'fail','error'}:
                if isinstance(value,bool) or not isinstance(value,int) or value<0:
                    raise TransitionError('rollback gate step is invalid')
                failed.append(value)
        step=min(failed) if failed else None
    expected={'status':'shipped'};wanted={'status':'in-progress'}
    note='pointer unchanged'
    if step is not None:
        if isinstance(step,bool) or not isinstance(step,int) or step<0:raise TransitionError('invalid rollback step')
        current=state.get('current_step','')
        if not current.isdigit():raise TransitionError('rollback pointer is invalid')
        expected['current_step']=current
        if int(current)>step:wanted['current_step']=step;note=f'current_step {current} -> {step}'
    transition(path,expected,wanted,actor='driver',reason=reason,expected_digest=_digest(raw))
    return note


def resolve_plan(plans, slug):
    """Use the frozen registry for every identity, never a first glob match."""
    from iteration_context import read_context
    from dispatch_target import identities,resolve,TargetError
    context=read_context()
    if context:
        if Path(context['plans_dir']).resolve()!=Path(plans).resolve():raise TransitionError('foreign plans root')
        candidates=[Path(row['path']) for row in context.get('plans',{}).values()]
        matches=[path for path in candidates if slug in identities(path)]
        if len(matches)!=1:raise TransitionError('ambiguous or absent frozen plan identity')
        return matches[0]
    try:return Path(resolve(Path(plans),slug)['subplan_path'])
    except TargetError as exc:raise TransitionError(str(exc)) from exc


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['finding','rollback','status','resolve','audit'])
    parser.add_argument('--context',type=Path)
    parser.add_argument('--enforce',action='store_true')
    parser.add_argument('--path',type=Path)
    parser.add_argument('--plans',type=Path)
    parser.add_argument('--slug')
    parser.add_argument('--note')
    parser.add_argument('--reason',default='driver refusal')
    parser.add_argument('--step',type=int)
    parser.add_argument('--history',type=Path)
    args=parser.parse_args()
    try:
        if args.action=='audit':
            from iteration_context import read_context
            result=audit_context(read_context(args.context));print(json.dumps(result))
            return 3 if args.enforce and not result['explained'] else 0
        path=args.path or resolve_plan(args.plans,args.slug)
        if args.action=='resolve':print(path)
        elif args.action=='status':print(parse_frontmatter(path.read_text(encoding='utf-8-sig')).get('status',''))
        elif args.action=='finding':
            if not args.note:raise TransitionError('finding note required')
            append_findings(path,args.note,reason=args.reason)
        else:print(rollback_ship(path,step=args.step,history=args.history,slug=args.slug,reason=args.reason))
        return 0
    except (TransitionError,OSError,ValueError,TypeError) as exc:
        import sys
        print(str(exc),file=sys.stderr);return 2


def reconcile_registry(path: Path, *, expected_digest: str) -> bool:
    """CAS only master registry Status projections, derived under journal lock."""
    if os.environ.get('ILK_WORKER_SESSION','').strip():raise TransitionError('workers cannot project registry state')
    path=Path(path).resolve(strict=True)
    from iteration_context import read_context
    context=read_context()
    if context and (context.get('dispatch') or {}).get('master_path')!=str(path):
        raise TransitionError('registry projection is outside frozen master')
    from plan_fingerprint import registry_rows
    log=path.parent/'state-transitions.jsonl'
    with _lock(path.parent/'.state-transitions.lock',**_driver_lock_options(context)):
        content=path.read_bytes();_recover(log,path,content)
        if _digest(content)!=expected_digest:raise TransitionError('master changed before registry projection')
        text=content.decode('utf-8-sig');lines=text.splitlines(keepends=True);changed=False
        for index,column,name in registry_rows(text):
            child=(path.parent/name).resolve()
            if child.parent!=path.parent:raise TransitionError('registry path escapes plans root')
            if not child.is_file():continue
            state=parse_frontmatter(child.read_text(encoding='utf-8-sig'))
            want=state.get('status','pending').strip();line=lines[index];cells=line.rstrip('\r\n').split('|')
            if cells[column].strip()==want:continue
            cells[column]=' '+want+' '
            ending='\r\n' if line.endswith('\r\n') else ('\n' if line.endswith('\n') else '')
            lines[index]='|'.join(cells)+ending;changed=True
        if not changed:return False
        new=''.join(lines).encode('utf-8')
        if content.startswith(b'\xef\xbb\xbf'):new=b'\xef\xbb\xbf'+new
        row=dict(id=str(uuid.uuid4()),path=str(path),actor='driver',reason='registry status projection',
                 from_={},to={},projection='registry',before_sha256=_digest(content),after_sha256=_digest(new),phase='intent')
        _append(log,row)
        with tempfile.NamedTemporaryFile(mode='wb',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);stream.write(new);stream.flush();os.fsync(stream.fileno())
        try:temporary.chmod(path.stat().st_mode & 0o777);os.replace(temporary,path);_sync_parent(path)
        finally:temporary.unlink(missing_ok=True)
        _append(log,dict(row,phase='applied'))
        return True


if __name__=='__main__':
    raise SystemExit(main())
