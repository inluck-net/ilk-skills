"""Reproduce gh-resolve 20261011-104410 without touching its real artifacts."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import collect


def render(tmp_path,monkeypatch,*,sentinel_run='run'):
    monkeypatch.setattr(collect,'read_sentinel',lambda _:dict(run_id=sentinel_run,
        started_at='2026-10-11T10:44:11+0800',master='held-master'))
    rows=[dict(iteration=1,opened_at='2026-10-11T10:44:21+0800',
        timestamp='2026-10-11T10:47:23+0800',driver_elapsed_sec=186,
        duration_sec=100,new_commits_total=2,exit_code=0,model='mimo-v2.6-pro'),
        dict(iteration=999999,record_type='run_exit',stop_reason='blocked-no-runnable')]
    return collect.render_report(tmp_path,'fixture','run',rows,None,
        'blocked-no-runnable',{},None,None,'No runnable master.',[])


def test_held_queue_report_uses_run_start_and_never_offers_resume(tmp_path,monkeypatch):
    report=render(tmp_path,monkeypatch)
    assert 'started_at: "2026-10-11T10:44:11+0800"' in report
    assert 'total_elapsed_sec: 186' in report
    assert 'iterations: 1' in report
    assert '| 1 | 3.1 | 2 | 0 |' in report
    assert '| 999999 |' not in report
    assert 'Resume now' not in report
    assert 'Leave the loop idle' in report
    assert 'Configured model' in report
    assert 'does not establish worker execution' in report


def test_historical_report_does_not_borrow_later_sentinel(tmp_path,monkeypatch):
    report=render(tmp_path,monkeypatch,sentinel_run='later')
    assert 'started_at: "2026-10-11T10:44:21+0800"' in report
    assert 'master: "held-master"' not in report


def test_held_queue_has_no_fabricated_launch_limits():
    maximum,timeout,rationale=collect.recommend_params('blocked-no-runnable',[],None)
    assert maximum is None and timeout is None
    assert 'Launch limits cannot make held work runnable' in rationale
