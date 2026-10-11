"""Real Bash entry with private pinned inputs and a disposable provider stub."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from run_inputs import prepare,pin_launch_resources
from run_registry import project_status,signal_run

pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='Mac production entry adapter')
ROOT=Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('case',['normal','selfmod','work-tree','native-tools','native-tools-batch-optin','hold','pending-advance','pending-selfmod','pending-work-tree','pending-staged','pending-committed','two-plans','gate-first-green','gate-first-red','upgrade-drain'])
def test_actual_code_only_runner_ships_only_after_driver_acceptance(tmp_path,monkeypatch,case):
    human_hold_during_gate=case=='hold'
    upgrading=case=='upgrade-drain'
    spec=importlib.util.spec_from_file_location('entry_fixture',Path(__file__).with_name('test_pre_loop_exit_sentinel.py'))
    fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
    world=fixtures._build_world_shipped(tmp_path,with_steps=True);work=world['project']
    if case in {'selfmod','pending-selfmod'}:
        import shutil
        shutil.rmtree(work) # disposable fixture only
        subprocess.run(['git','clone','-q','--shared',str(ROOT),str(work)],check=True,capture_output=True)
    def git(*args):return subprocess.run(['git','-C',str(work),*args],check=True,capture_output=True,text=True)
    git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (work/'counter.py').write_text('def f(value): return value + 1\n');git('add','.');git('commit','-qm','fixture counter')
    plan=world['plans']/(fixtures.STEM+'.md')
    plan.write_text('---\nplan: '+fixtures.SLUG+'\nstatus: pending\ncurrent_step: 0\nestimated_steps: 1\nverification_tier: loop-verified\n---\n# Counter\n## Steps\n### Step 0: fix counter\n```yaml\nlocal_checks:\n  - command: python3 -I -c "from pathlib import Path;exec(Path(\'counter.py\').read_text());assert f(4)==6"\n```\n')
    master=world['plans']/'MASTER-2026-08-29-execution-plan.md'
    declared_root=None
    if case in {'work-tree','pending-work-tree'}:
        declared_root=tmp_path/'declared-execution'
        git('worktree','add','--detach',str(declared_root))
        master.write_text(master.read_text().replace('status: active','status: active\nwork_tree: '+str(declared_root)))
    source_head=git('rev-parse','HEAD').stdout.strip()
    if case.startswith('gate-first-'):
        plan.write_text(plan.read_text().replace('local_checks:','gate_first: true\nlocal_checks:'))
        if case=='gate-first-green':
            (work/'counter.py').write_text('def f(value): return value + 2\n')
            git('add','.');git('commit','-qm','pre-existing accepted behavior')
    later=world['plans']/'2026-10-11-later.md'
    if case=='two-plans' or upgrading:
        later.write_text('---\nplan: later\nstatus: pending\ncurrent_step: 0\nestimated_steps: 1\n---\n# Later\n'
            '## Steps\n### Step 0: add file\n```yaml\nlocal_checks:\n'
            '  - command: python3 -I -c "from pathlib import Path;assert Path(\'later.txt\').read_text()==\'accepted\'"\n```\n')
        master.write_text(master.read_text()+'\n| '+later.name+' |\n')
    if human_hold_during_gate or upgrading:
        # The coordinator lives outside the gate's write boundary. An ignored
        # readiness file synchronizes it without changing the accepted tree.
        import shlex
        (work/'.gitignore').write_text('gate-ready\n');git('add','.gitignore');git('commit','-qm','fixture coordination')
        # 0.8s is a fixture coordination judgment over ~20ms host readiness
        # polling, not a production delay. Missed valid holds falsify this gap.
        action='from pathlib import Path;import threading;Path("gate-ready").write_text("ready");threading.Event().wait(0.8)'
        plan.write_text(plan.read_text().replace('python3 -I -c "from pathlib',
            'python3 -I -c '+shlex.quote(action)+'; python3 -I -c "from pathlib'))
    if case.startswith('native-tools'):
        # A harmless suite stand-in catches automatic invocation without
        # running any full suite. Its marker must appear only on explicit opt-in.
        import shlex
        (work/'.gitignore').write_text('batch-suite-invoked\n')
        command='from pathlib import Path;Path("batch-suite-invoked").write_text("explicit")'
        (work/'.ilk-launch.json').write_text(json.dumps({'ship':{'verification_subplan':'required',
            'suite':{'command':'python3 -I -c '+shlex.quote(command),'flags':[],'timeout':10}}}))
        git('add','.gitignore','.ilk-launch.json');git('commit','-qm','fixture optional final suite')
    home=tmp_path/'source-home';home.mkdir()
    (home/'settings.json').write_text(json.dumps({'env':{'ANTHROPIC_BASE_URL':'https://example.invalid','ANTHROPIC_MODEL':'fixture-model','ANTHROPIC_AUTH_TOKEN':'fixture-token'}}))
    original=(home/'settings.json').read_bytes()
    cli=world['bin']/'claude'
    cli.write_text('#!'+sys.executable+'\n'+'''import json,os,subprocess,sys
from pathlib import Path
from iteration_context import read_context
from worker_shell import canonical_command
assert sys.argv[sys.argv.index('--setting-sources')+1]==''
path=Path(os.environ['ILK_ITER_CONTEXT']);ctx=read_context(path)
command="printf 'accepted' > later.txt" if ctx['dispatch']['slug']=='later' else "printf 'def f(value): return value + 2\\n' > counter.py"
if ctx.get('worker_tools')=='native':
 assert sys.argv[sys.argv.index('--tools')+1]=='Bash,Read,Grep,Glob,Edit,Write'
 Path(ctx['exec_tree'],'counter.py').write_text('def f(value): return value + 2\\n')
 for protected in (Path(ctx['dispatch']['subplan_path']),Path(ctx['exec_tree'])/'.git/forged-worker-state'):
  try:protected.write_text('forged')
  except PermissionError:pass
  else:raise AssertionError('native CLI escaped protected scope')
else:
 assert sys.argv[sys.argv.index('--tools')+1]=='Bash'
 cp=subprocess.run(['/bin/bash','-c',canonical_command(ctx,path,command)],capture_output=True,text=True)
 assert cp.returncode==0,cp.stderr
print(json.dumps({'type':'system','subtype':'init','session_id':'fixture-session','model':'fixture-model'}))
print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':'fixture-session'}))
''');cli.chmod(0o700)
    inputs=tmp_path/'inputs';manifest=prepare(work if case in {'selfmod','pending-selfmod'} else ROOT,home,inputs,worker_tools='native' if case.startswith('native-tools') else 'shell')
    pin_launch_resources(inputs,project=work,executables={'python':sys.executable,'claude':str(cli)})
    if upgrading:
        target_inputs=tmp_path/'upgrade-target-inputs'
        target_source=tmp_path/'upgrade-target-source'
        subprocess.run(['git','clone','-q','--shared',str(ROOT),str(target_source)],check=True,capture_output=True)
        subprocess.run(['git','-C',str(target_source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
            'commit','--allow-empty','-qm','disposable next release identity'],check=True,capture_output=True)
        target_manifest=prepare(target_source,home,target_inputs)
        pin_launch_resources(target_inputs,project=work,executables={'python':sys.executable,'claude':str(cli)})
    code=Path(manifest['code_root'])
    if case.startswith('pending-'):
        from iteration_context import resolve_context,write_context
        from iteration_clock import monotonic
        import driver_step
        prior_root=declared_root or work
        if case=='pending-selfmod':
            prior_root=world['data_home']/'projects'/world['key']/'runtime/launcher/worktrees/selfmod-batch'
            prior_root.parent.mkdir(parents=True,exist_ok=True)
            git('worktree','add','--detach',str(prior_root))
        context=resolve_context(clone=work,exec_tree=prior_root,observe_tree={str(work):str(prior_root)},
            code_root=code,plans_dir=world['plans'],runtime_dir=world['data_home']/'projects'/world['key']/'runtime',
            data_root=world['data_home'],run_id='prior-interrupted',iteration=1,mode='selfmod' if case=='pending-selfmod' else 'work_tree' if case=='pending-work-tree' else 'none',
            dispatch={'slug':fixtures.SLUG,'step':0,'subplan_path':str(plan),'master_path':str(master)})
        context.update(worker_sandbox_adapter='macos-brokered-shell',toolkit_sha=manifest['toolkit_sha'])
        frozen=tmp_path/'prior-ctx.json';write_context(frozen,context)
        (prior_root/'counter.py').write_text('def f(value): return value + 2\n')
        real=driver_step.transition
        def crash(*args,**kwargs):
            real(*args,**kwargs)
            raise RuntimeError('fixture lost caller after advancement')
        real_git=driver_step.git
        def crash_git(root,*args,**kwargs):
            answer=real_git(root,*args,**kwargs)
            if (case=='pending-staged' and args[:2]==('add','-A') and kwargs.get('env') is None
                    or case=='pending-committed' and args[0]=='commit'):
                raise RuntimeError('fixture lost caller at commit')
            return answer
        with monkeypatch.context() as patch:
            patch.setenv('ILK_ITER_CONTEXT',str(frozen))
            if case in {'pending-advance','pending-selfmod','pending-work-tree'}:patch.setattr(driver_step,'transition',crash)
            else:patch.setattr(driver_step,'git',crash_git)
            with pytest.raises(RuntimeError):
                driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
        assert ('current_step: 1' if case in {'pending-advance','pending-selfmod','pending-work-tree'} else 'current_step: 0') in plan.read_text()
        assert 'status: shipped' not in plan.read_text()
    env=dict(os.environ,HOME=str(tmp_path),ILK_DATA_HOME=str(world['data_home']),ILK_DATA_DIR=str(world['data_home']),
        ILK_SKILL_HOME=str(code/'skills'),ILK_CODE_ROOT=str(code),ILK_RUN_INPUTS=str(inputs),
        CLAUDE_CONFIG_DIR=manifest['worker_home'],ILK_CLAUDE_BIN=str(cli),ILK_WORKER_MODE='code-only',
        PYTHONPATH=str(code/'skills/ilk-loop/scripts'),PATH=str(world['bin'])+os.pathsep+os.environ['PATH'])
    for key in ('ILK_SKIP_MAIN','ILK_WORKER_SESSION','ILK_ITER_CONTEXT','ILK_ITER_RECORD_STAGE'):env.pop(key,None)
    runtime=world['data_home']/'projects'/world['key']/'runtime/launcher'
    if upgrading:env['ILK_ENABLE_UPGRADE_DRAIN']='1'
    process=subprocess.Popen(['bash',str(code/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh'),
        '--project-path',str(work),'--max-iterations','2' if case=='two-plans' else '1','--iteration-timeout-min','1','--run-local-checks',*(['--run-batch-gate'] if case=='native-tools-batch-optin' else []),
        '--mcp-config-path',str(inputs/'mcp.json')],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        start_new_session=True)
    try:
        if human_hold_during_gate or upgrading:
            from iteration_clock import monotonic
            import threading
            # Fixture startup judgment: observed gates begin within ~12s;
            # 20s stays below the caller's existing 40s outer fixture bound.
            deadline=monotonic()+20
            while not (work/'gate-ready').exists():
                assert process.poll() is None and monotonic()<deadline
                threading.Event().wait(.02)
            if human_hold_during_gate:
                master.write_text(master.read_text().replace('status: active','status: blocked\nhold: human'))
            else:
                running=json.loads((runtime/'last-exit.json').read_text())
                (runtime/'upgrade-drain-request.json').write_text(json.dumps(dict(schema_version=1,
                    run_id=running['run_id'],from_toolkit_sha=manifest['toolkit_sha'],master_path=str(master),
                    next_inputs_dir=str(target_inputs),next_toolkit_sha=target_manifest['toolkit_sha'],
                    consumer_protocol='upgrade-drain-v1-owner-confirmed')))
        # 40s fixture judgment for sampled ~12s launch/close, below the
        # deliberately supplied 60s iteration budget; slower valid startup
        # falsifies this test bound, not a production limit.
        stdout,stderr=process.communicate(timeout=40)
        (tmp_path/'entry.stdout').write_text(stdout);(tmp_path/'entry.stderr').write_text(stderr)
        terminal=json.loads((runtime/'last-exit.json').read_text())
        runs=world['data_home']/'projects'/world['key']/'logs/runs'
        assert not list(runs.glob('*/master-snapshot-*.json'))
        assert not list(runs.glob('*/subplan-status-snapshot-*.json'))
        assert not list(runs.glob('*/gates-snapshot-*.json'))
        if human_hold_during_gate:
            assert terminal['state']!='all-shipped'
            assert 'status: blocked' in master.read_text() and 'hold: human' in master.read_text()
            assert 'current_step: 0' in plan.read_text() and 'status: shipped' not in plan.read_text()
            assert (home/'settings.json').read_bytes()==original
            assert not project_status(runtime)['busy']
            attempts=list((runtime/'driver-step').glob('*/attempt.json'))
            assert any(json.loads(path.read_text())['all_passed'] for path in attempts)
            return
        if upgrading:
            assert terminal['state']=='upgrade-drained',(terminal,stdout[-4500:],stderr[-4500:])
            assert process.returncode==0
            assert terminal['toolkit_sha']==manifest['toolkit_sha']
            assert terminal['next_toolkit_sha']==target_manifest['toolkit_sha']
            assert terminal['master_path']==str(master) and str(work) in terminal['consumer_worktrees']
            assert 'status: shipped' in plan.read_text() and 'current_step: 1' in plan.read_text()
            assert 'status: pending' in later.read_text() and 'current_step: 0' in later.read_text()
            assert 'status: active' in master.read_text()
            assert (home/'settings.json').read_bytes()==original
            assert not project_status(runtime)['busy']
            assert not (runtime/'running.pid').exists()
            selection=[sys.executable,'-c',
                'import json,sys;from pathlib import Path;from upgrade_drain import activation;print(json.dumps(activation(Path(sys.argv[1]),Path(sys.argv[2]))))',
                str(runtime),str(work)]
            selected=subprocess.run(selection,env=env,capture_output=True,text=True,check=True)
            assert json.loads(selected.stdout)['next_toolkit_sha']==target_manifest['toolkit_sha']
            # Independently refuse a still-live old PID, a human hold, and a
            # changed owner request before the scheduler's activation entry.
            sentinel_path=runtime/'last-exit.json'
            request_path=runtime/'upgrade-drain-request.json'
            for protected in (sentinel_path,master,request_path):
                before=protected.read_bytes()
                if protected==master:
                    protected.write_text(before.decode().replace('status: active','status: blocked\nhold: human'))
                else:
                    changed=json.loads(before)
                    changed['pid' if protected==sentinel_path else 'run_id']=os.getpid() if protected==sentinel_path else 'another-run'
                    protected.write_text(json.dumps(changed))
                try:
                    refused=subprocess.run(selection,env=env,capture_output=True,text=True)
                    assert refused.returncode!=0,(refused.stdout,refused.stderr)
                finally:protected.write_bytes(before)
            # Activate through the same launcher entry the scheduler calls.
            # Deliberately supplied 99/99 limits must not replace the frozen
            # original 1 iteration / 1 minute contract.
            activation_env=dict(env,ILK_SKIP_MAIN='1')
            launch=ROOT/'skills/ilk-launcher/scripts/launch.sh'
            argv=['bash','-c','source "$1"; unset ILK_SKIP_MAIN; start_ilk_window "$2" fixture 99 99 true false "" claude "" false ""',
                'fixture',str(launch),str(work)]
            started=subprocess.run(argv,env=activation_env,capture_output=True,text=True,timeout=20)
            assert started.returncode==0,(started.stdout,started.stderr)
            metadata=json.loads((runtime/'last-launch.json').read_text())
            assert metadata['toolkit_sha']==target_manifest['toolkit_sha']
            assert metadata['max_iterations']==1 and metadata['iteration_timeout_min']==1
            assert metadata['run_local_checks'] is True
            # 20s fixture judgment over observed ~7s stub launch; a valid
            # slower startup falsifies this bound, not production defaults.
            deadline=monotonic()+20
            while monotonic()<deadline:
                next_exit=json.loads((runtime/'last-exit.json').read_text())
                if next_exit.get('run_id')!=terminal['run_id'] and next_exit.get('state')!='running':break
                threading.Event().wait(.02)
            assert next_exit['state']=='all-shipped',(next_exit,started.stdout,started.stderr)
            assert next_exit['toolkit_sha']==target_manifest['toolkit_sha']
            assert next_exit['run_id']!=terminal['run_id']
            assert 'status: shipped' in later.read_text() and 'status: shipped' in master.read_text()
            assert not (runtime/'upgrade-drain-request.json').exists()
            assert (runtime/('upgrade-drain-consumed-'+terminal['run_id']+'.json')).exists()
            # Exit sentinel precedes EXIT-trap registry cleanup by a brief
            # interval. Wait for actual release rather than infer it.
            while project_status(runtime)['busy'] and monotonic()<deadline:threading.Event().wait(.02)
            assert not project_status(runtime)['busy']
            return
        if case.startswith('pending-'):
            assert terminal['state']=='already-shipped',(terminal,stdout[-4500:],stderr[-4500:])
            assert process.returncode==0
            assert 'status: shipped' in plan.read_text()
            assert 'status: shipped' in master.read_text()
            assert not list(runs.glob('*/iter-*.log.jsonl')) # zero provider dispatches
            records=[json.loads(line) for line in (world['data_home']/'projects'/world['key']/'logs/.ilk-loop.log').read_text().splitlines()]
            recovered=[row for row in records if row.get('acceptance_recovered_by_driver')]
            assert len(recovered)==1 and recovered[0]['run_id']=='prior-interrupted'
            assert recovered[0]['driver_integrity_passed'] and recovered[0]['state_audit']['explained']
            assert (home/'settings.json').read_bytes()==original and not project_status(runtime)['busy']
            if case=='pending-work-tree':
                assert git('rev-parse','HEAD').stdout.strip()==source_head
                assert recovered[0]['context']['exec_tree']==str(declared_root.resolve())
                assert recovered[0]['merge_outcome']=='not-applicable'
            if case=='pending-selfmod':
                assert prior_root.exists() and recovered[0]['merge_outcome']=='merged'
                assert git('rev-parse','HEAD').stdout.strip()==subprocess.check_output(['git','-C',str(prior_root),'rev-parse','HEAD'],text=True).strip()
            return
        assert terminal['state']=='all-shipped',(terminal,stdout[-4500:],stderr[-4500:])
        assert process.returncode==0,(stdout[-4500:],stderr[-4500:])
        if case.startswith('native-tools'):
            assert (work/'batch-suite-invoked').exists()==(case=='native-tools-batch-optin')
            if case=='native-tools':assert 'Optional final verification not requested; skipped.' in stdout
        assert 'current_step: 1' in plan.read_text() and 'status: shipped' in plan.read_text()
        if case=='two-plans':
            assert 'status: shipped' in later.read_text() and 'status: shipped' in master.read_text()
        assert (home/'settings.json').read_bytes()==original
        assert not project_status(runtime)['busy']
        closes=[json.loads(line) for line in (world['data_home']/'projects'/world['key']/'logs/.ilk-loop.log').read_text().splitlines()]
        closed=[row for row in closes if row.get('record_phase')=='close'][0]
        assert closed['driver_acceptance']['accepted'] is True
        assert closed['driver_integrity_passed'] is True and closed['state_audit']['explained'] is True
        phases={row['name']:row for row in closed['driver_phase_observation']['intervals']}
        for name in ('driver_gate','driver_commit_and_hooks','integrity_and_state_audit','owned_group_cleanup'):
            assert phases[name]['duration_sec']>=0 and phases[name]['observation_complete']
        if case=='selfmod':
            assert phases['landing']['duration_sec']>=0 and closed['merge_outcome']=='merged'
            isolated=world['data_home']/'projects'/world['key']/'runtime/launcher/worktrees/selfmod-batch'
            assert isolated.exists() # keep readable through close and recovery
            assert git('rev-parse','HEAD').stdout.strip()==subprocess.check_output(['git','-C',str(isolated),'rev-parse','HEAD'],text=True).strip()
            state=json.loads((runtime/'selfmod-state.json').read_text())
            assert state['state']=='MERGED'
            assert not (work/'.ilk-merge-deferred').exists()
        else:
            assert phases['landing']['duration_sec'] is None # ordinary consumer has no merge
        assert len(closed['driver_acceptance']['attempts'])==(2 if case=='gate-first-red' else 1)
        if case=='gate-first-green':
            assert closed['driver_acceptance']['gate_first'] and closed['driver_acceptance']['worker_turns']==[]
            assert closed['driver_acceptance']['commits']==[]
        if case=='gate-first-red':
            assert len(closed['driver_acceptance']['attempts'])==2
            assert len(closed['driver_acceptance']['worker_turns'])==1
        if case=='work-tree':
            assert git('rev-parse','HEAD').stdout.strip()==source_head
            assert closed['context']['exec_tree']==str(declared_root.resolve())
            assert closed['context']['work_tree_contract']['common_dir']==str((work/'.git').resolve())
            assert (declared_root/'counter.py').read_text()=='def f(value): return value + 2\n'
        if case=='two-plans':
            assert len([row for row in closes if row.get('record_phase')=='close'])==2
    finally:
        if runtime.exists():
            for row in project_status(runtime)['runs']:
                if row['liveness']=='running':signal_run(runtime,row['run_id'])
        if process.poll() is None:process.kill();process.wait()
