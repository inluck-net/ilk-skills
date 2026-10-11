"""Real registered, sandboxed pytest samples retain durable exact-base logs."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
import pytest
from test_owner_driver_step import fixture,git

@pytest.mark.skipif(sys.platform!='darwin',reason='Mac owned sample adapter')
def test_two_frozen_bases_keep_full_outputs_without_acceptance(tmp_path):
    context,root,_=fixture(tmp_path)
    (root/'test_focused.py').write_text("def test_existing():\n    print('A'*3000)\n    assert False\n")
    git(root,'add','test_focused.py');git(root,'commit','-qm','red at batch base')
    batch=git(root,'rev-parse','HEAD')
    git(root,'commit','--allow-empty','-qm','next iteration base')
    context.update(base_commit=git(root,'rev-parse','HEAD'),base_tree=git(root,'rev-parse','HEAD^{tree}'))
    context['dispatch']['batch_base_sha']=batch
    request=tmp_path/'request.json';request.write_text(json.dumps(context))
    program='''import json,os,sys,time
from pathlib import Path
from iteration_context import write_context,ContextError
from iteration_clock import monotonic
from run_registry import reserve,bind_running,finish,process_identity
from base_samples import measure_nodes,load_measurement
ctx=json.loads(Path(sys.argv[1]).read_text());registry=Path(sys.argv[1]).parent/'registry'
reserve(registry,run_id=ctx['run_id'],project_key='fixture',clone=ctx['clone'],exec_tree=ctx['exec_tree'],toolkit_sha='a'*40,expires_at=time.time()+20)
bind_running(registry,ctx['run_id'],pid=os.getpid())
ctx.update(run_registry_dir=str(registry),runner_owner_pid=os.getpid(),iteration_deadline_monotonic=monotonic()+15)
path=Path(sys.argv[1]).parent/'frozen.json';write_context(path,ctx);original=path.read_bytes()
os.environ['ILK_ITER_CONTEXT']=str(path)
node='test_focused.py::test_existing'
# Refuse a compound runner before any sample/broad fallback is executed.
try:measure_nodes(ctx,sys.executable+' -m pytest; touch forbidden',[node],budget_s=8)
except ContextError:pass
else:raise AssertionError('compound runner accepted')
assert not (Path(ctx['exec_tree'])/'forbidden').exists()
row=measure_nodes(ctx,sys.executable+' -m pytest -q test_focused.py',[node],budget_s=8)
assert row['accepted'] is False and row['diagnostic_only'] is True
assert row['capture_error'] is None,row
assert len(row['measurements'])==2 and all(results=={node:'failed'} for results in row['measurements'].values()),row
assert len(row['captures'])==2,row
assert load_measurement(ctx,row['receipt'])==row
log=Path(row['captures'][0]['stdout']['path']);output=log.read_bytes();log.write_text('tampered')
try:load_measurement(ctx,row['receipt'])
except ContextError:pass
else:raise AssertionError('changed output accepted')
log.write_bytes(output)
assert path.read_bytes()==original
finish(registry,ctx['run_id'],identity=process_identity(os.getpid()))
print(row['receipt'])
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION'):env.pop(key,None)
    # Fixture judgments: 8s sample cap/15s original/20s reservation/25s outer
    # cover the previously measured sub-3s path and two real pytest starts.
    # A legitimate slower host falsifies these margins, not production caps.
    result=subprocess.run([sys.executable,'-c',program,str(request)],env=env,start_new_session=True,capture_output=True,text=True,timeout=25)
    assert result.returncode==0,result.stdout+result.stderr
    path=Path(result.stdout.strip().splitlines()[-1]);row=json.loads(path.read_text())
    for capture in row['captures']:
        assert capture['sample_head']==capture['base_commit'] and capture['sample_unchanged'] is True
        stdout=Path(capture['stdout']['path']).read_bytes()
        assert b'A'*3000 in stdout
        for channel in ('stdout','stderr'):
            artifact=capture[channel]
            assert hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest()==artifact['sha256']
    assert git(root,'status','--porcelain')==''
