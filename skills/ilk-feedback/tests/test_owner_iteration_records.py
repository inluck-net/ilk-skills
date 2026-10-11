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
