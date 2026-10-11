"""Actual registered authority, private inputs and durable-close refusals."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]


def test_owned_boundary_requires_positive_close_and_exact_unused_target(tmp_path):
    script=r'''
import copy,json,os,sys,time
from pathlib import Path
from iteration_context import write_context,plan_snapshot
from run_registry import reserve,bind_running,process_identity,finish
from run_inputs import prepare,pin_launch_resources
from upgrade_drain import boundary
import test_owner_driver_step as fixtures
root=Path(sys.argv[1]);toolkit=Path(sys.argv[2])
context,work,plan=fixtures.fixture(root)
registry=root/'registry';ctx=root/'ctx.json';log=root/'close.jsonl'
master=Path(context['plans_dir'])/'MASTER-fixture.md'
master.write_text('---\nstatus: active\n---\n# Fixture\n')
context['dispatch']['master_path']=str(master)
context['master_snapshot']=plan_snapshot(master)
context.update(run_registry_dir=str(registry),runner_owner_pid=os.getpid())
reserve(registry,run_id=context['run_id'],project_key='fixture',clone=context['clone'],
 exec_tree=context['exec_tree'],toolkit_sha=context['toolkit_sha'],expires_at=time.time()+60)
bind_running(registry,context['run_id'],pid=os.getpid())
write_context(ctx,context)
home=root/'home';home.mkdir();(home/'settings.json').write_text('{"env":{}}')
inputs=root/'target';manifest=prepare(toolkit,home,inputs)
pin_launch_resources(inputs,project=work,executables={'python':sys.executable,'claude':sys.executable})
request=registry/'upgrade-drain-request.json'
row=dict(schema_version=1,run_id=context['run_id'],from_toolkit_sha=context['toolkit_sha'],
 master_path=str(master),next_inputs_dir=str(inputs),next_toolkit_sha=manifest['toolkit_sha'],
 consumer_protocol='upgrade-drain-v1-owner-confirmed')
assert boundary(ctx,log) is None
request.write_text(json.dumps(row))
close=dict(record_phase='close',context=context,run_id=context['run_id'],iteration=context['iteration'],
 driver_acceptance={'accepted':True},driver_integrity_passed=True,state_audit={'explained':True},
 merge_outcome='merged',driver_phase_observation={'intervals':[{'name':'owned_group_cleanup','observation_complete':True}]})
log.write_text(json.dumps(close)+'\n')
result=boundary(ctx,log)
assert result['next_toolkit_sha']==manifest['toolkit_sha'] and result['master_path']==str(master)
assert result['consumer_worktrees']==[str(work)]
assert 'status: active' in master.read_text() and 'current_step: 0' in plan.read_text()
def refused():
 try:boundary(ctx,log)
 except (ValueError,RuntimeError):return
 raise AssertionError('invalid drain accepted')
for field,value in [('run_id','another-run'),('next_toolkit_sha','0'*40),('consumer_protocol','unknown')]:
 bad=dict(row);bad[field]=value;request.write_text(json.dumps(bad));refused()
 request.write_text(json.dumps(row))
for field,value in [('driver_integrity_passed',False),('state_audit',{'explained':False}),
 ('driver_acceptance',{'accepted':False}),('stop_reason','timeout'),('merge_outcome','deferred'),
 ('driver_phase_observation',{'intervals':[]})]:
 bad=copy.deepcopy(close);bad[field]=value;log.write_text(json.dumps(bad)+'\n');refused()
log.write_text(json.dumps(close)+'\n'+json.dumps(close)+'\n');refused()
log.write_text(json.dumps(close)+'\n')
os.environ['ILK_WORKER_SESSION']='1';refused();os.environ.pop('ILK_WORKER_SESSION')
finish(registry,context['run_id'],identity=process_identity(os.getpid()))
refused()
print('exact target and close accepted; contradictory/dead-worker boundaries refused')
'''
    env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(ROOT/'skills/ilk-loop/scripts'),
        str(ROOT/'skills/ilk-loop/tests'),*sys.path]))
    for name in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION','ILK_RUN_INPUTS'):
        env.pop(name,None)
    # 30s fixture judgment over observed <1s setup; a valid slower setup
    # falsifies this test bound, not any production timeout.
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path),str(ROOT)],env=env,
        start_new_session=True,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'contradictory/dead-worker' in result.stdout
