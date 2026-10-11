"""Unattended results report recorded execution, never a guessed HEAD parent."""
import json
from pathlib import Path
import subprocess
import run_result
import pytest


def _close(project, iteration, base, head, commits, master='MASTER-test.md'):
    return dict(record_phase='close', run_id='run', iteration=iteration,
        context=dict(run_id='run', iteration=iteration, base_commit=base,
            clone=str(project), exec_tree=str(project),
            dispatch=dict(master_path=str(project/master))),
        execution=dict(head_commit=head, commits=commits))


def _write(tmp_path, rows, monkeypatch):
    log=tmp_path/'log';log.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    monkeypatch.setattr(run_result,'_git_info',lambda *a: __import__('pytest').fail('rederived execution'))
    result=tmp_path/'result.json'
    rc=run_result._cli(['write','--result-file',str(result),'--run-id','run',
        '--master','MASTER-test.md','--exit-state','all-shipped',
        '--project-path',str(tmp_path),'--iteration-log',str(log)])
    assert rc==0
    return json.loads(result.read_text())


def test_complete_multi_iteration_range_comes_from_close(tmp_path,monkeypatch):
    rows=[_close(tmp_path,1,'a'*40,'b'*40,['b'*40]),
          _close(tmp_path,2,'b'*40,'c'*40,['c'*40])]
    result=_write(tmp_path,rows,monkeypatch)
    assert result['base_sha']=='a'*40 and result['head_sha']=='c'*40
    assert [row['sha'] for row in result['commits']]==['b'*40,'c'*40]
    assert result['git_provenance']['source']=='closed-iteration-records'


def test_latest_open_without_close_is_unknown(tmp_path,monkeypatch):
    rows=[_close(tmp_path,1,'a'*40,'b'*40,['b'*40]),
          dict(record_phase='open',run_id='run',iteration=2)]
    result=_write(tmp_path,rows,monkeypatch)
    assert result['base_sha']==result['head_sha']=='' and result['commits']==[]


def test_foreign_master_does_not_supply_execution(tmp_path,monkeypatch):
    result=_write(tmp_path,[_close(tmp_path,1,'a'*40,'b'*40,['b'*40],
                                 master='MASTER-other.md')],monkeypatch)
    assert result['head_sha']=='' and result['commits']==[]


def test_unknown_capture_does_not_borrow_current_head(tmp_path,monkeypatch):
    row=_close(tmp_path,1,'a'*40,'b'*40,['b'*40])
    row['execution']['capture_error']='original deadline expired'
    result=_write(tmp_path,[row],monkeypatch)
    assert result['head_sha']=='' and result['commits']==[]


@pytest.mark.parametrize('change', ['gap', 'context-run', 'context-step',
                                  'foreign-root', 'wrong-range', 'duplicate'])
def test_conflicting_execution_is_unknown(tmp_path,monkeypatch,change):
    row=_close(tmp_path,1,'a'*40,'b'*40,['b'*40]); rows=[row]
    if change=='gap':
        rows.append(_close(tmp_path,3,'b'*40,'c'*40,['c'*40]))
    elif change=='context-run': row['context']['run_id']='other'
    elif change=='context-step': row['context']['iteration']=2
    elif change=='foreign-root':
        row['context'].update(clone=str(tmp_path/'other'),exec_tree=str(tmp_path/'other'))
    elif change=='wrong-range': row['execution']['commits']=['c'*40]
    elif change=='duplicate':
        rows.append(_close(tmp_path,1,'a'*40,'c'*40,['c'*40]))
    result=_write(tmp_path,rows,monkeypatch)
    assert result['head_sha']=='' and result['commits']==[]
    assert result['git_provenance']['observation_complete'] is False


def test_legacy_missing_base_does_not_invent_parent(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_RUN_BASE_SHA',raising=False)
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    for message in ['first','second']:
        subprocess.run(['git','-C',str(tmp_path),'-c','user.name=Fixture',
            '-c','user.email=fixture@example.invalid','commit','--allow-empty',
            '-qm',message],check=True)
    base,head,commits=run_result._git_info(tmp_path)
    assert base=='' and head and commits==[]


def test_canonical_source_clone_can_report_a_distinct_execution_root(tmp_path,monkeypatch):
    row=_close(tmp_path,1,'a'*40,'b'*40,['b'*40])
    row['context']['exec_tree']=str(tmp_path/'isolated-worktree')
    result=_write(tmp_path,[row],monkeypatch)
    assert result['base_sha']=='a'*40 and result['head_sha']=='b'*40
    assert result['git_provenance']['observation_complete'] is True


@pytest.mark.parametrize('raw', [
    {'exit_code':1},
    {'outcome':'pass','exit_code':None},
    {'outcome':'pass','exit_code':1},
    {'outcome':'pass','exit_code':False},
])
def test_missing_or_conflicting_check_evidence_is_unmeasured(raw):
    result=run_result._classify_check(dict(raw,slug='gate'))
    assert result['outcome']=='unmeasured' and result['reason']
