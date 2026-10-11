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


@pytest.mark.parametrize('human_hold_during_gate',[False,True])
def test_actual_code_only_runner_ships_only_after_driver_acceptance(tmp_path,human_hold_during_gate):
    spec=importlib.util.spec_from_file_location('entry_fixture',Path(__file__).with_name('test_pre_loop_exit_sentinel.py'))
    fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
    world=fixtures._build_world_shipped(tmp_path,with_steps=True);work=world['project']
    def git(*args):return subprocess.run(['git','-C',str(work),*args],check=True,capture_output=True,text=True)
    git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (work/'counter.py').write_text('def f(value): return value + 1\n');git('add','.');git('commit','-qm','fixture counter')
    plan=world['plans']/(fixtures.STEM+'.md')
    plan.write_text('---\nplan: '+fixtures.SLUG+'\nstatus: pending\ncurrent_step: 0\nestimated_steps: 1\nverification_tier: loop-verified\n---\n# Counter\n## Steps\n### Step 0: fix counter\n```yaml\nlocal_checks:\n  - command: python3 -I -c "from pathlib import Path;exec(Path(\'counter.py\').read_text());assert f(4)==6"\n```\n')
    master=world['plans']/'MASTER-2026-08-29-execution-plan.md'
    if human_hold_during_gate:
        # Driver-owned gate seam models an external coordinator's hold during
        # the turn. A worker cannot write this path under the CLI policy.
        import shlex
        action='from pathlib import Path;p=Path('+repr(str(master))+');p.write_text(p.read_text().replace("status: active","status: blocked\\nhold: human"))'
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
path=Path(os.environ['ILK_ITER_CONTEXT']);ctx=read_context(path)
cp=subprocess.run(['/bin/bash','-c',canonical_command(ctx,path,"printf 'def f(value): return value + 2\\n' > counter.py")],capture_output=True,text=True)
assert cp.returncode==0,cp.stderr
print(json.dumps({'type':'system','subtype':'init','session_id':'fixture-session','model':'fixture-model'}))
print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':'fixture-session'}))
''');cli.chmod(0o700)
    inputs=tmp_path/'inputs';manifest=prepare(ROOT,home,inputs)
    pin_launch_resources(inputs,project=work,executables={'python':sys.executable,'claude':str(cli)})
    code=Path(manifest['code_root'])
    env=dict(os.environ,HOME=str(tmp_path),ILK_DATA_HOME=str(world['data_home']),ILK_DATA_DIR=str(world['data_home']),
        ILK_SKILL_HOME=str(code/'skills'),ILK_CODE_ROOT=str(code),ILK_RUN_INPUTS=str(inputs),
        CLAUDE_CONFIG_DIR=manifest['worker_home'],ILK_CLAUDE_BIN=str(cli),ILK_WORKER_MODE='code-only',
        PYTHONPATH=str(code/'skills/ilk-loop/scripts'),PATH=str(world['bin'])+os.pathsep+os.environ['PATH'])
    for key in ('ILK_SKIP_MAIN','ILK_WORKER_SESSION','ILK_ITER_CONTEXT','ILK_ITER_RECORD_STAGE'):env.pop(key,None)
    runtime=world['data_home']/'projects'/world['key']/'runtime/launcher'
    process=subprocess.Popen(['bash',str(code/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh'),
        '--project-path',str(work),'--max-iterations','1','--iteration-timeout-min','1','--run-local-checks',
        '--mcp-config-path',str(inputs/'mcp.json')],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        start_new_session=True)
    try:
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
            return
        assert terminal['state']=='all-shipped',(terminal,stdout[-4500:],stderr[-4500:])
        assert process.returncode==0,(stdout[-4500:],stderr[-4500:])
        assert 'current_step: 1' in plan.read_text() and 'status: shipped' in plan.read_text()
        assert (home/'settings.json').read_bytes()==original
        assert not project_status(runtime)['busy']
        closes=[json.loads(line) for line in (world['data_home']/'projects'/world['key']/'logs/.ilk-loop.log').read_text().splitlines()]
        closed=[row for row in closes if row.get('record_phase')=='close'][0]
        assert closed['driver_acceptance']['accepted'] is True
        assert closed['driver_integrity_passed'] is True and closed['state_audit']['explained'] is True
        assert len(closed['driver_acceptance']['attempts'])==1
    finally:
        if runtime.exists():
            for row in project_status(runtime)['runs']:
                if row['liveness']=='running':signal_run(runtime,row['run_id'])
        if process.poll() is None:process.kill();process.wait()
