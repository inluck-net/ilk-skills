"""Two actual archived owned launches retain and repair the same red diff."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from run_inputs import prepare,pin_launch_resources
from run_registry import project_status,signal_run

ROOT=Path(__file__).resolve().parents[3]
pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='Mac production entry')


@pytest.mark.parametrize('fresh_inputs',[False,True])
def test_closed_red_launch_is_resumed_with_fresh_driver_gate(tmp_path,fresh_inputs):
    spec=importlib.util.spec_from_file_location('red_fixture',Path(__file__).with_name('test_pre_loop_exit_sentinel.py'))
    fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
    world=fixtures._build_world_shipped(tmp_path,with_steps=True);work=world['project']
    def git(*args):return subprocess.check_output(['git','-C',str(work),*args],text=True).strip()
    git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (work/'counter.py').write_text('value = 1\n');git('add','.');git('commit','-qm','fixture counter');base=git('rev-parse','HEAD')
    plan=world['plans']/(fixtures.STEM+'.md')
    plan.write_text('---\nplan: '+fixtures.SLUG+'\nstatus: pending\ncurrent_step: 0\nestimated_steps: 1\nverification_tier: loop-verified\n---\n# Counter\n## Steps\n### Step 0: fix counter\n```yaml\nlocal_checks:\n  - command: python3 -I -c "from pathlib import Path;assert Path(\'counter.py\').read_text()==\'value = 2\\n\'"\n```\n')
    home=tmp_path/'source-home';home.mkdir();(home/'settings.json').write_text(json.dumps({'env':{'ANTHROPIC_AUTH_TOKEN':'fixture-token','ANTHROPIC_BASE_URL':'https://example.invalid','ANTHROPIC_MODEL':'fixture-model'}}))
    (home/'CLAUDE.md').write_text('unchanged global instruction')
    cli=world['bin']/'claude'
    cli.write_text('#!'+sys.executable+'\n'+'''import json,os
from pathlib import Path
from iteration_context import read_context
context=read_context(Path(os.environ['ILK_ITER_CONTEXT']))
# Same immutable executable and settings in both launches. Initial turn
# deliberately stays red; only an admitted retained entry is repaired.
Path('counter.py').write_text('value = '+('2' if context.get('retained_red_entry') else '0')+'\\n')
print(json.dumps({'type':'system','subtype':'init','session_id':'fixture','model':'fixture-model'}))
print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':'fixture'}))
''');cli.chmod(0o700)
    inputs=tmp_path/'inputs';manifest=prepare(ROOT,home,inputs,project=work,worker_tools='native')
    pin_launch_resources(inputs,project=work,executables={'python':sys.executable,'claude':str(cli)})
    code=Path(manifest['code_root']);env=dict(os.environ,HOME=str(tmp_path),ILK_DATA_HOME=str(world['data_home']),ILK_DATA_DIR=str(world['data_home']),ILK_SKILL_HOME=str(code/'skills'),ILK_CODE_ROOT=str(code),ILK_RUN_INPUTS=str(inputs),ILK_WORKER_MODE='code-only',CLAUDE_CONFIG_DIR=manifest['worker_home'],ILK_CLAUDE_BIN=str(cli),PYTHONPATH=str(code/'skills/ilk-loop/scripts'),PATH=str(world['bin'])+os.pathsep+os.environ['PATH'])
    for name in ('ILK_SKIP_MAIN','ILK_ITER_CONTEXT','ILK_WORKER_SESSION','ILK_ITER_RECORD_STAGE'):env.pop(name,None)
    argv=['bash',str(code/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh'),'--project-path',str(work),'--max-iterations','1','--iteration-timeout-min','1','--run-local-checks','--mcp-config-path',str(inputs/'mcp.json')]
    runtime=world['data_home']/'projects'/world['key']/'runtime/launcher'
    try:
        # Existing entry fixture's 40s startup/close bound, not a new runner cap.
        first=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=40,start_new_session=True)
        terminal=json.loads((runtime/'last-exit.json').read_text());assert terminal['state']=='local_checks_failed',(terminal,first.stdout[-3000:],first.stderr[-3000:])
        assert git('rev-parse','HEAD')==base and (work/'counter.py').read_text()=='value = 0\n'
        assert 'current_step: 0' in plan.read_text() and not project_status(runtime)['busy']
        if fresh_inputs:
            inputs=tmp_path/'fresh-inputs';manifest=prepare(ROOT,home,inputs,project=work,worker_tools='native')
            pin_launch_resources(inputs,project=work,executables={'python':sys.executable,'claude':str(cli)})
            code=Path(manifest['code_root'])
            env.update(ILK_SKILL_HOME=str(code/'skills'),ILK_CODE_ROOT=str(code),ILK_RUN_INPUTS=str(inputs),
                CLAUDE_CONFIG_DIR=manifest['worker_home'],PYTHONPATH=str(code/'skills/ilk-loop/scripts'))
            argv[1]=str(code/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh')
            argv[-1]=str(inputs/'mcp.json')
        second=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=40,start_new_session=True)
        terminal=json.loads((runtime/'last-exit.json').read_text());assert terminal['state']=='all-shipped',(terminal,second.stdout[-5000:],second.stderr[-5000:])
        assert git('rev-parse','HEAD')!=base and 'current_step: 1' in plan.read_text()
        rows=[json.loads(line) for line in (runtime.parent.parent/'logs/.ilk-loop.log').read_text().splitlines()]
        closes=[row for row in rows if row.get('record_phase')=='close'];assert len(closes)==2
        assert closes[1]['context']['entry_dirty'] is True and closes[1]['context']['retained_red_entry']['diagnostic_only'] is True
        assert closes[1]['driver_acceptance']['attempts'][0]['attempt_id'] not in {row['attempt_id'] for row in closes[0]['driver_acceptance']['attempts']}
        assert closes[1]['driver_integrity_passed'] is True and not project_status(runtime)['busy']
    finally:
        for row in project_status(runtime)['runs']:
            if row['liveness']=='running':signal_run(runtime,row['run_id'])
