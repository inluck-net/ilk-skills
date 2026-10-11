import json
import subprocess
import sys
from pathlib import Path
import pytest
SCRIPTS=Path(__file__).parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from iteration_context import resolve_context,write_context


def fixture(tmp_path):
    clone=tmp_path/'clone';clone.mkdir()
    subprocess.run(['git','init','-q',str(clone)],check=True)
    subprocess.run(['git','-C',str(clone),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','base'],check=True)
    work=tmp_path/'work'
    subprocess.run(['git','-C',str(clone),'worktree','add','--detach',str(work)],check=True,capture_output=True)
    plans=tmp_path/'plans';plans.mkdir();p=plans/'step.md';p.write_text('step')
    return dict(clone=clone,exec_tree=work,observe_tree={str(clone):str(work)},code_root=clone,plans_dir=plans,runtime_dir=tmp_path/'runtime',data_root=tmp_path/'data',dispatch={'subplan_path':str(p)},run_id='test',iteration=1,mode='selfmod')


def test_frozen_context_keeps_worktree_and_data_identity(tmp_path):
    args=fixture(tmp_path);ctx=resolve_context(**args)
    assert ctx['exec_tree']==str(args['exec_tree'])
    assert ctx['base_commit']==ctx['toolkit_sha']
    assert ctx['runtime_dir']!=ctx['code_root']
    assert ctx['registry_path'] is None
    path=tmp_path/'ctx.json';write_context(path,ctx)
    assert json.loads(path.read_text())==ctx
    with pytest.raises(FileExistsError):write_context(path,dict(ctx,iteration=2))
    assert json.loads(path.read_text())['iteration']==1


def test_live_clone_and_wrong_plan_refused(tmp_path):
    args=fixture(tmp_path)
    with pytest.raises(ValueError,match='live clone'):
        resolve_context(**dict(args,exec_tree=args['clone'],observe_tree={}))
    other=tmp_path/'outside.md';other.write_text('outside')
    with pytest.raises(ValueError,match='another plans root'):
        resolve_context(**dict(args,dispatch={'subplan_path':str(other)}))


def test_release_without_git_has_unknown_toolkit_sha(tmp_path):
    args=fixture(tmp_path);release=tmp_path/'release';release.mkdir()
    ctx=resolve_context(**dict(args,code_root=release))
    assert ctx['toolkit_sha'] is None
    assert ctx['base_commit']


def test_open_fingerprint_and_close_use_same_plan_and_commit_range(tmp_path):
    from iteration_context import capture_execution
    args=fixture(tmp_path)
    selected=Path(args['dispatch']['subplan_path'])
    selected.write_text('---\nstatus: in-progress\ncurrent_step: 0\n---\n# Work\n## Findings\nold\n')
    ctx=resolve_context(**args)
    before=ctx['plans'][selected.name]
    selected.write_text('---\nstatus: shipped\ncurrent_step: 1\n---\n# Work\n## Findings\nnew\n')
    subprocess.run(['git','-C',str(args['exec_tree']),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','step'],check=True)
    close=capture_execution(ctx)
    assert close['step_to'][selected.name]['step']=='1'
    assert close['step_to'][selected.name]['fingerprint']==before['fingerprint']
    assert len(close['commits'])==1
    assert close['head_commit']==close['commits'][0]
    selected.write_text(selected.read_text().replace('# Work','# Weakened work'))
    assert capture_execution(ctx)['step_to'][selected.name]['fingerprint']!=before['fingerprint']


def test_selfmod_state_reentry_and_disappeared_unmerged_work(tmp_path):
    from selfmod_state import reconcile
    args=fixture(tmp_path);clone=Path(args['clone']);work=Path(args['exec_tree'])
    state=tmp_path/'state.json'
    assert reconcile(clone,work,state)['state']=='ISOLATED'
    marker=work/'.ilk-merge-deferred';marker.write_text('deferred')
    assert reconcile(clone,work,state)['state']=='MERGE_DEFERRED'
    marker.unlink()
    subprocess.run(['git','-C',str(work),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','unmerged'],check=True)
    reconcile(clone,work,state)
    head=subprocess.check_output(['git','-C',str(work),'rev-parse','HEAD'],text=True).strip()
    subprocess.run(['git','-C',str(clone),'worktree','remove',str(work)],check=True)
    with pytest.raises(ValueError,match='before its commits landed'):
        reconcile(clone,work,state)
    subprocess.run(['git','-C',str(clone),'merge','--ff-only',head],check=True,capture_output=True)
    assert reconcile(clone,work,state)['state']=='MERGED'
    subprocess.run(['git','-C',str(clone),'worktree','add','--detach',str(work)],check=True,capture_output=True)
    assert reconcile(clone,work,state)['state']=='ISOLATED'
