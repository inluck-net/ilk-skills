import importlib.util
import json
from pathlib import Path

SPEC=importlib.util.spec_from_file_location('owner_collect',Path(__file__).parents[1]/'scripts/collect.py')
collect=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect)


def test_reader_preserves_open_time_when_close_replaces_start(tmp_path,monkeypatch):
    log=tmp_path/'loop.jsonl'
    common={'project':str(tmp_path),'run_id':'run','iteration':1}
    opened=dict(common,record_phase='open',timestamp='2026-10-11T10:00:00')
    closed=dict(common,record_phase='close',timestamp='2026-10-11T10:10:00',duration_sec=30,driver_elapsed_sec=600,exit_code=0)
    monkeypatch.setattr(collect,'_jsonl_log_candidates',lambda *args:[log])
    for rows in ([opened,closed],[closed,opened]):
        log.write_text('\n'.join(json.dumps(row) for row in rows))
        result=collect.read_jsonl_iters(tmp_path)
        assert len(result)==1
        assert result[0]['opened_at']==opened['timestamp']
        assert result[0]['record_phase']=='close'
        assert collect.measured_iteration_elapsed(result[0])==600


def test_unknown_and_legacy_elapsed_remain_distinct():
    assert collect.measured_iteration_elapsed({}) is None
    assert collect.measured_iteration_elapsed({'duration_sec':9})==9
    assert collect.measured_iteration_elapsed({'driver_elapsed_sec':0,'duration_sec':9})==0
    assert collect.measured_iteration_elapsed({'driver_elapsed_sec':float('nan')}) is None
    assert not collect._carries_outcome({'record_phase':'open','stop_reason':'pending'})


def test_report_counts_driver_time_without_double_counting_worker(tmp_path,monkeypatch):
    monkeypatch.setattr(collect,'read_sentinel',lambda *args:None)
    records=[{'iteration':1,'duration_sec':30,'driver_elapsed_sec':600,'opened_at':'start','timestamp':'close','exit_code':0}, {'iteration':2}]
    report=collect.render_report(tmp_path,'fixture','run',records,None,'interrupted',{},None,None,'pending',[])
    assert 'total_elapsed_sec: 600' in report
    assert 'unknown_elapsed_iterations: 1' in report
    assert 'started_at: "start"' in report


def test_feedback_keeps_measured_progress_non_green_and_exact_run(tmp_path):
    import copy
    row=dict(record_phase='close',run_id='run',iteration=1,duration_sec=4,exit_code=0,
        driver_integrity_passed=False,driver_progress_integrity_passed=True,
        context=dict(run_id='run',iteration=1,dispatch=dict(slug='fixture',step=0,subplan_path='/exact/plan',master_path='/exact/master')),
        driver_acceptance=dict(run_id='run',iteration=1,accepted=False,green_accepted=False,progress_accepted=True,
            progress_observation=dict(observations=[dict(node_ids=['test_existing.py::test_existing'])])))
    terminal=dict(record_type='run_exit',run_id='run',stop_reason='shipped-unproven')
    label,facts=collect._classify_core([row,terminal],None,tmp_path)
    assert label=='shipped-unverified' and len(facts['measured_red_progress'])==1
    assert facts['measured_red_progress'][0]['plan_path']=='/exact/plan'
    assert 'green proof remains absent' in collect.recommend_params(label,[row],None,facts)[2]
    assert 'final verification remains opt-in' in collect._label_narrative(label,facts)
    for change in ('open','foreign','green'):
        modified=copy.deepcopy(row)
        if change=='open':modified['record_phase']='open'
        elif change=='foreign':modified['context']['run_id']='other'
        else:modified['driver_acceptance']['green_accepted']=True
        assert collect.recorded_measured_progress([modified],'run')==[]
    assert collect.recorded_measured_progress([row],'other')==[]


def test_per_iteration_fallback_uses_close_and_refuses_foreign_run(tmp_path,monkeypatch):
    root=tmp_path/'logs';directory=root/'runs'/'run';directory.mkdir(parents=True)
    log=directory/'iter-01.log.jsonl'
    common={'run_id':'run','iteration':1}
    opened=dict(common,record_phase='open',timestamp='start')
    closed=dict(common,record_phase='close',timestamp='close',driver_elapsed_sec=600,exit_code=0)
    foreign=dict(closed,run_id='other',driver_elapsed_sec=999)
    monkeypatch.setattr(collect,'_iter_log_root_candidates',lambda *args:[root])
    for rows in ([opened,closed,foreign],[foreign,closed,opened]):
        log.write_text('\n'.join(json.dumps(row) for row in rows))
        result=collect.read_per_iter_jsonl('run',tmp_path)
        assert len(result)==1
        assert result[0]['record_phase']=='close'
        assert result[0]['run_id']=='run'
        assert result[0]['opened_at']=='start'
        assert collect.measured_iteration_elapsed(result[0])==600
