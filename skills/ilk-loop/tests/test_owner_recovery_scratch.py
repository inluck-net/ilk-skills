"""A recovering driver gates the old tree after its broker removed scratch."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from test_owner_driver_step import fixture


@pytest.mark.skipif(sys.platform!='darwin',reason='Mac gate boundary')
def test_scoped_gate_works_after_original_scratch_is_removed(tmp_path):
    context,root,_=fixture(tmp_path)
    context.update(worker_tmp_dir=str(tmp_path/'gone'),worker_sandbox_adapter='macos-brokered-shell')
    request=tmp_path/'request.json';request.write_text(json.dumps(context))
    script='''
import json,os,sys,time
from pathlib import Path
from run_registry import reserve,bind_running,finish,process_identity
from iteration_context import write_context
from bounded_run import run
ctx=json.loads(Path(sys.argv[1]).read_text());registry=Path(sys.argv[1]).parent/'registry'
reserve(registry,run_id=ctx['run_id'],project_key='fixture',clone=ctx['clone'],exec_tree=ctx['exec_tree'],toolkit_sha='a'*40,expires_at=time.time()+10)
bind_running(registry,ctx['run_id'],pid=os.getpid())
ctx.update(run_registry_dir=str(registry),runner_owner_pid=os.getpid())
path=Path(sys.argv[1]).parent/'frozen.json';write_context(path,ctx);original=path.read_bytes()
os.environ['ILK_ITER_CONTEXT']=str(path)
command="import os;from pathlib import Path;p=Path(os.environ['TMPDIR']);(p/'gate').write_text('ok');print(p)"
rc,out,err,timed=run([sys.executable,'-c',command],cwd=ctx['exec_tree'],timeout=5,protect_control_plane=True)
assert rc==0 and not timed,(rc,out,err)
assert out.strip()!=ctx['worker_tmp_dir'] and not Path(out.strip()).exists()
assert path.read_bytes()==original
finish(registry,ctx['run_id'],identity=process_identity(os.getpid()))
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION'):env.pop(key,None)
    result=subprocess.run([sys.executable,'-c',script,str(request)],env=env,
                          capture_output=True,text=True,start_new_session=True,timeout=15)
    assert result.returncode==0,result.stderr
