#!/usr/bin/env python3
"""Keep red diagnostics across launches; this module never produces green proof."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from iteration_context import read_context


def read_retry_state(path: Path, kind: str) -> dict:
    if not path.exists(): return {}
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict): raise ValueError('retry history must be an object')
    for key,value in data.items():
        if not isinstance(key,str): raise ValueError('invalid retry identity')
        if kind=='count':
            if not isinstance(value,int) or isinstance(value,bool) or value<0:
                raise ValueError('invalid retry count')
        elif kind=='ids':
            if not isinstance(value,list) or any(not isinstance(item,str) or not item for item in value):
                raise ValueError('invalid failing IDs')
        else: raise ValueError('unknown retry history kind')
    return data


def update_retry_state(path: Path, key: str, kind: str, value=None):
    # The runner lock serializes driver writes. Never repair corrupt history by
    # resetting it: that would silently grant another retry budget.
    data=read_retry_state(path,kind)
    if kind=='count': value=data.get(key,0)+1
    else:
        if not isinstance(value,list) or any(not isinstance(item,str) or not item for item in value):
            raise ValueError('invalid failing IDs')
        value=sorted(set(value))
    data[key]=value
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
        tmp=Path(f.name)
        try:
            json.dump(data,f,separators=(',',':'));f.flush();os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True);raise
    try:
        tmp.chmod(0o600);os.replace(tmp,path)
    finally: tmp.unlink(missing_ok=True)
    return value


def notice_path(context: dict, slug: str, step: int) -> Path:
    key=hashlib.sha256(json.dumps([slug,step]).encode()).hexdigest()
    return Path(context['recovery_dir'])/f'gate-red-{key}.txt'


def save_notice(context: dict, slug: str, step: int, source: Path):
    destination=notice_path(context,slug,step)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=destination.parent,delete=False) as f:
        tmp=Path(f.name);f.write(source.read_text());f.flush();os.fsync(f.fileno())
    tmp.chmod(0o600);os.replace(tmp,destination)


def brief(context: dict, slug: str, step: int) -> list[str]:
    root=Path(context['recovery_dir']);key=f'{slug}:{step}'
    lines=[]
    try:
        count=json.loads((root/'gate-red-counts.json').read_text()).get(key)
    except (OSError,ValueError): count=None
    path=notice_path(context,slug,step)
    if count is not None or path.is_file():
        lines.append('Prior red diagnostics for this frozen batch/step; no green proof is carried:')
        if count is not None: lines.append(f'Existing retry count: {count}; preserve the existing retry rules.')
        if path.is_file(): lines.append(f'Prior gate output and artifact links: {path}')
    salvage=root/'last-worker-salvage.json'
    if salvage.is_file():
        try:
            data=json.loads(salvage.read_text())
            selected=data.get('dispatch') or {}
            if selected.get('slug')==slug and selected.get('step')==step:
                lines.append(f'Interrupted worker diagnostic context: {salvage}; rerun required gates after changes.')
        except (OSError,ValueError): pass
    return lines


def salvage_transcript(context: dict, source: Path, destination: Path) -> dict:
    """Recover completed root messages only; pending tools are never executed."""
    sessions=set();models=set();tools={};finished=set();last_text=None
    last_results=[];last_stream=None;malformed=0;result_subtypes=[]
    for line in source.read_text(encoding='utf-8-sig',errors='replace').splitlines():
        try: row=json.loads(line)
        except ValueError: malformed+=1;continue
        if not isinstance(row,dict) or row.get('parent_tool_use_id'): continue
        kind=row.get('type')
        if kind=='system' and row.get('subtype')=='init':
            if row.get('session_id'): sessions.add(row['session_id'])
            if row.get('model'): models.add(row['model'])
        if kind=='stream_event': last_stream=(row.get('event') or {}).get('type')
        if kind=='assistant':
            texts=[]
            for block in (row.get('message') or {}).get('content',[]):
                if not isinstance(block,dict): continue
                if block.get('type')=='text': texts.append(block.get('text',''))
                if block.get('type')=='tool_use' and block.get('id'):
                    tools[block['id']]={'id':block['id'],'name':block.get('name')}
            if texts: last_text='\n'.join(texts)
        if kind=='user':
            summaries=[]
            for block in (row.get('message') or {}).get('content',[]):
                if isinstance(block,dict) and block.get('type')=='tool_result':
                    finished.add(block.get('tool_use_id'))
                    content=block.get('content')
                    summaries.append({'tool_use_id':block.get('tool_use_id'),
                                      'is_error':block.get('is_error'),
                                      'content_chars':len(json.dumps(content)) if content is not None else None})
            if summaries: last_results=summaries
        if kind=='result': result_subtypes.append(row.get('subtype'))
    payload=dict(schema_version=1,diagnostic_only=True,source=str(source),
                 run_id=context['run_id'],iteration=context['iteration'],dispatch=context.get('dispatch'),
                 sessions_observed=sorted(sessions),models_observed=sorted(models),
                 last_completed_assistant_text=last_text,last_tool_result_summaries=last_results,
                 pending_tools=[v for key,v in tools.items() if key not in finished],
                 unfinished_stream_response=None if last_stream is None else last_stream!='message_stop',
                 malformed_rows=malformed,result_subtypes=result_subtypes)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=destination.parent,delete=False) as f:
        tmp=Path(f.name);json.dump(payload,f);f.write('\n');f.flush();os.fsync(f.fileno())
    tmp.chmod(0o600);os.replace(tmp,destination)
    return payload


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sources=p.add_mutually_exclusive_group(required=True)
    sources.add_argument('--save-notice',type=Path)
    sources.add_argument('--salvage',type=Path)
    p.add_argument('--out',type=Path)
    p.add_argument('--slug')
    p.add_argument('--step',type=int)
    a=p.parse_args();context=read_context()
    if not context: p.error('frozen iteration context required')
    if a.salvage:
        if not a.out: p.error('--out required for salvage')
        salvage_transcript(context,a.salvage,a.out)
    else:
        if not a.slug or a.step is None: p.error('slug and step required for notice')
        save_notice(context,a.slug,a.step,a.save_notice)
if __name__=='__main__':main()
