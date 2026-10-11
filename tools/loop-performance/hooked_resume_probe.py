#!/usr/bin/env python3
"""Exercise driver-red repair through resume with copied production hooks.

Synthetic disposable consumer fixture, not a production batch or speed benchmark.
The caller explicitly chooses timeout; no loop defaults or real plans change.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from resume_probe import collect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,required=True)
    args=parser.parse_args()
    manifest=json.loads((args.inputs/'inputs.json').read_text())
    home=Path(manifest['worker_home']);code=Path(manifest['code_root'])
    settings=home/'settings.json';configured=json.loads(settings.read_text())
    provider=configured.get('env',{})
    if not all(provider.get(key) for key in ('ANTHROPIC_BASE_URL','ANTHROPIC_MODEL','ANTHROPIC_AUTH_TOKEN')):
        parser.error('explicit provider/model/auth required')
    if not manifest.get('scope_observation_only'): parser.error('scope observation must be included')
    import sys
    sys.path.insert(0,str(code/'skills/ilk-loop/scripts'))
    from iteration_context import resolve_context,write_context
    session=str(uuid.uuid4());rows=[]
    with tempfile.TemporaryDirectory(prefix='ilk-hooked-resume-') as temporary:
        root=Path(temporary);project=root/'consumer';project.mkdir();plans=root/'plans';plans.mkdir()
        subprocess.run(['git','init','-q',str(project)],check=True)
        subprocess.run(['git','-C',str(project),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','base'],check=True)
        subplan=plans/'probe.md';subplan.write_text('---\nplan: probe\nstatus: pending\ncurrent_step: 0\n---\n# Synthetic one-file probe\n')
        logs=root/'logs';logs.mkdir();context=resolve_context(clone=project,exec_tree=project,
            observe_tree={str(project):str(project)},code_root=code,plans_dir=plans,
            runtime_dir=root/'runtime',data_root=root/'data',run_id='hooked-resume-probe',iteration=1,
            dispatch={'slug':'probe','step':0,'subplan_path':str(subplan)},mode='none')
        frozen=logs/'ctx.json';write_context(frozen,context)
        env={key:value for key,value in os.environ.items() if not key.startswith('ANTHROPIC_')}
        env.update(provider);env.update(HOME=str(home),CLAUDE_CONFIG_DIR=str(home),
            ILK_ITER_CONTEXT=str(frozen),ILK_WORKER_SESSION='1',ILK_ITER_RECORD_STAGE='',
            ILK_ITERATION_SUBPLAN='probe',ILK_DISPATCH_SLUG='probe',ILK_DISPATCH_SUBPLAN=str(subplan),
            ILK_PLANS_DIR=str(plans),ILK_DATA_HOME=str(root/'data'),ILK_DATA_DIR=str(root/'data'),
            ILK_WRITE_SCOPE_MODE='observe')
        target=project/'counter.py'
        command=['claude','-p','--output-format','stream-json','--verbose','--tools','Read,Write',
                 '--allowedTools','Read','Write','--permission-mode','acceptEdits',
                 '--setting-sources','','--settings',str(settings),'--model',provider['ANTHROPIC_MODEL']]
        for phase,prompt,flags in (
            ('initial',f'This is a synthetic native-write capability probe, not an ilk batch. Use Write to create exactly {target} containing Python def f(value): return value + 1. No other writes. Do not load skills or run tests. Reply briefly after writing.', ['--session-id',session]),
            ('resumed-red-repair',f'The driver measured a red gate: assert f(4) == 6 failed on {target}. Fix that exact file so f(value) returns value + 2. Use Read/Write only. Do not modify plans, commit, or run tests. Continue in this same session and reply briefly.', ['--resume',session]),
        ):
            started=time.monotonic();row={'phase':phase,'requested_model':provider['ANTHROPIC_MODEL'],
                                         'accepted':False,'production_hook_copies_active':True}
            try:
                cp=subprocess.run(command+flags+[prompt],cwd=project,env=env,capture_output=True,text=True,timeout=args.timeout)
                init,result,_=collect(cp.stdout)
                gate=subprocess.run(['python3','-I','-c','from pathlib import Path; exec(Path("counter.py").read_text()); assert f(4)==6'],cwd=project,env=env,capture_output=True,text=True,timeout=args.timeout)
                observed=logs/'write-scope-1.jsonl'
                events=[json.loads(line) for line in observed.read_text().splitlines()] if observed.exists() else []
                writes=[event for event in events if event.get('write_observed')]
                row.update(exit_code=cp.returncode,actual_model=init.get('model'),session_matches=init.get('session_id')==session,
                    turns=result.get('num_turns'),driver_gate_exit=gate.returncode,
                    native_write_observations=len(writes),outside_scope_observations=sum(event['scope']!='worktree' for event in writes))
                expected_gate=gate.returncode!=0 and 'AssertionError' in gate.stderr if phase=='initial' else gate.returncode==0
                row['accepted']=cp.returncode==0 and row['actual_model']==provider['ANTHROPIC_MODEL'] and row['session_matches'] and expected_gate and bool(writes) and row['outside_scope_observations']==0
            except subprocess.TimeoutExpired: row['error_class']='timeout'
            row['elapsed_seconds']=round(time.monotonic()-started,3);rows.append(row)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({'toolkit_sha':manifest['toolkit_sha'],'limits':'Synthetic native-write/driver-red repair only; no Bash boundary, interrupted resume or batch speed proof.',
                'timeout_judgment_seconds':args.timeout,'observations':rows},indent=2)+'\n')
            print(json.dumps(row),flush=True)
            if not row['accepted']: break


if __name__=='__main__':main()
