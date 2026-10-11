"""Confirmation cannot overwrite or substitute a different measured check."""
import json
import pytest
from blocking_checks import confirm_rerun

@pytest.mark.parametrize('retries,expected', [
    ([{'outcome':'fail','command':'c'}, {'outcome':'pass','command':'c'}], 'flaky'),
    ([{'outcome':'pass','command':'different'}], 'inconclusive'),
    ([{'outcome':'pass','command':'c'}], 'flaky'),
])
def test_every_confirmation_attempt_is_retained(tmp_path, retries, expected):
    first=tmp_path/'first'; retry=tmp_path/'retry'
    first.write_text(json.dumps(dict(slug='a',step=0,outcome='fail',command='c'))+'\n')
    rows=[dict(slug='a',step=0,**row) for row in retries]
    retry.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    result=confirm_rerun(first,retry); observed=json.loads(first.read_text())
    assert observed['verdict']==expected
    assert len(observed['attempt_records'])==1+len(rows)
    assert observed['attempts']==['fail']+[row['outcome'] for row in rows]
    assert result['blocked']==(expected!='flaky')


def test_display_label_is_not_the_actual_check_identity(tmp_path):
    first=tmp_path/'first'; retry=tmp_path/'retry'
    checks=[dict(command='first',scope='worktree',tree_sha='tree'),
            dict(command='second',scope='worktree',tree_sha='tree')]
    first.write_text(json.dumps(dict(slug='a',step=0,outcome='fail',command='second',check_observations=checks))+'\n')
    retry.write_text(json.dumps(dict(slug='a',step=0,outcome='pass',command='first',check_observations=checks))+'\n')
    result=confirm_rerun(first,retry)
    assert result['blocked'] is False
    assert json.loads(first.read_text())['verdict']=='flaky'
