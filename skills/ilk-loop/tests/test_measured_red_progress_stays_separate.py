"""Actual owned head/base gates: eligible progress is never green acceptance."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from test_owner_driver_step import fixture,git

@pytest.mark.skipif(sys.platform!='darwin',reason='Mac owned progress observation adapter')
@pytest.mark.parametrize('case',['pre-existing','owned','runner-error'])
def test_real_gate_and_bases_keep_distinct_decisions(tmp_path,case):
    command=sys.executable+' -m pytest -q test_focused.py'
    context,root,plan=fixture(tmp_path,command)
    contents={'pre-existing':'def test_existing():\n    assert False\n',
              'owned':'from counter import f\ndef test_existing():\n    assert f(4)==5\n',
              'runner-error':'import nonexistent_fixture_module\ndef test_existing():\n    assert False\n'}
    (root/'test_focused.py').write_text(contents[case])
    git(root,'add','test_focused.py');git(root,'commit','-qm','batch fixture')
    batch=git(root,'rev-parse','HEAD')
    git(root,'commit','--allow-empty','-qm','iteration fixture')
    context.update(base_commit=git(root,'rev-parse','HEAD'),base_tree=git(root,'rev-parse','HEAD^{tree}'))
    context['dispatch']['batch_base_sha']=batch
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    request=tmp_path/'request.json';request.write_text(json.dumps(context))
    before=plan.read_bytes();head=git(root,'rev-parse','HEAD')
    program='''import json,os,sys,time
from pathlib import Path
from iteration_context import write_context,read_dispatch
from iteration_clock import monotonic
from run_registry import reserve,bind_running,finish,process_identity
from driver_step import gate
from measured_progress import assess
ctx=json.loads(Path(sys.argv[1]).read_text());registry=Path(sys.argv[1]).parent/'registry'
reserve(registry,run_id=ctx['run_id'],project_key='fixture',clone=ctx['clone'],exec_tree=ctx['exec_tree'],toolkit_sha='a'*40,expires_at=time.time()+35)
bind_running(registry,ctx['run_id'],pid=os.getpid())
ctx.update(run_registry_dir=str(registry),runner_owner_pid=os.getpid(),iteration_deadline_monotonic=monotonic()+30)
path=Path(sys.argv[1]).parent/'frozen.json';write_context(path,ctx)
os.environ['ILK_ITER_CONTEXT']=str(path)
selected=read_dispatch(ctx)
attempt=gate(ctx,Path(ctx['exec_tree']),Path(selected['subplan_path']),selected,default_timeout=15)
row=assess(ctx,attempt,budget_s=15)
assert row['accepted'] is False and row['green_accepted'] is False,row
assert row['progress_eligible'] is (sys.argv[2]=='pre-existing'),row
finish(registry,ctx['run_id'],identity=process_identity(os.getpid()))
print(json.dumps(row))
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION'):env.pop(key,None)
    # 15s gate/sample, 30s original, 35s reservation, 40s outer are fixture
    # judgments for the measured sub-6s two-sample path plus a head gate.
    # A legitimate slower host falsifies these margins; no default changes.
    result=subprocess.run([sys.executable,'-c',program,str(request),case],env=env,start_new_session=True,capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    assert plan.read_bytes()==before and git(root,'rev-parse','HEAD')==head
