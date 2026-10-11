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
def _lock(path):
    with path.open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            if path.stat().st_size==0: stream.write(b'0');stream.flush()
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
        try: yield
        finally:
            if os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _append(log,row):
    with log.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(row,separators=(',',':'))+'\n')
        stream.flush();os.fsync(stream.fileno())


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


def transition(path: Path, from_: dict, to: dict, *, actor: str, reason: str) -> bool:
    """Change only requested scalar state fields if every expected value matches.

    Human holds cannot be lifted or progressed by driver calls. The worker
    session flag refuses mutation regardless of the actor it supplies.
    """
    if os.environ.get('ILK_WORKER_SESSION','').strip() or actor=='worker':
        raise TransitionError('workers cannot mutate plan state')
    if not actor or not reason or not from_ or not to:
        raise TransitionError('expected state, requested state, actor and reason required')
    path=Path(path).resolve(strict=True)
    from iteration_context import read_context
    context=read_context()
    if context:
        allowed={Path(row['path']).resolve() for row in context.get('plans',{}).values()}
        master=(context.get('dispatch') or {}).get('master_path')
        if master: allowed.add(Path(master).resolve())
        if path not in allowed: raise TransitionError('plan is outside frozen registry')
    allowed_fields={'status','current_step','parked_at','parked_reason','pre_park_status','yield','last_updated'}
    if not set(to)<=allowed_fields or not set(from_)<=allowed_fields:
        raise TransitionError('transition cannot edit plan contract fields')
    for value in to.values():
        if value is not None and (not isinstance(value,(str,int)) or isinstance(value,bool) or '\n' in str(value) or '\r' in str(value)):
            raise TransitionError('state values must be single-line scalars')
    log=path.parent/'state-transitions.jsonl'
    with _lock(path.parent/'.state-transitions.lock'):
        content=path.read_bytes();_recover(log,path,content)
        text=content.decode('utf-8-sig');state=parse_frontmatter(text)
        if any(state.get(key)!=str(value) if value is not None else key in state for key,value in from_.items()):
            raise TransitionError('state changed since dispatch; compare-and-set refused')
        if (state.get('status') in {'draft','parked','blocked'} or state.get('hold')=='human') and actor!='human':
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
        new=('---'+head.rstrip('\n')+text[end:]).encode('utf-8')
        if new==content: return False
        row=dict(id=str(uuid.uuid4()),path=str(path),actor=actor,reason=reason,
                 from_=from_,to=to,before_sha256=_digest(content),after_sha256=_digest(new),phase='intent')
        _append(log,row)
        with tempfile.NamedTemporaryFile(mode='wb',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);stream.write(new);stream.flush();os.fsync(stream.fileno())
        try:
            temporary.chmod(path.stat().st_mode & 0o777);os.replace(temporary,path)
        finally: temporary.unlink(missing_ok=True)
        _append(log,dict(row,phase='applied'))
        return True
