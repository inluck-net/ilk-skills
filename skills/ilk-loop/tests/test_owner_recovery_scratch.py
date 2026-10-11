"""A recovering driver gates the old tree after its broker removed scratch."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from test_owner_driver_step import fixture


@pytest.mark.skipif(sys.platform!='darwin',reason='Mac gate boundary')
@pytest.mark.parametrize("isolated_test",[False,True])
def test_scoped_gate_works_after_original_scratch_is_removed(tmp_path,isolated_test):
    context,root,_=fixture(tmp_path)
    context.update(worker_tmp_dir=str(tmp_path/'gone'),worker_sandbox_adapter='macos-brokered-shell')
    request=tmp_path/'request.json';request.write_text(json.dumps(context))
    (root/'test_environment.py').write_text('''import os
from pathlib import Path
def test_environment():
    scratch=Path(os.environ['TMPDIR']).resolve()
    assert Path.home().resolve().is_relative_to(scratch)
    assert os.environ['ILK_DATA_HOME']==os.environ['ILK_DATA_DIR']
    assert Path(os.environ['ILK_DATA_HOME']).resolve().is_relative_to(scratch)
    assert not os.environ.get('ILK_ITER_CONTEXT')
    (Path.home()/'gate.txt').write_text('private')
''')
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
isolated=sys.argv[2]=='True'
argv=[sys.executable,'-m','pytest','-q','test_environment.py'] if isolated else [sys.executable,'-c',command]
rc,out,err,timed=run(argv,cwd=ctx['exec_tree'],timeout=5,protect_control_plane=True)
assert rc==0 and not timed,(rc,out,err)
if not isolated:assert out.strip()!=ctx['worker_tmp_dir'] and not Path(out.strip()).exists()
assert path.read_bytes()==original
finish(registry,ctx['run_id'],identity=process_identity(os.getpid()))
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION'):env.pop(key,None)
    result=subprocess.run([sys.executable,'-c',script,str(request),str(isolated_test)],env=env,
                          capture_output=True,text=True,start_new_session=True,timeout=15)
    assert result.returncode==0,result.stderr
