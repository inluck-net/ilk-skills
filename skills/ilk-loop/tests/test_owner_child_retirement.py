"""Finished child groups leave durable events, not an ever-growing live set."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='Mac registered command fixture')


def test_finished_command_groups_are_retired_without_losing_history(tmp_path):
    script='''
import json,os,sys,time
from pathlib import Path
from iteration_context import write_context
from iteration_clock import monotonic
from run_registry import reserve,bind_running,scan
from bounded_run import run
root=Path(sys.argv[1]);registry=root/'launcher'
context={key:str(root) for key in ('clone','exec_tree','plans_dir','runtime_dir','data_root','code_root')}
context.update(schema_version=1,run_id='retirement',iteration=1,worker_sandbox_adapter='macos-brokered-shell',
               runner_owner_pid=os.getpid(),run_registry_dir=str(registry))
reserve(registry,run_id='retirement',project_key='fixture',clone=root,exec_tree=root,
        toolkit_sha='fixture',expires_at=time.time()+30)
bind_running(registry,'retirement',pid=os.getpid())
path=root/'context.json';write_context(path,context);os.environ['ILK_ITER_CONTEXT']=str(path)
started=monotonic()
for _ in range(30):
    assert run(['/usr/bin/true'],cwd=str(root),timeout=5)==(0,'','',False)
elapsed=monotonic()-started
started=monotonic();rows=scan(registry);observation=monotonic()-started
print(json.dumps(dict(commands=30,retained=len(rows[0]['children']),command_elapsed_sec=elapsed,
                     registry_scan_sec=observation)),flush=True)
assert rows[0]['children']==[]
events=[json.loads(line) for line in (registry/'child-events.jsonl').read_text().splitlines()]
assert len([row for row in events if row['event']=='registered'])==30
assert len([row for row in events if row['event']=='closed'])==30
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION','ILK_RUN_INPUTS'):env.pop(key,None)
    # 15s fixture bound over thirty trivial commands; not a production limit.
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],env=env,capture_output=True,
        text=True,start_new_session=True,timeout=15)
    assert result.returncode==0,(result.stdout,result.stderr)
    print(result.stdout.strip()) # sanitized paired growth observation, with -s
