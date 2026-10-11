"""Expired or refused sample cleanup must not acquire a fresh timeout/delete."""
import subprocess
from pathlib import Path
import pytest
import red_owner


def sample(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','base'],check=True)
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    return repo,sha


def test_expired_sample_does_not_start_fresh_cleanup(tmp_path,monkeypatch):
    repo,sha=sample(tmp_path);clock=[100.];calls=[];original=subprocess.run
    monkeypatch.setattr(red_owner,'_clock',lambda:clock[0])
    def run(args,**kwargs):
        calls.append((args,kwargs))
        return original(args,**kwargs)
    monkeypatch.setattr(red_owner.subprocess,'run',run)
    def gate(*args,**kwargs):
        clock[0]+=2
        return 1,'FAILED test_a.py::test_a - AssertionError','',False
    monkeypatch.setattr(red_owner,'_run_base_sample',gate)
    # One-second fake cap is a fixture judgment, not wall time/default.
    result=red_owner._run_at_base_worktree(repo,sha,'python3 -m pytest',['test_a.py::test_a'],1)
    removals=[a for a,k in calls if a[:3]==['git','worktree','remove']]
    try:
        assert result=={'test_a.py::test_a':'unmeasured'}
        assert not removals,'expired sample granted a new cleanup deadline'
    finally:
        # Test-owned cleanup outside the expired production measurement.
        trees=subprocess.check_output(['git','-C',str(repo),'worktree','list','--porcelain'],text=True)
        for line in trees.splitlines():
            if line.startswith('worktree ') and line[9:]!=str(repo):
                original(['git','-C',str(repo),'worktree','remove','--force',line[9:]],check=True)
                Path(line[9:]).parent.rmdir()


def test_refused_git_cleanup_retains_sample(tmp_path,monkeypatch):
    repo,sha=sample(tmp_path);original=subprocess.run;removed=[]
    def run(args,**kwargs):
        if args[:3]==['git','worktree','remove']:
            removed.append(Path(args[-1]))
            return subprocess.CompletedProcess(args,1,stdout='',stderr='fixture refuses removal')
        return original(args,**kwargs)
    monkeypatch.setattr(red_owner.subprocess,'run',run)
    monkeypatch.setattr(red_owner,'_run_base_sample',lambda *a,**k:(0,'','',False))
    try:
        with pytest.raises(RuntimeError,match='cleanup'):
            red_owner._run_at_base_worktree(repo,sha,'focused-check',[],10)
        assert len(removed)==1 and removed[0].is_dir()
    finally:
        for path in removed:
            if path.is_dir():
                original(['git','-C',str(repo),'worktree','remove','--force',str(path)],check=True)
                path.parent.rmdir()
            else:
                original(['git','-C',str(repo),'worktree','prune'],check=True)
