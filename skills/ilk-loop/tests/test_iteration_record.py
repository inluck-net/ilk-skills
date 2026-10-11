import importlib.util
import sys
from pathlib import Path
import pytest
SCRIPTS=Path(__file__).parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from iteration_context import resolve_context,write_context
from iteration_record import project_close_record,latest_close
from ship_proof_ledger import append_record,read_records,read_raw_records
from ship_audit import ledger_record_is_reachable
spec=importlib.util.spec_from_file_location('context_fixture',Path(__file__).with_name('test_iteration_context.py'))
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)


def setup(tmp_path,monkeypatch):
    ctx=resolve_context(**fixtures.fixture(tmp_path))
    context_path=tmp_path/'ctx.json';write_context(context_path,ctx)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(context_path))
    monkeypatch.delenv('ILK_ITER_RECORD_STAGE',raising=False)
    monkeypatch.delenv('ILK_WORKER_SESSION',raising=False)
    ledger=Path(ctx['ledger_path'])
    row={'run_id':'test','iteration':1,'slug':'step','step_from':0,'step_to':1,
         'commits':[ctx['base_commit']],'gate_outcome':'pass'}
    append_record(ledger,row)
    close={'run_id':'test','iteration':1,'record_phase':'close','context':ctx,'merge_outcome':'merged'}
    return ctx,ledger,close


def test_provisional_evidence_is_driver_only_then_finalized_once(tmp_path,monkeypatch):
    ctx,ledger,close=setup(tmp_path,monkeypatch)
    assert read_records(ledger)==[]
    assert read_raw_records(ledger)[0]['record_phase']=='provisional'
    monkeypatch.setenv('ILK_ITER_RECORD_STAGE','driver-check')
    assert len(read_records(ledger))==1
    monkeypatch.setenv('ILK_WORKER_SESSION','1')
    assert read_records(ledger)==[]
    monkeypatch.delenv('ILK_WORKER_SESSION')
    assert project_close_record(close)==1
    assert project_close_record(close)==0
    monkeypatch.delenv('ILK_ITER_RECORD_STAGE')
    records=read_records(ledger)
    assert len(records)==1 and records[0]['record_phase']=='close'
    assert ledger_record_is_reachable(records[0],Path(ctx['clone']))
    assert len(read_raw_records(ledger))==2


@pytest.mark.parametrize('missing',['driver_acceptance','driver_integrity_passed','state_audit','execution'])
def test_code_only_close_requires_each_positive_boundary(tmp_path,monkeypatch,missing):
    ctx,ledger,close=setup(tmp_path,monkeypatch)
    close['context']=dict(ctx,worker_sandbox_adapter='macos-brokered-shell')
    close.update(driver_acceptance={'accepted':True,'run_id':'test','iteration':1,'tree':'exact-tree'},
                 driver_integrity_passed=True,state_audit={'explained':True},execution={'head_tree':'exact-tree'})
    close.pop(missing)
    project_close_record(close)
    records=read_records(ledger)
    assert records[0]['integrity_passed'] is False
    assert not ledger_record_is_reachable(records[0],Path(ctx['clone']))


def test_unknown_terminal_failure_never_projects_green(tmp_path,monkeypatch):
    ctx,ledger,close=setup(tmp_path,monkeypatch)
    close['stop_reason']='unrecognized-new-failure'
    project_close_record(close)
    records=read_records(ledger)
    assert records[0]['integrity_passed'] is False
    assert not ledger_record_is_reachable(records[0],Path(ctx['clone']))


@pytest.mark.parametrize('change',[{'stop_reason':'ship_integrity_violation'},{'merge_outcome':'deferred'},{'merge_outcome':'failed'}])
def test_red_or_unlanded_close_never_becomes_external_proof(tmp_path,monkeypatch,change):
    ctx,ledger,close=setup(tmp_path,monkeypatch)
    project_close_record(dict(close,**change))
    records=read_records(ledger)
    assert len(records)==1
    assert not ledger_record_is_reachable(records[0],Path(ctx['clone']))


def test_missing_or_mismatched_close_keeps_evidence_provisional(tmp_path,monkeypatch):
    _,ledger,close=setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='contradicts'):
        project_close_record(dict(close,iteration=2))
    log=tmp_path/'loop.jsonl';log.write_text('{"record_phase":"open"}\n')
    with pytest.raises(ValueError,match='no matching close'):
        latest_close(log,'test',1)
    assert read_records(ledger)==[]


