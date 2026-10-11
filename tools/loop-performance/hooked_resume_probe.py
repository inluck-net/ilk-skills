#!/usr/bin/env python3
"""Exercise driver-red repair through resume with copied production hooks.

Synthetic disposable consumer fixture, not a production batch or speed benchmark.
The caller explicitly chooses timeout; no loop defaults or real plans change.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import selectors
import signal
import tempfile
import time
import uuid
from resume_probe import collect


def interrupt_after_tool(command,*,cwd,env,timeout):
    """Kill only this owned CLI group after a completed tool result is observed."""
    lines=[];interrupted=False;deadline=time.monotonic()+timeout
    with tempfile.TemporaryFile(mode='w+') as stderr:
        process=subprocess.Popen(command,cwd=cwd,env=env,stdout=subprocess.PIPE,
                                 stderr=stderr,text=True,start_new_session=True)
        def kill_owned():
            if process.poll() is not None:return False
            group=os.getpgid(process.pid)
            try:
                if group==process.pid:os.killpg(group,signal.SIGKILL)
                else:os.kill(process.pid,signal.SIGKILL)
                return True
            except ProcessLookupError:return False
            except PermissionError:
                # A refused group signal is not evidence of interruption.
                # Signal only the exact Popen child; never widen PID selection.
                try:os.kill(process.pid,signal.SIGKILL);return True
                except (ProcessLookupError,PermissionError):return False
        selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
        try:
            while time.monotonic()<deadline:
                if not selector.select(max(0,deadline-time.monotonic())):break
                line=process.stdout.readline()
                if not line:break
                lines.append(line)
                try:row=json.loads(line)
                except ValueError:continue
                completed=any(isinstance(block,dict) and block.get('type')=='tool_result'
                              and not block.get('is_error') for block in row.get('message',{}).get('content',[]))
                if completed:
                    interrupted=kill_owned();break
                if row.get('type')=='result':break
        finally:
            selector.close()
            kill_owned()
            remainder,_=process.communicate();lines.append(remainder)
        stderr.seek(0)
        return subprocess.CompletedProcess(command,process.returncode,''.join(lines),stderr.read()),interrupted


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,required=True)
    parser.add_argument('--enforce-scope',action='store_true')
    parser.add_argument('--interrupt-after-write',action='store_true')
    parser.add_argument('--driver-accept',action='store_true')
    args=parser.parse_args()
    if args.interrupt_after_write and not args.enforce_scope:parser.error('interruption requires owned broker fixture')
    if args.driver_accept and not args.enforce_scope:parser.error('driver acceptance requires owned broker fixture')
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
    with ExitStack() as stack:
        temporary=stack.enter_context(tempfile.TemporaryDirectory(prefix='ilk-hooked-resume-'))
        root=Path(temporary);project=root/'consumer';project.mkdir();plans=root/'plans';plans.mkdir()
        subprocess.run(['git','init','-q',str(project)],check=True)
        subprocess.run(['git','-C',str(project),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','base'],check=True)
        if args.driver_accept:
            subprocess.run(['git','-C',str(project),'config','user.name','Fixture'],check=True)
            subprocess.run(['git','-C',str(project),'config','user.email','fixture@example.invalid'],check=True)
        subplan=plans/'probe.md';subplan.write_text('---\nplan: probe\nstatus: pending\ncurrent_step: 0\n---\n# Synthetic one-file probe\n')
        if args.driver_accept:
            subplan.write_text(subplan.read_text()+'''## Steps
### Step 0: counter repair
```yaml
local_checks:
  - command: python3 -I -c 'exec(open("counter.py").read()); assert f(4)==6'
```
''')
        logs=root/'logs';logs.mkdir();context=resolve_context(clone=project,exec_tree=project,
            observe_tree={str(project):str(project)},code_root=code,plans_dir=plans,
            runtime_dir=root/'runtime',data_root=root/'data',run_id='hooked-resume-probe',iteration=1,
            dispatch={'slug':'probe','step':0,'subplan_path':str(subplan)},mode='none')
        frozen=logs/'ctx.json'
        if args.enforce_scope:
            from worker_broker import ShellBroker
            from worker_sandbox import profile
            from worker_shell import canonical_command
            from run_inputs import bind_context
            private_tmp=root/'tmp';private_tmp.mkdir()
            context.update(worker_tmp_dir=str(private_tmp),private_worker_home=str(home),
                           worker_inputs_dir=str(args.inputs.resolve()),worker_shell_python=sys.executable)
            broker=stack.enter_context(ShellBroker(context,deadline=time.monotonic()+2*args.timeout))
            context.update(broker.descriptor())
        write_context(frozen,context)
        if args.enforce_scope:settings=bind_context(args.inputs,frozen)
        env={key:value for key,value in os.environ.items() if not key.startswith('ANTHROPIC_')}
        env.update(provider);env.update(HOME=str(home),CLAUDE_CONFIG_DIR=str(home),
            ILK_ITER_CONTEXT=str(frozen),ILK_WORKER_SESSION='1',ILK_ITER_RECORD_STAGE='',
            ILK_ITERATION_SUBPLAN='probe',ILK_DISPATCH_SLUG='probe',ILK_DISPATCH_SUBPLAN=str(subplan),
            ILK_PLANS_DIR=str(plans),ILK_DATA_HOME=str(root/'data'),ILK_DATA_DIR=str(root/'data'),
            ILK_WRITE_SCOPE_MODE='observe')
        if args.enforce_scope:env.update(TMPDIR=str(private_tmp),CLAUDE_CODE_TMPDIR=str(private_tmp),
                                        CLAUDE_TMPDIR=str(private_tmp),ILK_WRITE_SCOPE_MODE='enforce')
        target=project/'counter.py'
        command=['claude','-p','--output-format','stream-json','--verbose','--tools','Read,Write',
                 '--allowedTools','Read','Write','--permission-mode','acceptEdits',
                 '--setting-sources','','--settings',str(settings),'--model',provider['ANTHROPIC_MODEL']]
        phases=(
            ('initial',f'This is a synthetic native-write capability probe, not an ilk batch. Use Write to create exactly {target} containing Python def f(value): return value + 1. No other writes. Do not load skills or run tests. Reply briefly after writing.', ['--session-id',session]),
            ('resumed-red-repair',f'The driver measured a red gate: assert f(4) == 6 failed on {target}. Fix that exact file so f(value) returns value + 2. Use Read/Write only. Do not modify plans, commit, or run tests. Continue in this same session and reply briefly.', ['--resume',session]),
        )
        if args.enforce_scope:
            command=['/usr/bin/sandbox-exec','-p',profile(context,temporary=private_tmp,private_home=home,
                     diagnostic_log=logs/'write-scope-1.jsonl')]+command
            command[command.index('Read,Write')]='Bash'
            allowed=command.index('--allowedTools');command[allowed+1:allowed+3]=['Bash']
            phases=tuple((phase,
                'Synthetic scoped-shell probe; use Bash exactly once with this exact command, '
                'including its quoting. No skills, tests, plans or commits. '+canonical_command(context,frozen,
                "printf 'def f(value): return value + "+str(value)+"\\n' > counter.py"),flags)
                for (phase,_,flags),value in zip(phases,(1,2)))
        for phase,prompt,flags in phases:
            started=time.monotonic();row={'phase':phase,'requested_model':provider['ANTHROPIC_MODEL'],
                                         'accepted':False,'production_hook_copies_active':True}
            try:
                interrupted=False
                if args.interrupt_after_write and phase=='initial':
                    cp,interrupted=interrupt_after_tool(command+flags+[prompt],cwd=project,env=env,timeout=args.timeout)
                else:cp=subprocess.run(command+flags+[prompt],cwd=project,env=env,capture_output=True,text=True,timeout=args.timeout)
                init,result,_=collect(cp.stdout)
                if args.driver_accept:
                    from driver_step import accept_step
                    accepted=accept_step(context,default_timeout=args.timeout,deadline=broker.deadline)
                    row.update(driver_accepted=accepted['accepted'],driver_commit_count=len(accepted.get('commits',[])),
                        driver_attempt_count=len(accepted['attempts']),pointer_advanced='current_step: 1' in subplan.read_text(),
                        shipping_proof_provisional=accepted.get('proof_provisional'))
                    gate=subprocess.CompletedProcess([],0 if accepted['accepted'] else 1,'',
                        '\n'.join(check['stderr_tail'] for attempt in accepted['attempts'] for check in attempt['check_observations']))
                else:gate=subprocess.run(['python3','-I','-c','from pathlib import Path; exec(Path("counter.py").read_text()); assert f(4)==6'],cwd=project,env=env,capture_output=True,text=True,timeout=args.timeout)
                observed=logs/'write-scope-1.jsonl'
                events=[json.loads(line) for line in observed.read_text().splitlines()] if observed.exists() else []
                writes=[event for event in events if event.get('write_observed')]
                broker_rows=[]
                if args.enforce_scope:
                    for artifact in (root/'runtime').rglob('attempts.jsonl'):
                        broker_rows.extend(json.loads(line) for line in artifact.read_text().splitlines())
                row.update(exit_code=cp.returncode,actual_model=init.get('model'),session_matches=init.get('session_id')==session,
                    turns=result.get('num_turns'),driver_gate_exit=gate.returncode,
                    native_write_observations=len(writes),outside_scope_observations=sum(event['scope']!='worktree' for event in writes))
                row.update(scope_enforced=args.enforce_scope,broker_attempts=len(broker_rows),
                           advertised_tools=init.get('tools'),owned_cli_interrupted=interrupted,
                           terminal_result_observed=bool(result))
                expected_gate=gate.returncode!=0 and 'AssertionError' in gate.stderr if phase=='initial' else gate.returncode==0
                changed=bool(broker_rows) if args.enforce_scope else bool(writes)
                completion=interrupted and not result if args.interrupt_after_write and phase=='initial' else cp.returncode==0
                row['accepted']=completion and row['actual_model']==provider['ANTHROPIC_MODEL'] and row['session_matches'] and expected_gate and changed and row['outside_scope_observations']==0
                if cp.returncode:row['cli_error_tail']=cp.stderr[-1000:]
            except subprocess.TimeoutExpired: row['error_class']='timeout'
            row['elapsed_seconds']=round(time.monotonic()-started,3);rows.append(row)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({'toolkit_sha':manifest['toolkit_sha'],'limits':('Synthetic owned-interruption/driver-red repair only; no batch speed proof.' if args.interrupt_after_write else 'Synthetic brokered-shell/driver-red repair only; no interrupted resume or batch speed proof.' if args.enforce_scope else 'Synthetic native-write/driver-red repair only; no Bash boundary, interrupted resume or batch speed proof.'),
                'timeout_judgment_seconds':args.timeout,'observations':rows},indent=2)+'\n')
            print(json.dumps(row),flush=True)
            if not row['accepted']: break


if __name__=='__main__':main()
