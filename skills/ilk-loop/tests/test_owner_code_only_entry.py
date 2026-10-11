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


@pytest.mark.parametrize('case',['normal','hold','pending-advance','pending-staged','pending-committed','two-plans','gate-first-green','gate-first-red','upgrade-drain'])
def test_actual_code_only_runner_ships_only_after_driver_acceptance(tmp_path,monkeypatch,case):
    human_hold_during_gate=case=='hold'
    upgrading=case=='upgrade-drain'
    spec=importlib.util.spec_from_file_location('entry_fixture',Path(__file__).with_name('test_pre_loop_exit_sentinel.py'))
    fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
    world=fixtures._build_world_shipped(tmp_path,with_steps=True);work=world['project']
    def git(*args):return subprocess.run(['git','-C',str(work),*args],check=True,capture_output=True,text=True)
    git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (work/'counter.py').write_text('def f(value): return value + 1\n');git('add','.');git('commit','-qm','fixture counter')
    plan=world['plans']/(fixtures.STEM+'.md')
    plan.write_text('---\nplan: '+fixtures.SLUG+'\nstatus: pending\ncurrent_step: 0\nestimated_steps: 1\nverification_tier: loop-verified\n---\n# Counter\n## Steps\n### Step 0: fix counter\n```yaml\nlocal_checks:\n  - command: python3 -I -c "from pathlib import Path;exec(Path(\'counter.py\').read_text());assert f(4)==6"\n```\n')
    master=world['plans']/'MASTER-2026-08-29-execution-plan.md'
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
cp=subprocess.run(['/bin/bash','-c',canonical_command(ctx,path,command)],capture_output=True,text=True)
assert cp.returncode==0,cp.stderr
print(json.dumps({'type':'system','subtype':'init','session_id':'fixture-session','model':'fixture-model'}))
print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':'fixture-session'}))
''');cli.chmod(0o700)
    inputs=tmp_path/'inputs';manifest=prepare(ROOT,home,inputs)
    pin_launch_resources(inputs,project=work,executables={'python':sys.executable,'claude':str(cli)})
    if upgrading:
        target_inputs=tmp_path/'upgrade-target-inputs'
        target_manifest=prepare(ROOT,home,target_inputs,revision='HEAD^')
        pin_launch_resources(target_inputs,project=work,executables={'python':sys.executable,'claude':str(cli)})
    code=Path(manifest['code_root'])
    if case.startswith('pending-'):
        from iteration_context import resolve_context,write_context
        from iteration_clock import monotonic
        import driver_step
        context=resolve_context(clone=work,exec_tree=work,observe_tree={str(work):str(work)},
            code_root=code,plans_dir=world['plans'],runtime_dir=world['data_home']/'projects'/world['key']/'runtime',
            data_root=world['data_home'],run_id='prior-interrupted',iteration=1,mode='none',
            dispatch={'slug':fixtures.SLUG,'step':0,'subplan_path':str(plan),'master_path':str(master)})
        context.update(worker_sandbox_adapter='macos-brokered-shell',toolkit_sha=manifest['toolkit_sha'])
        frozen=tmp_path/'prior-ctx.json';write_context(frozen,context)
        (work/'counter.py').write_text('def f(value): return value + 2\n')
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
            if case=='pending-advance':patch.setattr(driver_step,'transition',crash)
            else:patch.setattr(driver_step,'git',crash_git)
            with pytest.raises(RuntimeError):
                driver_step.accept_step(context,default_timeout=10,deadline=monotonic()+10)
        assert ('current_step: 1' if case=='pending-advance' else 'current_step: 0') in plan.read_text()
        assert 'status: shipped' not in plan.read_text()
    env=dict(os.environ,HOME=str(tmp_path),ILK_DATA_HOME=str(world['data_home']),ILK_DATA_DIR=str(world['data_home']),
        ILK_SKILL_HOME=str(code/'skills'),ILK_CODE_ROOT=str(code),ILK_RUN_INPUTS=str(inputs),
        CLAUDE_CONFIG_DIR=manifest['worker_home'],ILK_CLAUDE_BIN=str(cli),ILK_WORKER_MODE='code-only',
        PYTHONPATH=str(code/'skills/ilk-loop/scripts'),PATH=str(world['bin'])+os.pathsep+os.environ['PATH'])
    for key in ('ILK_SKIP_MAIN','ILK_WORKER_SESSION','ILK_ITER_CONTEXT','ILK_ITER_RECORD_STAGE'):env.pop(key,None)
    runtime=world['data_home']/'projects'/world['key']/'runtime/launcher'
    if upgrading:env['ILK_ENABLE_UPGRADE_DRAIN']='1'
    process=subprocess.Popen(['bash',str(code/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh'),
        '--project-path',str(work),'--max-iterations','2' if case=='two-plans' else '1','--iteration-timeout-min','1','--run-local-checks',
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
            return
        assert terminal['state']=='all-shipped',(terminal,stdout[-4500:],stderr[-4500:])
        assert process.returncode==0,(stdout[-4500:],stderr[-4500:])
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
        assert phases['landing']['duration_sec'] is None # ordinary consumer has no merge
        assert len(closed['driver_acceptance']['attempts'])==(2 if case=='gate-first-red' else 1)
        if case=='gate-first-green':
            assert closed['driver_acceptance']['gate_first'] and closed['driver_acceptance']['worker_turns']==[]
            assert closed['driver_acceptance']['commits']==[]
        if case=='gate-first-red':
            assert len(closed['driver_acceptance']['attempts'])==2
            assert len(closed['driver_acceptance']['worker_turns'])==1
        if case=='two-plans':
            assert len([row for row in closes if row.get('record_phase')=='close'])==2
    finally:
        if runtime.exists():
            for row in project_status(runtime)['runs']:
                if row['liveness']=='running':signal_run(runtime,row['run_id'])
        if process.poll() is None:process.kill();process.wait()
