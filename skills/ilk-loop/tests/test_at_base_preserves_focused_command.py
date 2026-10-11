"""Command-level attribution must not expand one authored file to the suite."""
import subprocess
import sys
import red_owner


def test_focused_file_is_not_replaced_with_all_tests(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    (repo/'test_focused.py').write_text('def test_selected(): assert True\n')
    (repo/'test_sibling.py').write_text('def test_unselected(): assert False\n')
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    result=red_owner._run_at_base_worktree(repo,sha,sys.executable+' -m pytest test_focused.py -q',[],5)
    assert result=={'_command':'passed'},result
