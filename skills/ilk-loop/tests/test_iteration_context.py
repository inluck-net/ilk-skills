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
    assert ctx['registry_path']==str(Path(args['runtime_dir']).resolve()/'launcher')
    assert ctx['clone_base_commit']==ctx['base_commit']
    assert ctx['clone_ref'].startswith('refs/heads/')
    assert ctx['clone_entry_dirty'] is False
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


def test_recovery_identity_survives_registry_progress_but_not_contract_changes(tmp_path):
    args=fixture(tmp_path)
    selected=Path(args['dispatch']['subplan_path'])
    registered=selected.with_name('2026-10-11-step.md');selected.rename(registered)
    args['dispatch']['subplan_path']=str(registered)
    master=Path(args['plans_dir'])/'MASTER-selected.md'
    master.write_text('---\nstatus: active\n---\n# Work\n## Sub-plan registry\n'
                      '| File | Status |\n|---|---|\n| 2026-10-11-step.md | pending |\n'
                      '## Progress log\nold progress\n## Contract\nkeep this\n')
    args['dispatch'].update(master_path=str(master),batch_base_sha='frozen-base')
    initial=resolve_context(**args)['recovery_dir']
    master.write_text(master.read_text().replace('status: active','status: shipped')
                      .replace('| pending |','| shipped |').replace('old progress','new progress'))
    assert resolve_context(**dict(args,run_id='relaunch',iteration=2))['recovery_dir']==initial
    master.write_text(master.read_text().replace('keep this','changed contract'))
    assert resolve_context(**args)['recovery_dir']!=initial


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


def test_selfmod_worker_cannot_create_lifecycle_state(tmp_path,monkeypatch):
    from selfmod_state import reconcile
    from run_registry import RegistryError
    args=fixture(tmp_path);state=tmp_path/'runtime/state.json'
    monkeypatch.setenv('ILK_WORKER_SESSION','1')
    with pytest.raises(RegistryError,match='worker cannot'):
        reconcile(Path(args['clone']),Path(args['exec_tree']),state)
    assert not state.parent.exists()


def test_audit_ignores_unmerged_trailer_and_unreachable_ledger(tmp_path):
    import ship_audit
    args=fixture(tmp_path);clone=Path(args['clone']);work=Path(args['exec_tree'])
    subprocess.run(['git','-C',str(work),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','step [plan:work#step-0]'],check=True)
    sha=subprocess.check_output(['git','-C',str(work),'rev-parse','HEAD'],text=True).strip()
    subprocess.run(['git','-C',str(clone),'branch','unmerged',sha],check=True)
    assert ship_audit.check_step_commits('work',[0],cwd=clone)==([],[0])
    assert not ship_audit._step_has_own_trailer('work',0,cwd=clone)
    record={'slug':'work','step_from':0,'step_to':1,'commits':[sha]}
    assert ship_audit.check_step_commits('work',[0],cwd=clone,ledger_records=[record])==([],[0])
    subprocess.run(['git','-C',str(clone),'merge','--ff-only',sha],check=True,capture_output=True)
    assert ship_audit.check_step_commits('work',[0],cwd=clone)==([0],[])


def test_integrity_reads_frozen_execution_tree_and_refuses_bad_context(tmp_path,monkeypatch):
    import ship_integrity
    from iteration_context import ContextError
    args=fixture(tmp_path);ctx=resolve_context(**args)
    path=tmp_path/'ctx.json';write_context(path,ctx)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(path))
    subplan=Path(args['dispatch']['subplan_path'])
    assert ship_integrity._resolve_project_root(subplan)==(Path(ctx['exec_tree']),'iteration_context')
    subplan.write_text('---\nstatus: shipped\nplan: work\n---\n')
    assert 'contradicts' in ship_integrity._missing_step_reason(subplan,repo=Path(ctx['clone']))
    outside=tmp_path/'outside.md';outside.write_text('---\nstatus: shipped\nplan: outside\n---\n')
    with pytest.raises(ContextError,match='outside'):
        ship_integrity._resolve_project_root(outside)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(tmp_path/'missing.json'))
    assert 'iteration context invalid' in ship_integrity._missing_step_reason(outside)


def test_batch_gate_keeps_frozen_identity_and_refuses_wrong_latest_batch(tmp_path,monkeypatch):
    import batch_gate
    args=fixture(tmp_path);plans=Path(args['plans_dir'])
    master=plans/'MASTER-selected.md';master.write_text('| step.md |\n')
    base=subprocess.check_output(['git','-C',str(args['clone']),'rev-parse','HEAD'],text=True).strip()
    args['dispatch'].update(master_path=str(master),batch_base_sha=base)
    ctx=resolve_context(**args);path=tmp_path/'ctx.json';write_context(path,ctx)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(path))
    monkeypatch.delenv('ILK_WORKER_SESSION',raising=False)
    subprocess.run(['git','-C',str(args['exec_tree']),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','later'],check=True)
    current=subprocess.check_output(['git','-C',str(args['exec_tree']),'rev-parse','HEAD'],text=True).strip()
    record=batch_gate.BatchGateRecord('delegated',current,'none','now')
    runtime=Path(ctx['runtime_dir'])
    batch_gate.write_record(record,runtime,batch='selected')
    read=batch_gate.read_record(runtime,batch='selected')
    assert read.master_path==str(master)
    assert read.batch_base_sha==base and read.batch_base_sha!=read.head_sha
    assert read.run_id=='test'
    assert batch_gate.read_record(runtime,batch='different') is None


def test_worker_reads_pinned_dispatch_without_reselecting_queue(tmp_path):
    from iteration_context import read_dispatch
    args=fixture(tmp_path)
    args['dispatch']['step']=0
    context=resolve_context(**args)
    result=read_dispatch(context)
    assert result['subplan_path']==context['dispatch']['subplan_path']
    assert result['step']==context['dispatch']['step']
    assert result['run_id']==context['run_id']
    foreign=dict(context['dispatch'],subplan_path=str(tmp_path/'foreign.md'))
    import pytest
    from iteration_context import ContextError
    with pytest.raises(ContextError,match='outside frozen registry'):
        read_dispatch(dict(context,dispatch=foreign))


def test_dispatch_cli_uses_the_same_frozen_record(tmp_path,monkeypatch):
    import os,sys
    args=fixture(tmp_path);args['dispatch']['step']=0
    context=resolve_context(**args);path=tmp_path/'ctx.json';write_context(path,context)
    monkeypatch.setenv('ILK_ITER_CONTEXT',str(path))
    completed=subprocess.run([sys.executable,str(SCRIPTS/'iteration_context.py'),'--print-dispatch'],capture_output=True,text=True,env=os.environ,timeout=10)
    assert completed.returncode==0,completed.stderr
    identity=json.loads(completed.stdout)
    assert identity['subplan_path']==context['dispatch']['subplan_path']
    assert identity['step']==0
