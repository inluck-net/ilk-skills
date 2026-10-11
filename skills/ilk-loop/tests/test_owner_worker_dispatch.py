from iteration_clock import monotonic
"""Production preparation API in an exact owned, disposable runner session."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from run_inputs import prepare,pin_launch_resources
import test_owner_driver_step as fixtures

pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='explicit Mac ownership and sandbox adapter')
ROOT=Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('scope_probe',[False,True])
def test_owned_prepare_freezes_once_binds_settings_and_registers_shell(tmp_path,scope_probe):
    context,work,plan=fixtures.fixture(tmp_path)
    forged=Path(context['runtime_dir'])/'forged-proof.txt'
    if scope_probe:
        import re,shlex
        attempt='try:\n Path('+repr(str(forged))+').write_text("forged")\nexcept PermissionError:\n pass'
        command='python3 -I -c '+shlex.quote('from pathlib import Path;exec('+repr(attempt)+');exec(Path("counter.py").read_text());assert f(4)==6')
        plan.write_text(re.sub(r'(?m)^  - command:.*$',lambda _: '  - command: '+command,plan.read_text()))
        from iteration_context import plan_snapshot
        context['plans'][plan.name]=plan_snapshot(plan)
    toolkit=tmp_path/'toolkit';(toolkit/'skills/ilk-loop').mkdir(parents=True)
    shutil.copytree(ROOT/'skills/ilk-loop/scripts',toolkit/'skills/ilk-loop/scripts',ignore=shutil.ignore_patterns('__pycache__'))
    (toolkit/'hooks').mkdir();shutil.copy(ROOT/'hooks/worker-write-scope.py',toolkit/'hooks/worker-write-scope.py')
    subprocess.run(['git','init','-q',str(toolkit)],check=True)
    subprocess.run(['git','-C',str(toolkit),'add','.'],check=True)
    subprocess.run(['git','-C',str(toolkit),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','candidate snapshot'],check=True)
    home=tmp_path/'home';home.mkdir();(home/'settings.json').write_text('{"env":{}}')
    inputs=tmp_path/'inputs';manifest=prepare(toolkit,home,inputs)
    context.update(code_root=manifest['code_root'],toolkit_sha=manifest['toolkit_sha'])
    request=tmp_path/'request.json';request.write_text(json.dumps(context))
    ctx=tmp_path/'ctx.json';registry=tmp_path/'registry';result=tmp_path/'result.json'
    provider=tmp_path/'fixture-cli'
    provider.write_text('#!'+sys.executable+'\n'+'''import json,os,subprocess,sys
from pathlib import Path
from iteration_context import read_context
from worker_shell import canonical_command
path=Path(os.environ['ILK_ITER_CONTEXT']);context=read_context(path)
counter=Path(context['exec_tree'])/'counter.py'
repair='value + 0' in counter.read_text()
assert ('--resume' in sys.argv)==repair
command=canonical_command(context,path,"printf 'def f(value): return value + "+('2' if repair else '0')+"\\n' > counter.py")
cp=subprocess.run(['/bin/bash','-c',command],capture_output=True,text=True)
assert cp.returncode==0,cp.stderr
print(json.dumps({'type':'system','subtype':'init','session_id':'fixture-session','model':'fixture'}))
print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'## Keep this diagnostic heading quoted'}]}}))
print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':'fixture-session'}))
''');provider.chmod(0o700)
    pin_launch_resources(inputs,project=work,executables={'python':sys.executable,'claude':str(provider)})
    script='''
import json,os,sys,time
from pathlib import Path
from iteration_clock import monotonic
from run_registry import reserve,bind_running,scan,finish,process_identity
from worker_dispatch import prepare,close
from worker_shell import canonical_command
from driver_step import accept_step
import subprocess
request,ctx,inputs,registry,result,provider=map(Path,sys.argv[1:])
context=json.loads(request.read_text());deadline=monotonic()+15
reserve(registry,run_id=context['run_id'],project_key='fixture',clone=context['clone'],exec_tree=context['exec_tree'],toolkit_sha=context['toolkit_sha'],expires_at=time.time()+15)
bind_running(registry,context['run_id'],pid=os.getpid())
binding=prepare(context,ctx,inputs=inputs,deadline=deadline,registry=registry,owner_pid=os.getpid())
frozen=json.loads(ctx.read_text());original=ctx.read_bytes()
env=dict(os.environ,ILK_ITER_CONTEXT=str(ctx))
# Execute the real admission hook inside the actual returned CLI policy.
# A provider stub alone bypasses hooks and missed this diagnostic-path defect.
hook=[sys.executable,str(Path(frozen['code_root'])/'hooks/worker-write-scope.py'),
      '--mode','enforce','--context',str(ctx)]
event={'tool_name':'Bash','tool_input':{'command':canonical_command(frozen,ctx,'pwd')}}
observed=subprocess.run(['/usr/bin/sandbox-exec','-f',binding['sandbox_path'],*hook],
    input=json.dumps(event),env=env,capture_output=True,text=True)
assert observed.returncode==0,observed.stderr
diagnostic=ctx.parent/f"write-scope-{frozen['iteration']}.jsonl"
assert json.loads(diagnostic.read_text().splitlines()[-1])['diagnostic_only']
for target in (ctx,ctx.parent/'forged-proof.json'):
    denied=subprocess.run(['/usr/bin/sandbox-exec','-f',binding['sandbox_path'],sys.executable,
        '-c','from pathlib import Path;import sys;Path(sys.argv[1]).write_text("forged")',str(target)],
        capture_output=True,text=True)
    assert denied.returncode!=0
assert ctx.read_bytes()==original
from worker_dispatch import binding as read_binding
from run_inputs import InputPinError
for protected in (Path(binding['settings_path']),Path(binding['sandbox_path'])):
    before=protected.read_bytes();protected.chmod(0o600)
    if protected.suffix=='.json':
        altered=json.loads(before);altered['env']['ANTHROPIC_BASE_URL']='https://foreign.invalid'
        protected.write_text(json.dumps(altered))
    else:protected.write_text(before.decode()+'(allow file-write*)\\n')
    try:
        read_binding(ctx)
        raise AssertionError('changed private settings/policy admitted')
    except InputPinError as exc:assert 'pinned run input changed' in str(exc)
    finally:protected.write_bytes(before);protected.chmod(0o400)
assert read_binding(ctx)['sandbox_path']==binding['sandbox_path']
cp=subprocess.run([sys.executable,str(Path(frozen['code_root'])/'skills/ilk-loop/scripts/worker_iteration.py'),
    '--context',str(ctx),'--prompt','implement selected step','--claude',str(provider),'--gate-timeout','10',
    '--mcp',str(inputs/'mcp.json')],
    env=env,capture_output=True,text=True)
assert cp.returncode==0,cp.stderr
assert ctx.read_bytes()==original
rows=scan(registry)
events=[json.loads(line) for line in (registry/'child-events.jsonl').read_text().splitlines()]
registered=[event for event in events if event['event']=='registered'];assert len(registered)>=5
assert all(event['identity']['pid']==event['identity']['pgid'] and event['identity']['lstart'] for event in registered)
assert all(child['liveness'] in {'running','dead'} for child in rows[0]['children_liveness'])
assert any(child['liveness']=='running' for child in rows[0]['children_liveness'])
accepted=json.loads((ctx.parent/(ctx.stem+'-acceptance.json')).read_text())
assert accepted['accepted'] and len(accepted['attempts'])==2
assert not accepted['attempts'][0]['all_passed'] and accepted['attempts'][1]['all_passed']
assert len(accepted['commits'])==1 and len(accepted['worker_turns'])==2
assert accepted['worker_turns'][0]['session_id']==accepted['worker_turns'][1]['session_id']
close(ctx)
while any(child['liveness']=='running' for child in scan(registry)[0]['children_liveness']):
    assert monotonic()<deadline
    time.sleep(.02)
finish(registry,context['run_id'],identity=process_identity(os.getpid()))
result.write_text(json.dumps({'accepted':accepted['accepted'],'children':len(registered),'scope':binding,'frozen':original.decode()}))
'''
    env=dict(os.environ,PYTHONPATH=str(Path(manifest['code_root'])/'skills/ilk-loop/scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION','ILK_RUN_INPUTS'):env.pop(key,None)
    # 20s is fixture-only judgment over the 15s existing supplied deadline;
    # a valid host needing more startup time falsifies this fixture bound.
    cp=subprocess.run([sys.executable,'-c',script,str(request),str(ctx),str(inputs),str(registry),str(result),str(provider)],
                      env=env,capture_output=True,text=True,start_new_session=True,timeout=20)
    assert cp.returncode==0,cp.stderr
    row=json.loads(result.read_text());assert row['accepted'] and row['children']>=5
    assert ctx.stat().st_mode & 0o222==0
    assert 'current_step: 1' in plan.read_text()
    assert '> ## Keep this diagnostic heading quoted' in plan.read_text()
    assert (home/'settings.json').read_text()=='{"env":{}}'
    assert not forged.exists(), 'gate child wrote outside the execution worktree'


def test_unowned_preparation_refuses_before_spawning(tmp_path):
    from worker_dispatch import prepare as dispatch_prepare
    from iteration_context import ContextError
    context,work,plan=fixtures.fixture(tmp_path)
    with pytest.raises(ContextError,match='iteration deadline'):
        dispatch_prepare(dict(context,entry_dirty=True),tmp_path/'ctx.json',inputs=tmp_path/'missing',
                         deadline=0,registry=tmp_path/'registry',owner_pid=os.getpid())
    assert not (tmp_path/'ctx.json').exists()
