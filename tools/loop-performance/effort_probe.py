#!/usr/bin/env python3
"""Isolated same-model capability probe; never changes worker defaults.

This small synthetic task cannot establish full-batch performance. Replication
count and timeout are explicit operator choices, not runner defaults. Reports
exclude provider credentials, raw prompts and generated code.
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

PROMPT = '''Return only JSON with one field "code", containing Python source for a
function gate_counts(rows). Each row is a dictionary. Count rows whose outcome
is exactly "failed", and sum integer nonnegative failed_nodes of those rows.
Ignore booleans, negative numbers, numeric strings and missing failed_nodes.
Return a tuple (failed_rows, failed_nodes). Do not use tools. No explanation.'''
CASES = '''
assert gate_counts([]) == (0,0)
assert gate_counts([{'outcome':'failed','failed_nodes':3},{'outcome':'passed','failed_nodes':99}]) == (1,3)
assert gate_counts([{'outcome':'failed','failed_nodes':True},{'outcome':'failed','failed_nodes':-2},{'outcome':'failed','failed_nodes':'4'}, {'outcome':'failed'}]) == (4,0)
assert gate_counts([{'outcome':'failed','failed_nodes':0},{'outcome':'error','failed_nodes':5}]) == (1,0)
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker-home', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--timeout', type=int, required=True)
    p.add_argument('--pairs', type=int, required=True)
    args = p.parse_args()
    env_settings = json.loads((args.worker_home/'settings.json').read_text()).get('env', {})
    if not env_settings.get('ANTHROPIC_BASE_URL') or not env_settings.get('ANTHROPIC_MODEL') or not env_settings.get('ANTHROPIC_AUTH_TOKEN'):
        raise SystemExit('Refusing probe: explicit provider/model/auth missing.')
    observations = []
    for pair in range(args.pairs):
        # Alternate order; tiny sample is capability evidence, not a benchmark.
        for effort in (['default','low'] if pair % 2 == 0 else ['low','default']):
            with tempfile.TemporaryDirectory(prefix='ilk-effort-probe-') as sandbox:
                root = Path(sandbox)
                home = root/'home'; home.mkdir()
                settings = root/'settings.json'
                settings.write_text(json.dumps({'env':env_settings, 'disableAllHooks':True}))
                env = {k:v for k,v in os.environ.items() if not k.startswith('ANTHROPIC_')}
                env.update(env_settings)
                env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(home),
                           ILK_DATA_HOME=str(root/'data'), ILK_DATA_DIR=str(root/'data'))
                command = ['claude','-p','--output-format','stream-json','--verbose',
                           '--no-session-persistence','--tools','',
                           '--setting-sources','','--settings',str(settings),
                           '--model',env_settings['ANTHROPIC_MODEL']]
                if effort != 'default':
                    command += ['--effort',effort]
                command.append(PROMPT)
                start = time.monotonic()
                row = {'pair':pair+1,'effort':effort,'task':'gate-counts-v1',
                       'requested_model':env_settings['ANTHROPIC_MODEL'],
                       'accepted':False,'limits':'Synthetic tools-disabled task, not a production step.'}
                try:
                    cp = subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=args.timeout)
                    row['exit_code'] = cp.returncode
                    result, text, init = {}, '', {}
                    for line in cp.stdout.splitlines():
                        try: event=json.loads(line)
                        except ValueError: continue
                        if event.get('type')=='system' and event.get('subtype')=='init': init=event
                        if event.get('type')=='result': result=event
                        if event.get('type')=='assistant' and not event.get('parent_tool_use_id'):
                            text += ''.join(b.get('text','') for b in event.get('message',{}).get('content',[]) if b.get('type')=='text')
                    row['actual_model'] = init.get('model')
                    row['per_turn_effort_active'] = init.get('per_turn_effort_active')
                    row['api_seconds'] = result.get('duration_api_ms',0)/1000 if result else None
                    row['output_tokens'] = result.get('usage',{}).get('output_tokens')
                    candidate = json.loads(text.strip().removeprefix('```json').removesuffix('```').strip())
                    code = root/'candidate.py'; code.write_text(candidate['code']+'\n'+CASES)
                    tested = subprocess.run(['python3','-I',str(code)],cwd=root,env=env,capture_output=True,timeout=args.timeout)
                    row['accepted'] = cp.returncode==0 and tested.returncode==0 and row['actual_model']==row['requested_model']
                except subprocess.TimeoutExpired:
                    row['error_class'] = 'timeout'
                except (ValueError, KeyError, TypeError):
                    row['error_class'] = 'invalid-output'
                row['elapsed_seconds'] = round(time.monotonic()-start,3)
                observations.append(row)
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(observations,indent=2)+'\n')
                print(json.dumps(row),flush=True)


if __name__=='__main__': main()
