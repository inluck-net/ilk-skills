"""A real owned baseline child writes its sample, never the live/control trees."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from test_owner_driver_step import fixture,git

@pytest.mark.skipif(sys.platform!='darwin',reason='Mac sample sandbox adapter')
def test_owned_base_sample_protects_control_and_live_roots(tmp_path):
    # Fixture judgments: 5s sample, 10s original deadline, 15s reservation,
    # 20s outer bound cover the observed sub-2s Git/pytest/registry path.
    # Legitimate slow-host completion beyond these margins falsifies them;
    # they change no production default.
    context,root,plan=fixture(tmp_path)
    (root/'test_focused.py').write_text('''import os
from pathlib import Path
def test_existing():
    Path('sample-output').write_text('sample')
    for name in ('CONTROL_PLAN','LIVE_CODE'):
        try: Path(os.environ[name]).write_text('forged')
        except PermissionError: pass
        else: raise AssertionError('outside sample was writable')
    assert False
''')
    git(root,'add','test_focused.py');git(root,'commit','-qm','sample fixture')
    context['base_commit']=git(root,'rev-parse','HEAD')
    context['base_tree']=git(root,'rev-parse','HEAD^{tree}')
    request=tmp_path/'request.json';request.write_text(json.dumps(context))
    before=plan.read_bytes();code=(root/'counter.py').read_bytes()
    program='''import json,os,sys,time
from pathlib import Path
from iteration_context import write_context
from run_registry import reserve,bind_running,finish,process_identity
from iteration_clock import monotonic
from red_owner import _run_at_base_worktree
ctx=json.loads(Path(sys.argv[1]).read_text());registry=Path(sys.argv[1]).parent/'registry'
reserve(registry,run_id=ctx['run_id'],project_key='fixture',clone=ctx['clone'],exec_tree=ctx['exec_tree'],toolkit_sha='a'*40,expires_at=time.time()+15)
bind_running(registry,ctx['run_id'],pid=os.getpid())
ctx.update(run_registry_dir=str(registry),runner_owner_pid=os.getpid(),iteration_deadline_monotonic=monotonic()+10)
path=Path(sys.argv[1]).parent/'frozen.json';write_context(path,ctx);original=path.read_bytes()
os.environ.update(ILK_ITER_CONTEXT=str(path),CONTROL_PLAN=ctx['dispatch']['subplan_path'],LIVE_CODE=str(Path(ctx['exec_tree'])/'counter.py'))
node='test_focused.py::test_existing'
result=_run_at_base_worktree(Path(ctx['exec_tree']),ctx['base_commit'],sys.executable+' -m pytest -q test_focused.py',[node],5)
assert result[node]=='failed',result
assert path.read_bytes()==original
finish(registry,ctx['run_id'],identity=process_identity(os.getpid()))
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION'):env.pop(key,None)
    result=subprocess.run([sys.executable,'-c',program,str(request)],env=env,
        capture_output=True,text=True,start_new_session=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert plan.read_bytes()==before
    assert (root/'counter.py').read_bytes()==code


def test_sandbox_metadata_uses_existing_frozen_deadline(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import worker_sandbox
    from iteration_clock import monotonic
    from test_owner_worker_sandbox import fixture as boundary_fixture
    context,scratch,_=boundary_fixture(tmp_path)
    # Two seconds is a fixture judgment for metadata spy setup, not a new
    # production cap; a setup taking that long falsifies the fixture margin.
    context['iteration_deadline_monotonic']=monotonic()+2
    observed=[]
    monkeypatch.setattr(worker_sandbox.subprocess,'run',lambda *a,**kw: observed.append(kw) or SimpleNamespace(returncode=0,stdout='.git'))
    worker_sandbox.profile(context,temporary=scratch,shell=True)
    assert len(observed)==1 and 0<observed[0]['timeout']<=2


def test_expired_sandbox_metadata_does_not_start_git(tmp_path,monkeypatch):
    import worker_sandbox
    from iteration_clock import monotonic
    from iteration_context import ContextError
    from test_owner_worker_sandbox import fixture as boundary_fixture
    context,scratch,_=boundary_fixture(tmp_path)
    context['iteration_deadline_monotonic']=monotonic()-1
    monkeypatch.setattr(worker_sandbox.subprocess,'run',lambda *a,**kw:pytest.fail('expired scope started Git'))
    with pytest.raises(ContextError,match='deadline'):
        worker_sandbox.profile(context,temporary=scratch,shell=True)
