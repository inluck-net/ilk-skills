"""Actual owned head/base gates: eligible progress is never green acceptance."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from test_owner_driver_step import fixture,git

pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='Mac owned progress observation adapter')

def run_case(tmp_path,case,transaction='none'):
    command=sys.executable+' -m pytest -q test_focused.py'
    context,root,plan=fixture(tmp_path,command)
    contents={'pre-existing':'def test_existing():\n    assert False\n',
              'owned':'from counter import f\ndef test_existing():\n    assert f(4)==5\n',
              'runner-error':'import nonexistent_fixture_module\ndef test_existing():\n    assert False\n'}
    (root/'test_focused.py').write_text(contents[case])
    git(root,'add','test_focused.py');git(root,'commit','-qm','batch fixture')
    batch=git(root,'rev-parse','HEAD')
    git(root,'commit','--allow-empty','-qm','iteration fixture')
    context.update(base_commit=git(root,'rev-parse','HEAD'),base_tree=git(root,'rev-parse','HEAD^{tree}'))
    context['dispatch']['batch_base_sha']=batch
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    request=tmp_path/'request.json';request.write_text(json.dumps(context))
    before=plan.read_bytes();head=git(root,'rev-parse','HEAD')
    program=r'''import json,os,sys,time
from pathlib import Path
from iteration_context import write_context,read_dispatch
from iteration_clock import monotonic
from run_registry import reserve,bind_running,finish,process_identity
from driver_step import gate
from measured_progress import assess
ctx=json.loads(Path(sys.argv[1]).read_text());registry=Path(sys.argv[1]).parent/'registry'
reserve(registry,run_id=ctx['run_id'],project_key='fixture',clone=ctx['clone'],exec_tree=ctx['exec_tree'],toolkit_sha='a'*40,expires_at=time.time()+35)
bind_running(registry,ctx['run_id'],pid=os.getpid())
ctx.update(run_registry_dir=str(registry),runner_owner_pid=os.getpid(),iteration_deadline_monotonic=monotonic()+30)
path=Path(sys.argv[1]).parent/'frozen.json';write_context(path,ctx)
os.environ['ILK_ITER_CONTEXT']=str(path)
selected=read_dispatch(ctx)
attempt=gate(ctx,Path(ctx['exec_tree']),Path(selected['subplan_path']),selected,default_timeout=15)
row=assess(ctx,attempt,budget_s=15)
assert row['accepted'] is False and row['green_accepted'] is False,row
assert row['progress_eligible'] is (sys.argv[2]=='pre-existing'),row
if sys.argv[3]!='none':
    import progress_transaction as tx
    original_save=tx._save;original_git=tx.driver.git
    mode=sys.argv[3]
    if mode.startswith('hook-'):
        common=Path(original_git(Path(ctx['exec_tree']),'rev-parse','--git-common-dir'))
        if not common.is_absolute():common=(Path(ctx['exec_tree'])/common).resolve()
        hook=common/'hooks/pre-commit'
        hook.write_text('#!/bin/sh\n'+"printf 'def f(value): return value + 3\\n' > counter.py\ngit add counter.py\n")
        hook.chmod(0o700)
    if mode in {'changed-output','forged-observation'}:
        if mode=='changed-output':
            log=Path(row['head_captures'][0]['path']);log.write_text('changed')
        else:
            row['observations'][0]['node_ids']=[]
        from iteration_context import ContextError
        try:tx.accept(ctx,attempt,row,deadline=ctx['iteration_deadline_monotonic'],gate_timeout=15,budget_s=15)
        except ContextError:pass
        else:raise AssertionError('changed evidence committed')
    else:
        def save(context,result,phase):
            original_save(context,result,phase)
            if phase==mode:raise OSError('fixture crash after '+phase)
        def git(root,*args,**kwargs):
            result=original_git(root,*args,**kwargs)
            if mode in {'committed','hook-committed'} and args[0]=='commit':raise OSError('fixture crash after commit')
            return result
        tx._save=save;tx.driver.git=git
        try:row=tx.accept(ctx,attempt,row,deadline=ctx['iteration_deadline_monotonic'],gate_timeout=15,budget_s=15)
        except OSError:
            assert mode!='normal'
            tx._save=original_save;tx.driver.git=original_git
            row=tx.recover(ctx,deadline=ctx['iteration_deadline_monotonic'])
        if mode.startswith('hook-'):
            assert len(row['attempts'])==2 and row['attempts'][-1]['working_tree']==row['tree'],row
        assert row['progress_accepted'] is True and row['accepted'] is False and row['green_accepted'] is False,row
        assert original_git(Path(ctx['exec_tree']),'rev-list','--count',ctx['base_commit']+'..HEAD')=='1'
        again=tx.recover(ctx,deadline=ctx['iteration_deadline_monotonic'])
        assert again['head_commit']==row['head_commit'] and again['accepted'] is False
finish(registry,ctx['run_id'],identity=process_identity(os.getpid()))
print(json.dumps(row))
'''
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_WORKER_SESSION'):env.pop(key,None)
    # 15s gate/sample, 30s original, 35s reservation, 40s outer are fixture
    # judgments for the measured sub-6s two-sample path plus a head gate.
    # A legitimate slower host falsifies these margins; no default changes.
    result=subprocess.run([sys.executable,'-c',program,str(request),case,transaction],env=env,start_new_session=True,capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    if transaction in {'none','changed-output','forged-observation'}:
        assert plan.read_bytes()==before and git(root,'rev-parse','HEAD')==head
    else:
        assert 'current_step: 1' in plan.read_text()
        assert git(root,'rev-list','--count',head+'..HEAD')=='1'


@pytest.mark.parametrize('case',['pre-existing','owned','runner-error'])
def test_real_gate_and_bases_keep_distinct_decisions(tmp_path,case):
    run_case(tmp_path,case)


@pytest.mark.parametrize('phase',['normal','commit-intent','committed','prepared','advanced','changed-output','hook-changed','hook-committed','forged-observation'])
def test_progress_transaction_recovers_without_green_or_duplicate_commit(tmp_path,phase):
    run_case(tmp_path,'pre-existing',phase)
