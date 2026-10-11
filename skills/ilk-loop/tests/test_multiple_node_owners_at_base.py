"""Actual failing nodes keep their distinct measured commit owners."""
import json
import os
from pathlib import Path
import subprocess
import sys
from blocking_checks import strike_targets
from driver_step import git
from red_owner import attribute_red


def test_two_actual_pytest_nodes_find_two_owners(tmp_path):
    root=tmp_path/'repo';root.mkdir();git(root,'init','-q');git(root,'config','user.name','Fixture');git(root,'config','user.email','fixture@example.invalid')
    (root/'counter.py').write_text('a = 1\nb = 2\n')
    (root/'test_nodes.py').write_text('import counter\ndef test_a(): assert counter.a == 1\ndef test_b(): assert counter.b == 2\n')
    git(root,'add','.');git(root,'commit','-qm','green base');base=git(root,'rev-parse','HEAD')
    (root/'counter.py').write_text('a = 0\nb = 2\n');git(root,'add','.');git(root,'commit','-qm','[plan:first-owner#step-0] break a')
    (root/'counter.py').write_text('a = 0\nb = 0\n');git(root,'add','.');git(root,'commit','-qm','[plan:second-owner#step-0] break b');head=git(root,'rev-parse','HEAD')
    command=sys.executable+' -m pytest test_nodes.py -q --color=no'
    red=subprocess.run(command,shell=True,cwd=root,env=dict(os.environ,PYTHONUSERBASE='/Users/chad/Library/Python/3.9'),capture_output=True,text=True)
    assert red.returncode==1
    # Fixture judgment: 20s covers measured ~2s nested pytest/Git work; a
    # valid slower host falsifies this fixture allowance, not a runner cap.
    attr=attribute_red(root,base,base,head,command,red.stdout,20)
    assert attr['verdict']=='owned' and attr['batch_base_green'] is True,attr
    assert {node['owner_slug'] for node in attr['nodes']}=={'first-owner','second-owner'},attr
    path=tmp_path/'rows';path.write_text(json.dumps(dict(slug='victim',step=0,outcome='fail',verdict='red',attribution=attr))+'\n')
    assert strike_targets(path)==['first-owner','second-owner']


def test_mutating_base_check_is_unmeasured(tmp_path):
    from red_owner import _run_at_base_worktree
    root=tmp_path/'mutating-repo';root.mkdir();git(root,'init','-q');git(root,'config','user.name','Fixture');git(root,'config','user.email','fixture@example.invalid')
    (root/'counter.py').write_text('value = 1\n');git(root,'add','.');git(root,'commit','-qm','base')
    base=git(root,'rev-parse','HEAD')
    command=sys.executable+' -I -c "from pathlib import Path;Path(\'counter.py\').write_text(\'value = 2\\n\')"'
    result=_run_at_base_worktree(root,base,command,[],5)
    assert result=={'_command':'unmeasured'},result
    assert (root/'counter.py').read_text()=='value = 1\n' # live source was untouched
