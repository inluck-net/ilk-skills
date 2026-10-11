import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from dispatch_target import TargetError, resolve
import pytest


def plan(plans,name,slug):
    path=plans/name;path.write_text(f'---\nplan: {slug}\n---\n');return path


def master(plans,name,*paths):
    path=plans/name;path.write_text('---\nbase_sha: base123\n---\n'+'\n'.join(f'| {p.name} |' for p in paths));return path


def test_exact_master_wins_over_suffix_and_other_master(tmp_path):
    chosen=plan(tmp_path,'2026-10-11-work-a1.md','work')
    decoy=plan(tmp_path,'2026-10-11-other-work-a1.md','other-work-a1')
    other=plan(tmp_path,'2026-10-11b-work-a1.md','work')
    selected=master(tmp_path,'MASTER-selected.md',chosen,decoy)
    master(tmp_path,'MASTER-other.md',other)
    r=resolve(tmp_path,'work-a1',master=selected,filename=chosen.name)
    assert r['subplan_path']==str(chosen)
    assert r['master_path']==str(selected)
    assert r['batch_base_sha']=='base123'


def test_duplicate_identity_is_refused(tmp_path):
    a=plan(tmp_path,'2026-10-11-work.md','alias')
    b=plan(tmp_path,'2026-10-11b-work.md','alias')
    selected=master(tmp_path,'MASTER-selected.md',a,b)
    with pytest.raises(TargetError,match='2 matches'):
        resolve(tmp_path,'alias',master=selected)


def test_legacy_alias_is_preserved_and_missing_path_is_not_guessed(tmp_path):
    a=plan(tmp_path,'2026-10-11-work-run-id.md','work')
    m=master(tmp_path,'MASTER-selected.md',a)
    assert resolve(tmp_path,'work',master=m)['subplan_path']==str(a)
    with pytest.raises(TargetError,match='0 matches'):
        resolve(tmp_path,'work',master=m,filename='missing.md')


def test_cli_refuses_ambiguity_without_emitting_identity(tmp_path):
    a=plan(tmp_path,'2026-10-11-work.md','work')
    b=plan(tmp_path,'2026-10-11b-work.md','work')
    master(tmp_path,'MASTER-selected.md',a,b)
    cp=subprocess.run([sys.executable,str(SCRIPTS/'dispatch_target.py'),'--plans-dir',str(tmp_path),'--slug','work'],capture_output=True,text=True,timeout=10)
    assert cp.returncode==2
    assert cp.stdout==''
    assert 'refused' in cp.stderr


def test_gate_and_snapshot_read_the_pinned_path(tmp_path,monkeypatch):
    import run_local_checks
    import gate_snapshot
    good=plan(tmp_path,'2026-10-11b-work-run.md','work')
    plan(tmp_path,'2026-10-11-other-work-run.md','other-work-run')
    plan(tmp_path,'2026-10-11-work-run.md','work')
    monkeypatch.setenv('ILK_DISPATCH_SLUG','work-run')
    monkeypatch.setenv('ILK_DISPATCH_SUBPLAN',str(good))
    monkeypatch.setattr(run_local_checks,'_resolve_plans_dir',lambda p:(tmp_path,'fixture'))
    assert run_local_checks.find_subplan(tmp_path,'work-run')==good
    assert gate_snapshot.take(tmp_path,'work-run')['path']==str(good)
    good.unlink()
    with pytest.raises(TargetError,match='missing'):
        run_local_checks.find_subplan(tmp_path,'work-run')
    with pytest.raises(TargetError,match='missing'):
        gate_snapshot.take(tmp_path,'work-run')


def test_context_pin_wins_and_conflicting_legacy_pin_is_refused(tmp_path,monkeypatch):
    from dispatch_target import pinned_subplan
    selected=plan(tmp_path,'2026-10-11-work.md','work')
    wrong=plan(tmp_path,'2026-10-11b-work.md','work')
    context=tmp_path/'ctx.json'
    context.write_text(json.dumps({'plans_dir':str(tmp_path),'dispatch':{'slug':'work','subplan_path':str(selected)}}))
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(context))
    monkeypatch.delenv('ILK_DISPATCH_SLUG',raising=False)
    assert pinned_subplan(tmp_path,'work') == selected
    monkeypatch.setenv('ILK_DISPATCH_SLUG','work')
    monkeypatch.setenv('ILK_DISPATCH_SUBPLAN',str(wrong))
    with pytest.raises(TargetError,match='contradicts'):
        pinned_subplan(tmp_path,'work')


def test_runner_uses_scheduler_next_and_refuses_null(tmp_path):
    import os
    runner=SCRIPTS/'run_ilk_loop_claude.sh'
    payload={'master_status':'active','subplans':[
        {'slug':'blocked','fname':'blocked.md','status':'in-progress','current_step':'0'},
        {'slug':'ready','fname':'ready.md','status':'pending','current_step':'1'}],
        'next':{'fname':'ready.md'}}
    env=dict(os.environ,ILK_DOTSOURCE_ONLY='1',ILK_DISPATCH_STATUS_JSON=json.dumps(payload))
    code='source "$1"; get_active_subplan_targets'
    cp=subprocess.run(['bash','-c',code,'fixture',str(runner)],env=env,capture_output=True,text=True,timeout=20)
    assert cp.returncode==0,cp.stderr
    assert cp.stdout.strip()=='ready 1'
    payload['next']=None;env['ILK_DISPATCH_STATUS_JSON']=json.dumps(payload)
    cp=subprocess.run(['bash','-c',code,'fixture',str(runner)],env=env,capture_output=True,text=True,timeout=20)
    assert cp.stdout.strip()==''
