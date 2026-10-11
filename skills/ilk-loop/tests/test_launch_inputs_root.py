"""Prepared settings must use the declared execution root, not clone settings."""
import json
from pathlib import Path
import subprocess
import pytest
from launch_inputs_root import resolve
from iteration_context import ContextError
from run_inputs import prepare
from test_owner_run_inputs import fixture as input_fixture
from test_pre_loop_exit_sentinel import _build_world_shipped


@pytest.mark.parametrize('selected',['default','pinned','foreign'])
def test_launch_prepares_selected_worktree_project_settings(tmp_path,monkeypatch,selected):
    world=_build_world_shipped(tmp_path,with_steps=True);project=world['project']
    monkeypatch.setenv('ILK_DATA_HOME',str(world['data_home']))
    monkeypatch.setenv('ILK_DATA_DIR',str(world['data_home']))
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    monkeypatch.delenv('ILK_MASTER',raising=False)
    work=tmp_path/'declared-worktree'
    subprocess.run(['git','-C',str(project),'worktree','add','--detach',str(work)],check=True,capture_output=True)
    for root,value in ((project,'clone-model'),(work,'worktree-model')):
        (root/'.claude').mkdir();(root/'.claude/settings.json').write_text(json.dumps({'model':value}))
    master=world['plans']/'MASTER-2026-08-29-execution-plan.md'
    master.write_text(master.read_text().replace('status: active','status: active\nwork_tree: '+str(work)))
    if selected=='pinned':
        other=world['plans']/'MASTER-2026-10-12-alternative.md'
        other.write_text('---\nstatus: active\npriority: 999\n---\n')
    if selected=='foreign':
        other=tmp_path/'MASTER-foreign.md';other.write_text('---\nstatus: active\n---\n')
        with pytest.raises(ContextError):resolve(project,master=str(other))
        return
    chosen=resolve(project,master=master.name if selected=='pinned' else None)
    assert chosen==work.resolve()
    pins=tmp_path/'pins';pins.mkdir();repo,home,_=input_fixture(pins)
    destination=pins/'run';manifest=prepare(repo,home,destination,project=chosen)
    settings=json.loads((Path(manifest['worker_home'])/'settings.json').read_text())
    assert settings['model']=='worktree-model'
    assert manifest['source_project']==str(work.resolve())
    source=next(row for row in manifest['settings_sources'] if row['scope']=='project')
    assert source['path']==str(work/'.claude/settings.json')


def test_explicit_master_without_plans_refuses(tmp_path,monkeypatch):
    import launch_inputs_root
    monkeypatch.setattr(launch_inputs_root,'find_plans_dir',lambda _: (None,None))
    assert resolve(tmp_path)==tmp_path.resolve()
    with pytest.raises(ContextError,match='no plans root'):
        resolve(tmp_path,master='MASTER-missing.md')


def test_dispatch_refuses_clone_settings_for_declared_execution_before_resources(tmp_path,monkeypatch):
    import worker_dispatch
    from iteration_clock import monotonic
    monkeypatch.setattr(worker_dispatch.sys,'platform','darwin')
    monkeypatch.setattr(worker_dispatch,'verify_launch_resources',lambda _: {'source_project':str(tmp_path/'clone')})
    context={'dispatch':{'slug':'fixture'},'mode':'work_tree','clone':str(tmp_path/'clone'),
             'exec_tree':str(tmp_path/'execution')}
    frozen=tmp_path/'context.json'
    with pytest.raises(ContextError,match='execution role'):
        worker_dispatch.prepare(context,frozen,inputs=tmp_path/'inputs',deadline=monotonic()+1,
                                registry=tmp_path/'registry',owner_pid=0)
    assert not frozen.exists()
