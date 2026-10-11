import json
import pytest
from plan_state import reconcile_registry,_digest,TransitionError
from plan_status import reconcile_master_registry,parse_frontmatter
from plan_fingerprint import fingerprint


def fixture(tmp_path):
    child=tmp_path/'2026-10-11-step.md';child.write_text('---\nstatus: shipped\n---\n# Work\n')
    master=tmp_path/'MASTER-fixture.md'
    master.write_text('---\nstatus: blocked\nhold: human\n---\n# Legacy registry\n'
                      '| File | Status |\n|---|---|\n| 2026-10-11-step.md | pending |\n'
                      '## Other table\n| Meaning | Status |\n|---|---|\n| contract | fixed |\n')
    return master


def test_legacy_registry_projection_is_journaled_without_hold_or_contract_changes(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    master=fixture(tmp_path);before=fingerprint(master)
    assert reconcile_master_registry(master,tmp_path)
    assert fingerprint(master)==before
    assert parse_frontmatter(master.read_text())['hold']=='human'
    assert '| contract | fixed |' in master.read_text() and '| 2026-10-11-step.md | shipped |' in master.read_text()
    rows=[json.loads(line) for line in (tmp_path/'state-transitions.jsonl').read_text().splitlines()]
    assert rows[0]['projection']=='registry' and rows[1]['phase']=='applied'
    assert not reconcile_master_registry(master,tmp_path)


def test_projection_preserves_bom_and_crlf_and_refuses_stale_master_digest(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    master=fixture(tmp_path);raw=b'\xef\xbb\xbf'+master.read_bytes().replace(b'\n',b'\r\n');master.write_bytes(raw)
    assert reconcile_registry(master,expected_digest=_digest(raw))
    actual=master.read_bytes();assert actual.startswith(b'\xef\xbb\xbf')
    assert actual.count(b'\n')==actual.count(b'\r\n')
    with pytest.raises(TransitionError,match='master changed'):
        reconcile_registry(master,expected_digest=_digest(raw))


def test_worker_cannot_project_or_weaken_master_registry(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    master=fixture(tmp_path);before=master.read_bytes()
    monkeypatch.setenv('ILK_WORKER_SESSION','1')
    with pytest.raises(TransitionError,match='workers'):
        reconcile_registry(master,expected_digest=_digest(before))
    assert master.read_bytes()==before
