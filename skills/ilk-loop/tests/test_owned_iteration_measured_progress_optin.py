"""Owned worker-iteration orchestration with a stub turn and actual red gates."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from test_owner_driver_step import fixture,git

@pytest.mark.skipif(sys.platform!='darwin',reason='Mac owned iteration adapter')
def test_optin_iteration_advances_once_without_retry_or_green_claim(tmp_path):
    context,root,plan=fixture(tmp_path,sys.executable+' -m pytest -q test_focused.py')
    (root/'test_focused.py').write_text('def test_existing():\n    assert False\n')
    git(root,'add','test_focused.py');git(root,'commit','-qm','existing red')
    context.update(base_commit=git(root,'rev-parse','HEAD'),base_tree=git(root,'rev-parse','HEAD^{tree}'))
    context['dispatch']['batch_base_sha']=context['base_commit']
    context['measured_red_progress_protocol']=1
    request=tmp_path/'request.json';request.write_text(json.dumps(context))
    program=r'''import json,os,sys,time
from pathlib import Path
from iteration_context import write_context
from iteration_clock import monotonic
from run_registry import reserve,bind_running,finish,process_identity
from worker_iteration import run
ctx=json.loads(Path(sys.argv[1]).read_text());registry=Path(sys.argv[1]).parent/'registry'
reserve(registry,run_id=ctx['run_id'],project_key='fixture',clone=ctx['clone'],exec_tree=ctx['exec_tree'],toolkit_sha='a'*40,expires_at=time.time()+35)
bind_running(registry,ctx['run_id'],pid=os.getpid())
ctx.update(run_registry_dir=str(registry),runner_owner_pid=os.getpid(),iteration_deadline_monotonic=monotonic()+30)
path=Path(sys.argv[1]).parent/'frozen.json';write_context(path,ctx);os.environ['ILK_ITER_CONTEXT']=str(path)
turns=[]
def turn(context,path,prompt,**kwargs):
    turns.append(prompt)
    (Path(context['exec_tree'])/'counter.py').write_text('def f(value): return value + 2\n')
    return dict(completed=True,session_id='fixture',findings='')
row=run(ctx,path,'fixture',claude='unused',model='none',budget=0,mcp=None,gate_timeout=15,turn=turn)
assert len(turns)==1 and row['progress_accepted'] is True and row['accepted'] is False and row['green_accepted'] is False,row
assert row['attempts'][-1]['all_passed'] is False
from driver_step import project_result
from iteration_context import ContextError
projection=project_result(row,ctx)
assert projection['outcome']=='fail' and projection['accepted_tree'] is None and projection['progress_accepted'] is True,projection
try:project_result(row,dict(ctx,measured_red_progress_protocol=None))
except ContextError:pass
else:raise AssertionError('progress projection allowed without frozen opt-in')
assert 'status: shipped' in Path(ctx['dispatch']['subplan_path']).read_text()
assert 'current_step: 1' in Path(ctx['dispatch']['subplan_path']).read_text()
finish(registry,ctx['run_id'],identity=process_identity(os.getpid()))
print('one stub turn; actual failed gates; progress accepted; green false')
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION'):env.pop(key,None)
    # Fixture judgments reuse 15/30/35/40s gate/original/reservation/outer.
    # The measured ~6s core plus marker path fits; legitimate slower-host
    # completion beyond this margin falsifies it. No production cap changes.
    result=subprocess.run([sys.executable,'-c',program,str(request)],env=env,start_new_session=True,capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'green false' in result.stdout
    assert git(root,'rev-list','--count',context['base_commit']+'..HEAD')=='2'
