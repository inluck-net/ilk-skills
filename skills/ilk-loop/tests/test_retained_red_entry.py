"""Observed red diffs can resume; arbitrary dirt and live owners cannot."""
import json
import os
from pathlib import Path
import subprocess
import pytest
from iteration_clock import monotonic
import driver_step
from iteration_context import ContextError
from test_owner_driver_step import fixture,accept_step


def retained(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    old,root,plan=fixture(tmp_path)
    (root/'counter.py').write_text('def f(value): return value + 0\n')
    red=accept_step(old,default_timeout=10)
    red.update(run_id=old['run_id'],iteration=old['iteration'])
    assert not red['accepted'] and red['attempts'][-1]['verdict']=='red'
    ended=subprocess.Popen(['/usr/bin/true'],start_new_session=True);ended.wait()
    registry=tmp_path/'previous-registry';registry.mkdir()
    old=dict(old,runner_owner_pid=ended.pid,run_registry_dir=str(registry))
    (registry/'child-events.jsonl').write_text(json.dumps(dict(event='registered',run_id=old['run_id'],identity={'pid':ended.pid,'pgid':ended.pid,'lstart':'closed fixture'}))+'\n')
    close=dict(record_phase='close',run_id=old['run_id'],iteration=old['iteration'],context=old,
               stop_reason='local_checks_failed',driver_acceptance=red,state_audit={'explained':True},
               execution={'head_commit':old['base_commit'],'commits':[]})
    log=Path(old['runtime_dir']).parent/'logs/.ilk-loop.log';log.parent.mkdir();log.write_text(json.dumps(close)+'\n')
    new={k:v for k,v in old.items() if k not in {'runner_owner_pid','run_registry_dir'}}
    new.update(run_id='next-launch',iteration=2,entry_dirty=True)
    return new,root,plan,close,log


def test_exact_red_diff_resumes_but_runs_fresh_gates(tmp_path,monkeypatch):
    context,root,plan,close,log=retained(tmp_path,monkeypatch)
    receipt=driver_step.admit_retained_red_entry(context)
    assert receipt['diagnostic_only'] and receipt['entry_tree']==close['driver_acceptance']['attempts'][-1]['working_tree']
    context['retained_red_entry']=receipt
    # The retained red measurement cannot itself accept the same red tree.
    red=accept_step(context,default_timeout=10)
    assert not red['accepted'] and 'current_step: 0' in plan.read_text()
    assert red['attempts'][-1]['attempt_id']!=close['driver_acceptance']['attempts'][-1]['attempt_id']
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    green=accept_step(context,default_timeout=10)
    assert green['accepted'] and 'current_step: 1' in plan.read_text()
    assert len(green['commits'])==1


@pytest.mark.parametrize('change',['extra-diff','timeout','unknown-state','changed-plan','live-owner','missing-children','live-child','changed-input','green'])
def test_unknown_or_changed_retained_work_refuses(tmp_path,monkeypatch,change):
    context,root,plan,close,log=retained(tmp_path,monkeypatch)
    if change=='extra-diff':(root/'unknown.txt').write_text('unowned edit')
    elif change=='timeout':close['stop_reason']='timeout'
    elif change=='unknown-state':close['state_audit']['explained']=None
    elif change=='changed-plan':context['plans']={}
    elif change=='live-owner':close['context']['runner_owner_pid']=os.getpid()
    elif change=='missing-children':(Path(close['context']['run_registry_dir'])/'child-events.jsonl').unlink()
    elif change=='live-child':
        events=Path(close['context']['run_registry_dir'])/'child-events.jsonl'
        events.write_text(json.dumps(dict(event='registered',run_id=close['context']['run_id'],identity={'pid':os.getpid(),'pgid':os.getpgrp(),'lstart':'live fixture'}))+'\n')
    elif change=='changed-input':context['toolkit_sha']='f'*40
    elif change=='green':close['driver_acceptance']['attempts'][-1]['verdict']='green'
    log.write_text(json.dumps(close)+'\n')
    with pytest.raises(ContextError):driver_step.admit_retained_red_entry(context)
    assert 'current_step: 0' in plan.read_text()
