import json
from pathlib import Path
import pytest
from master_snapshot import take,restore
from subplan_status_snapshot import _write_status
from plan_state import transition,TransitionError
from plan_status import parse_frontmatter


def test_restore_other_master_field_never_erases_an_accepted_new_hold(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    master=tmp_path/'MASTER-fixture.md';master.write_text('---\nstatus: active\n---\n# Registry\n')
    snapshot=take(tmp_path,[master.name])
    master.write_text('---\nstatus: strange-worker-value\nhold: "human"\n---\n# Registry\n')
    result=restore(tmp_path,snapshot)
    state=parse_frontmatter(master.read_text())
    assert result['restored'] and not result['errors']
    assert state['status']=='active' and state['hold']=='human'
    assert result['accepted'][0]['field']=='hold'
    rows=[json.loads(line) for line in (tmp_path/'state-transitions.jsonl').read_text().splitlines()]
    assert rows[0]['actor']=='integrity-repair' and rows[1]['phase']=='applied'


def test_invalid_status_restore_is_journaled_without_changing_body(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    plan=tmp_path/'step.md';plan.write_text('---\nstatus: nonsense\ncurrent_step: 0\n---\n# Body\n')
    _write_status(plan,'pending')
    assert plan.read_text()=='---\nstatus: pending\ncurrent_step: 0\n---\n# Body\n'
    assert (tmp_path/'state-transitions.jsonl').exists()


def test_integrity_repair_cannot_advance_or_erase_human_hold(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    plan=tmp_path/'step.md';plan.write_text('---\nstatus: blocked\nhold: human\ncurrent_step: 0\n---\n')
    for changes in ({'hold':None},{'current_step':1}):
        with pytest.raises(TransitionError,match='integrity repair'):
            transition(plan,{'status':'blocked'},changes,actor='integrity-repair',reason='invalid repair')


def test_actual_runner_refuses_proof_when_status_repair_cannot_be_verified(tmp_path):
    import shutil
    if shutil.which('gtimeout') is None:pytest.skip('explicit bash production-entry fixture requires gtimeout')
    from test_a_worker_cannot_change_master_state import _run_main
    proc,_,state=_run_main(tmp_path,
        "sed -i '' 's/^status: in-progress$/status: nonsense/' $(dirname {master})/2026-09-29-aaa.md\n"
        "printf corrupt > $(dirname {master})/state-transitions.jsonl")
    assert state.get('state')=='ship_integrity_violation',(state,proc.stdout[-2000:],proc.stderr[-2000:])
    assert 'refusing integrity proof for unverified sub-plan state' in proc.stderr
