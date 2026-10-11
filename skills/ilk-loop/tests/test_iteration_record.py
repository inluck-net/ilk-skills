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
