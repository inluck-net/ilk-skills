"""Result provenance and its write path are driver control-plane inputs."""
import json
import os
from pathlib import Path
import subprocess
import pytest
import run_result


def test_worker_cannot_write_a_runner_result(tmp_path,monkeypatch):
    monkeypatch.setenv('ILK_WORKER_SESSION','1')
    result=tmp_path/'forged-result.json'
    with pytest.raises(ValueError,match='worker'):
        run_result.write_result(result,run_id='fake',master_slug='fake',exit_state='all-shipped')
    assert not result.exists()


@pytest.mark.parametrize('through_symlink',[False,True])
def test_actual_profile_refuses_a_result_inside_project_before_mkdir(tmp_path,through_symlink):
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    source=(scripts/'run_ilk_loop_claude.sh').read_text()
    function=source[source.index('_read_unattended_profile() {'):
                    source.index('# Write the unattended result file via run_result.py.')]
    project=tmp_path/'project';project.mkdir();plans=tmp_path/'plans';plans.mkdir()
    root=project
    if through_symlink:
        root=tmp_path/'outside-looking';root.symlink_to(project,target_is_directory=True)
    result=root/'new-parent/result.json'
    master=plans/'MASTER-fixture.md'
    master.write_text('---\nmaster_plan: fixture\nstatus: active\nilk_profile: unattended\n'
                      'result_file: '+str(result)+'\n---\n')
    status=tmp_path/'status.py';status.write_text('import json;print(json.dumps({"master":"MASTER-fixture.md"}))\n')
    env=dict(os.environ,PROJECT_PATH=str(project),FIXTURE_PLANS=str(plans),
             LOOP_STATUS_SCRIPT=str(status),_SKILL_ROOT=str(scripts.parent.parent))
    for key in ('ILK_EXEC_TREE','SELFMOD_WORKTREE_PATH'):env.pop(key,None)
    program='get_plans_dir(){ printf "%s\\n" "$FIXTURE_PLANS"; }\n'+function+'''
_read_unattended_profile
printf 'RESULT=%s\\n' "$_UNATTENDED_RESULT_FILE"
'''
    call=subprocess.run(['bash','-c',program],env=env,capture_output=True,text=True,timeout=10)
    assert call.returncode==0,call.stderr
    assert 'RESULT=\n' in call.stdout,call.stdout
    assert not (project/'new-parent').exists()


@pytest.mark.parametrize('role',['clone','exec_tree'])
def test_frozen_context_refuses_results_inside_either_writable_root(tmp_path,role):
    from test_iteration_context import fixture
    from iteration_context import resolve_context
    args=fixture(tmp_path)
    master=args['plans_dir']/'MASTER-fixture.md'
    master.write_text('---\nstatus: active\nilk_profile: unattended\nresult_file: '+
        str(args[role]/'result.json')+'\n---\n')
    args['dispatch']['master_path']=str(master)
    with pytest.raises(ValueError,match='overlaps'):
        resolve_context(**args)


def test_outside_sibling_is_valid_but_inside_alias_is_not(tmp_path):
    project=tmp_path/'project';project.mkdir()
    outside=tmp_path/'project-other';outside.mkdir()
    assert run_result.validate_result_path(outside/'result.json',[project])==outside/'result.json'
    (project/'outside-link').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='overlaps'):
        run_result.validate_result_path(project/'outside-link/result.json',[project])