def test_red_count_and_notice_survive_launch_change_without_green_proof(tmp_path,monkeypatch):
    import os
    import subprocess
    import json
    from recovery import save_notice,brief
    args=fixtures.fixture(tmp_path)
    first=resolve_context(**args)
    second=resolve_context(**dict(args,run_id='next-launch',iteration=2))
    assert first['recovery_dir']==second['recovery_dir']
    recovery=Path(first['recovery_dir']);recovery.mkdir(parents=True)
    contexts=[]
    for n,ctx in enumerate((first,second)):
        path=tmp_path/f'ctx-{n}.json';write_context(path,ctx);contexts.append(path)
    code='''source "$1"
RUN_LOG_DIR="$HOME"
_CONTEXT_RECOVERY_DIR="$2"
'''
    env=dict(os.environ,ILK_DOTSOURCE_ONLY='1',HOME=str(tmp_path),ILK_ITER_CONTEXT=str(contexts[0]))
    runner=str(SCRIPTS/'run_ilk_loop_claude.sh')
    result=subprocess.run(['bash','-c',code+"_increment_gate_red_count step 0\n_write_gate_red_ids step 0 '[\"tests/red.py::test_red\"]'\n",'fixture',runner,str(recovery)],env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    env['ILK_ITER_CONTEXT']=str(contexts[1])
    result=subprocess.run(['bash','-c',code+'_read_gate_red_count step 0\n_read_gate_red_ids step 0\n','fixture',runner,str(recovery)],env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    rows=result.stdout.splitlines()
    assert rows[0]=='1' and json.loads(rows[1])==['tests/red.py::test_red']
    source=tmp_path/'red.txt';source.write_text('FAILED tests/red.py::test_red\ncomplete stdout: prior-attempt.stdout.log\n')
    save_notice(first,'step',0,source)
    lines=brief(second,'step',0)
    assert any('Existing retry count: 1' in line for line in lines)
    assert any('artifact links:' in line for line in lines)
    assert not Path(first['ledger_path']).exists()


def test_salvage_keeps_completed_context_without_replaying_pending_tools(tmp_path,monkeypatch):
    import json
    from recovery import salvage_transcript
    ctx,_,_=setup(tmp_path,monkeypatch)
    stream=tmp_path/'worker.jsonl'
    rows=[{'type':'system','subtype':'init','session_id':'session','model':'measured-model'},
          {'type':'assistant','message':{'content':[{'type':'text','text':'Next fix the focused defect.'},{'type':'tool_use','id':'pending','name':'Bash','input':{'command':'do not execute'}}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'done','content':'private output','is_error':False}]}},
          {'type':'stream_event','event':{'type':'content_block_delta'}}]
    stream.write_text('\n'.join(json.dumps(row) for row in rows)+'\n{"partial":')
    out=tmp_path/'salvage.json'
    result=salvage_transcript(ctx,stream,out)
    assert result['diagnostic_only'] is True
    assert result['last_completed_assistant_text']=='Next fix the focused defect.'
    assert result['pending_tools']==[{'id':'pending','name':'Bash'}]
    assert 'private output' not in out.read_text()
    assert 'do not execute' not in out.read_text()
    assert result['models_observed']==['measured-model']
    assert result['unfinished_stream_response'] is True and result['malformed_rows']==1


def test_corrupted_persistent_retry_count_is_not_reset_to_zero(tmp_path,monkeypatch):
    import os
    import subprocess
    ctx,_,_=setup(tmp_path,monkeypatch)
    recovery=Path(ctx['recovery_dir']);recovery.mkdir(parents=True)
    (recovery/'gate-red-counts.json').write_text('{broken')
    env=dict(os.environ,ILK_DOTSOURCE_ONLY='1')
    code='source "$1"; _CONTEXT_RECOVERY_DIR="$2"; _read_gate_red_count step 0'
    result=subprocess.run(['bash','-c',code,'fixture',str(SCRIPTS/'run_ilk_loop_claude.sh'),str(recovery)],env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode!=0
    assert result.stdout.strip()!='0'


def test_next_launch_projects_closed_evidence_once(tmp_path,monkeypatch):
    import json
    from iteration_record import replay_closed_records
    ctx,ledger,close=setup(tmp_path,monkeypatch)
    log=tmp_path/'loop.jsonl'
    log.write_text(json.dumps({'record_phase':'open','run_id':'test','iteration':1})+'\n'+json.dumps(close)+'\n{"partial":')
    assert replay_closed_records(log,ledger)==1
    assert replay_closed_records(log,ledger)==0
    assert len(read_raw_records(ledger))==2
    assert ledger_record_is_reachable(read_records(ledger)[0],Path(ctx['clone']))


def test_replay_keeps_open_only_evidence_provisional(tmp_path,monkeypatch):
    import json
    from iteration_record import replay_closed_records
    _,ledger,close=setup(tmp_path,monkeypatch)
    log=tmp_path/'loop.jsonl';log.write_text(json.dumps(dict(close,record_phase='open'))+'\n')
    assert replay_closed_records(log,ledger)==0
    assert read_records(ledger)==[]


def test_replay_refuses_foreign_ledger_path(tmp_path,monkeypatch):
    import json
    from iteration_record import replay_closed_records
    _,ledger,close=setup(tmp_path,monkeypatch)
    close['context']['ledger_path']=str(tmp_path/'foreign.jsonl')
    log=tmp_path/'loop.jsonl';log.write_text(json.dumps(close)+'\n')
    with pytest.raises(ValueError,match='ledger'):
        replay_closed_records(log,ledger)
    assert read_records(ledger)==[]
    assert not (tmp_path/'foreign.jsonl').exists()


def test_replay_does_not_make_failed_integrity_green(tmp_path,monkeypatch):
    import json
    from iteration_record import replay_closed_records
    ctx,ledger,close=setup(tmp_path,monkeypatch)
    log=tmp_path/'loop.jsonl';log.write_text(json.dumps(dict(close,stop_reason='ship_integrity_violation'))+'\n')
    assert replay_closed_records(log,ledger)==1
    assert not ledger_record_is_reachable(read_records(ledger)[0],Path(ctx['clone']))


@pytest.mark.parametrize('kind,value',[('count',-1),('count',True),('ids','node'),('ids',[None])])
def test_invalid_retry_history_is_never_overwritten(tmp_path,kind,value):
    import json
    from recovery import update_retry_state
    path=tmp_path/'retry.json';original=json.dumps({'step:0':value});path.write_text(original)
    with pytest.raises(ValueError):
        update_retry_state(path,'step:0',kind,[] if kind=='ids' else None)
    assert path.read_text()==original


def test_retry_state_updates_keep_existing_identity_and_private_atomic_file(tmp_path):
    import json
    from recovery import update_retry_state
    path=tmp_path/'retry.json';path.write_text(json.dumps({'other:0':3}))
    assert update_retry_state(path,'step:0','count')==1
    assert json.loads(path.read_text())=={'other:0':3,'step:0':1}
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(tmp_path.iterdir())=={path,path.with_name(path.name+'.lock')}


def test_missing_failure_identity_cannot_extend_retry_budget():
    from gate_retry import decide_gate_retry
    assert not decide_gate_retry(red_count=1,progress_known=False).should_stop
    assert decide_gate_retry(red_count=2,progress_known=False).should_stop
    assert not decide_gate_retry(red_count=2,progress_known=True,same_failing_set=False).should_stop


def test_retry_updates_do_not_lose_parallel_counts(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from recovery import update_retry_state,read_retry_state
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=tmp_path/'retries.json'
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:update_retry_state(path,'step:0','count'),range(12)))
    assert sorted(results)==list(range(1,13))
    assert read_retry_state(path,'count')=={'step:0':12}


@pytest.mark.parametrize('writer',['retry','notice','salvage'])
def test_worker_cannot_change_recovery_history(tmp_path,monkeypatch,writer):
    from recovery import update_retry_state,save_notice,salvage_transcript
    from run_registry import RegistryError
    monkeypatch.setenv('ILK_WORKER_SESSION','1')
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    target=tmp_path/'history';source=tmp_path/'source';source.write_text('{}\n')
    context={'run_id':'old','iteration':1,'recovery_dir':str(target)}
    with pytest.raises(RegistryError,match='worker'):
        if writer=='retry':update_retry_state(target,'step:0','count')
        elif writer=='notice':save_notice(context,'step',0,source)
        else:salvage_transcript(context,source,target)
    assert not target.exists()


@pytest.mark.parametrize('writer',['retry','notice','salvage'])
def test_driver_loss_after_fsync_cannot_replace_history(tmp_path,monkeypatch,writer):
    import os,subprocess,sys,time,json
    import recovery
    from run_registry import reserve,bind_running,as_driver,RegistryError
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    owner=subprocess.Popen([sys.executable,'-u','-c',
        'import threading;print("ready",flush=True);threading.Event().wait()'],
        stdout=subprocess.PIPE,text=True,start_new_session=True)
    try:
        assert owner.stdout.readline().strip()=='ready'
        registry=tmp_path/'registry'
        reserve(registry,run_id='history-loss',project_key='fixture',clone=tmp_path,
                exec_tree=tmp_path,toolkit_sha='fixture',expires_at=time.time()+30)
        bind_running(registry,'history-loss',pid=owner.pid)
        context=dict(run_registry_dir=str(registry),runner_owner_pid=owner.pid,
                     run_id='history-loss',iteration=1,recovery_dir=str(tmp_path/'recovery'))
        source=tmp_path/'source';source.write_text('{}\n')
        target=(recovery.notice_path(context,'step',0) if writer=='notice' else tmp_path/'history')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text('{"step:0":2}')
        before=target.read_bytes();real=os.fsync
        def lose_owner(descriptor):
            real(descriptor)
            if owner.poll() is None:owner.kill();owner.wait()
        monkeypatch.setattr(recovery.os,'fsync',lose_owner)
        with as_driver(context):
            with pytest.raises(RegistryError,match='no longer live'):
                if writer=='retry':recovery.update_retry_state(target,'step:0','count')
                elif writer=='notice':recovery.save_notice(context,'step',0,source)
                else:recovery.salvage_transcript(context,source,target)
        assert target.read_bytes()==before
        assert set(target.parent.iterdir()) <= {target,target.with_name(target.name+'.lock'),registry,source}
    finally:
        if owner.poll() is None:owner.kill();owner.wait()
