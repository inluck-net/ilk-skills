"""Driver clocks retain observed model context and missing values."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import collect


def test_phase_report_uses_actual_context_and_does_not_invent_unknown_time(tmp_path,monkeypatch):
    monkeypatch.setattr(collect,'read_sentinel',lambda _:None)
    row=dict(iteration=1,timestamp='2026-10-11T14:00:00+0800',duration_sec=10,
        worker_cost_observation={'models_actual_init':['mimo-v2.6-pro']},
        driver_phase_observation={'intervals':[
            {'name':'driver_gate','duration_sec':3.125,'clock_basis':'shared-host-monotonic'},
            {'name':'landing','duration_sec':None,'clock_basis':'shared-host-monotonic'}]})
    report=collect.render_report(tmp_path,'fixture','run',[row],None,
        'clean-success',{},None,None,'Measured context.',[])
    assert '| driver_gate | 3.125 | mimo-v2.6-pro | shared-host-monotonic |' in report
    assert '| landing | unknown | mimo-v2.6-pro | shared-host-monotonic |' in report
    assert 'do not sum them' in report
