"""Requested-node attribution distinguishes runner faults from test failures."""
import subprocess
import sys
import pytest
import red_owner

@pytest.mark.parametrize('code,output',[(1,'ImportError: pytest unavailable'),(127,'pytest: command not found'),(1,'FAILED tests/other.py::test_other - unrelated'),(1,'ERROR tests/one.py::test_one - fixture setup unavailable')])
def test_runner_fault_does_not_measure_requested_node_red(tmp_path,code,output):
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','base'],check=True)
    base=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    runner=tmp_path/'pytest';runner.write_text(f'import sys\nprint({output!r})\nraise SystemExit({code})\n')
    node='tests/one.py::test_one'
    result=red_owner._run_at_base_worktree(repo,base,str(sys.executable)+' '+str(runner),[node],5)
    assert result[node]=='unmeasured'


def test_actual_pytest_assertion_measures_the_exact_base_node(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'test_focused.py').write_text('def test_existing():\n    assert False\n')
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','known failing node'],check=True)
    base=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    node='test_focused.py::test_existing'
    result=red_owner._run_at_base_worktree(repo,base,str(sys.executable)+' -m pytest -q test_focused.py',[node],10)
    assert result[node]=='failed'
