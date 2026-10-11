from pathlib import Path
import json
import pytest
from quarantine_subplan import quarantine_subplan
from plan_status import parse_frontmatter
from dispatch_target import TargetError


def fixture(tmp_path,status='in-progress',extra=''):
    path=tmp_path/'2026-10-11-counter.md'
    path.write_text('---\nplan: counter\nstatus: '+status+'\ncurrent_step: 0\n'+extra+'---\n# Work\n')
    return path


def test_strikes_block_and_findings_are_one_transaction_and_green_clears_auto_block(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=fixture(tmp_path)
    assert not quarantine_subplan(tmp_path,'counter','focused gate')['blocked']
    assert quarantine_subplan(tmp_path,'counter','focused gate')['blocked']
    state=parse_frontmatter(path.read_text())
    assert state['status']=='blocked' and state['auto_block_fails']=='2'
    assert 'auto-quarantined' in path.read_text()
    rows=[json.loads(line) for line in (tmp_path/'state-transitions.jsonl').read_text().splitlines()]
    assert len(rows)==4 and rows[-2]['to']=={'auto_block_fails':2,'status':'blocked'}
    assert quarantine_subplan(tmp_path,'counter','focused gate',outcome='pass')['reset']
    assert parse_frontmatter(path.read_text())['status']=='in-progress'


@pytest.mark.parametrize('extra',['','auto_block_fails: 2\nhold: human\n'])
def test_green_never_releases_manual_or_human_held_block(tmp_path,monkeypatch,extra):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=fixture(tmp_path,status='blocked',extra=extra);before=path.read_bytes()
    result=quarantine_subplan(tmp_path,'counter','focused gate',outcome='pass')
    assert result['blocked'] and result['held'] and path.read_bytes()==before


def test_ambiguous_slug_is_refused_instead_of_quarantining_first_file(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    path=fixture(tmp_path);other=tmp_path/'2026-10-12-counter.md';other.write_bytes(path.read_bytes())
    with pytest.raises(TargetError,match='ambiguous'):quarantine_subplan(tmp_path,'counter','focused gate')
    assert 'auto_block_fails' not in path.read_text()+other.read_text()
