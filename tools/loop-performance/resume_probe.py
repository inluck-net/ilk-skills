#!/usr/bin/env python3
"""Probe actual provider session continuity in a disposable tools-disabled home.

This does not establish recovery under production hooks or batch performance.
Timeout is an explicit experiment judgment, never a runner default.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid


def collect(output):
    init, result, texts = {}, {}, []
    for line in output.splitlines():
        try: row=json.loads(line)
        except ValueError: continue
        if row.get('type')=='system' and row.get('subtype')=='init': init=row
        if row.get('type')=='result': result=row
        if row.get('type')=='assistant' and not row.get('parent_tool_use_id'):
            texts.extend(b.get('text','') for b in row.get('message',{}).get('content',[])
                         if b.get('type')=='text')
    text=''.join(texts).strip()
    if text.startswith('```json'): text=text[len('```json'):].strip()
    if text.endswith('```'): text=text[:-3].strip()
    try: answer=json.loads(text)
    except ValueError: answer=None
    return init,result,answer


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker-home',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--timeout',type=int,required=True)
    a=p.parse_args()
    provider=json.loads((a.worker_home/'settings.json').read_text()).get('env',{})
    if not all(provider.get(key) for key in ('ANTHROPIC_BASE_URL','ANTHROPIC_MODEL','ANTHROPIC_AUTH_TOKEN')):
        p.error('explicit provider/model/auth required')
    rows=[];session=str(uuid.uuid4());token=str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix='ilk-resume-probe-') as temporary:
        root=Path(temporary);home=root/'home';home.mkdir()
        settings=root/'settings.json'
        settings.write_text(json.dumps({'env':provider,'disableAllHooks':True}));settings.chmod(0o600)
        env={key:value for key,value in os.environ.items() if not key.startswith('ANTHROPIC_')}
        env.update(provider);env.update(HOME=str(home),CLAUDE_CONFIG_DIR=str(home),
                                       ILK_DATA_HOME=str(root/'data'),ILK_DATA_DIR=str(root/'data'))
        command=['claude','-p','--output-format','stream-json','--verbose','--tools','',
                 '--setting-sources','','--settings',str(settings),'--model',provider['ANTHROPIC_MODEL']]
        for phase,prompt,flags in (
            ('initial',f'Remember this opaque token for the next request: {token}. Return only JSON {{"remembered":true}}. Do not use tools.', ['--session-id',session]),
            ('resumed','Return only JSON with field "token" containing the opaque token remembered from my previous request. Do not use tools.', ['--resume',session]),
        ):
            start=time.monotonic();row={'phase':phase,'requested_model':provider['ANTHROPIC_MODEL'],
                                       'accepted':False,'production_hooks_verified':False}
            try:
                cp=subprocess.run(command+flags+[prompt],env=env,cwd=root,capture_output=True,text=True,timeout=a.timeout)
                init,result,answer=collect(cp.stdout)
                row.update(exit_code=cp.returncode,actual_model=init.get('model'),
                           session_matches=init.get('session_id')==session,
                           api_seconds=result.get('duration_api_ms',0)/1000 if result else None,
                           turns=result.get('num_turns'))
                expected={'remembered':True} if phase=='initial' else {'token':token}
                row['accepted']=cp.returncode==0 and row['session_matches'] and row['actual_model']==provider['ANTHROPIC_MODEL'] and answer==expected
            except subprocess.TimeoutExpired: row['error_class']='timeout'
            row['elapsed_seconds']=round(time.monotonic()-start,3)
            rows.append(row);a.output.parent.mkdir(parents=True,exist_ok=True)
            a.output.write_text(json.dumps({'limits':'Synthetic tools-disabled continuity only; no batch speed claim.',
                                           'timeout_judgment_seconds':a.timeout,'observations':rows},indent=2)+'\n')
            print(json.dumps(row),flush=True)
            if not row['accepted']: break


if __name__=='__main__':main()
