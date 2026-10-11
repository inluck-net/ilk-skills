"""Unavailable or mixed baseline results never excuse all head failures."""
import subprocess
import pytest
import red_owner

@pytest.mark.parametrize('batch_result', ['exception','unmeasured','mixed'])
def test_unknown_batch_base_remains_unmeasured(tmp_path,monkeypatch,batch_result):
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','base'],check=True)
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    calls=[]
    def measure(*args,**kwargs):
        calls.append(args)
        if len(calls)==1:return {'one':'failed','two':'failed'}
        if batch_result=='exception':raise RuntimeError('fixture unavailable')
        if batch_result=='unmeasured':return {'one':'unmeasured','two':'failed'}
        return {'one':'passed','two':'failed'}
    monkeypatch.setattr(red_owner,'_run_at_base_worktree',measure)
    result=red_owner.attribute_red(repo,iteration_base=sha,batch_base=sha,head=sha,
        cmd='python3 -m pytest tests/focused.py',stdout_tail='FAILED one\nFAILED two\n',budget_s=5)
    assert result['verdict']=='unmeasured',result
    assert result['owner_sha'] is None
