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


def test_owned_prepare_freezes_once_binds_settings_and_registers_shell(tmp_path):
    context,work,plan=fixtures.fixture(tmp_path)
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
from run_registry import reserve,bind_running,scan,finish,process_identity
from worker_dispatch import prepare,close
from worker_shell import canonical_command
from driver_step import accept_step
import subprocess
request,ctx,inputs,registry,result,provider=map(Path,sys.argv[1:])
context=json.loads(request.read_text());deadline=time.monotonic()+15
reserve(registry,run_id=context['run_id'],project_key='fixture',clone=context['clone'],exec_tree=context['exec_tree'],toolkit_sha=context['toolkit_sha'],expires_at=time.time()+15)
bind_running(registry,context['run_id'],pid=os.getpid())
binding=prepare(context,ctx,inputs=inputs,deadline=deadline,registry=registry,owner_pid=os.getpid())
frozen=json.loads(ctx.read_text());original=ctx.read_bytes()
env=dict(os.environ,ILK_ITER_CONTEXT=str(ctx))
cp=subprocess.run([sys.executable,str(Path(frozen['code_root'])/'skills/ilk-loop/scripts/worker_iteration.py'),
    '--context',str(ctx),'--prompt','implement selected step','--claude',str(provider),'--gate-timeout','10',
    '--mcp',str(inputs/'mcp.json')],
    env=env,capture_output=True,text=True)
assert cp.returncode==0,cp.stderr
assert ctx.read_bytes()==original
rows=scan(registry);assert len(rows[0]['children'])==5
accepted=json.loads((ctx.parent/(ctx.stem+'-acceptance.json')).read_text())
assert accepted['accepted'] and len(accepted['attempts'])==2
assert not accepted['attempts'][0]['all_passed'] and accepted['attempts'][1]['all_passed']
assert len(accepted['commits'])==1 and len(accepted['worker_turns'])==2
assert accepted['worker_turns'][0]['session_id']==accepted['worker_turns'][1]['session_id']
close(ctx)
while scan(registry)[0]['children_liveness'][0]['liveness']=='running':
    assert time.monotonic()<deadline
    time.sleep(.02)
finish(registry,context['run_id'],identity=process_identity(os.getpid()))
result.write_text(json.dumps({'accepted':accepted['accepted'],'children':len(rows[0]['children']),'scope':binding,'frozen':original.decode()}))
'''
    env=dict(os.environ,PYTHONPATH=str(Path(manifest['code_root'])/'skills/ilk-loop/scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION','ILK_RUN_INPUTS'):env.pop(key,None)
    # 20s is fixture-only judgment over the 15s existing supplied deadline;
    # a valid host needing more startup time falsifies this fixture bound.
    cp=subprocess.run([sys.executable,'-c',script,str(request),str(ctx),str(inputs),str(registry),str(result),str(provider)],
                      env=env,capture_output=True,text=True,start_new_session=True,timeout=20)
    assert cp.returncode==0,cp.stderr
    row=json.loads(result.read_text());assert row['accepted'] and row['children']==5
    assert ctx.stat().st_mode & 0o222==0
    assert 'current_step: 1' in plan.read_text()
    assert '> ## Keep this diagnostic heading quoted' in plan.read_text()
    assert (home/'settings.json').read_text()=='{"env":{}}'


def test_unowned_preparation_refuses_before_spawning(tmp_path):
    from worker_dispatch import prepare as dispatch_prepare
    from iteration_context import ContextError
    context,work,plan=fixtures.fixture(tmp_path)
    with pytest.raises(ContextError,match='clean frozen entry'):
        dispatch_prepare(dict(context,entry_dirty=True),tmp_path/'ctx.json',inputs=tmp_path/'missing',
                         deadline=0,registry=tmp_path/'registry',owner_pid=os.getpid())
    assert not (tmp_path/'ctx.json').exists()
