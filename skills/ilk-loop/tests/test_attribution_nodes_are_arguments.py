"""Failed node identifiers are data, never additional shell commands."""
import red_owner
import pytest

@pytest.mark.parametrize('at_base',[False,True])
def test_node_is_one_literal_argument(tmp_path,at_base):
    stub=tmp_path/'pytest'
    stub.write_text('#!/usr/bin/env python3\nimport sys\nraise SystemExit(0 if len(sys.argv)==2 else 1)\n');stub.chmod(0o700)
    marker=tmp_path/'unwanted-marker'
    node='test_param[abc;touch${IFS}'+str(marker)+']'
    # The final bracket belongs to the node ID, so the shell-created path
    # would have a bracket suffix. Both remain in this disposable directory.
    if at_base:
        import subprocess
        repo=tmp_path/'repo';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True)
        subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','fixture'],check=True)
        sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
        result=red_owner._run_at_base_worktree(repo,sha,str(stub),[node],5)
        assert result[node]=='passed'
    else:assert red_owner._run_test(tmp_path,str(stub),[node]) is True
    assert not marker.exists() and not marker.with_name(marker.name+']').exists()
