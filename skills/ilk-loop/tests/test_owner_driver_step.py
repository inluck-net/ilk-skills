"""Actual disposable code/gate/commit/CAS checks; no live repositories."""
from pathlib import Path
import time
import pytest
from driver_step import accept_step as _accept_step,git
from iteration_context import resolve_context,ContextError
import test_iteration_context as fixtures


def accept_step(context,*,default_timeout):
    return _accept_step(context,default_timeout=default_timeout,deadline=time.monotonic()+10)


def fixture(tmp_path,command='python3 -I -c "from pathlib import Path;exec(Path(\'counter.py\').read_text());assert f(4)==6"'):
    args=fixtures.fixture(tmp_path);root=Path(args['exec_tree'])
    git(root,'config','user.name','Fixture');git(root,'config','user.email','fixture@example.invalid')
    (root/'counter.py').write_text('def f(value): return value + 1\n')
    git(root,'add','counter.py');git(root,'commit','-qm','initial counter')
    plan=Path(args['dispatch']['subplan_path'])
    plan.write_text('---\nplan: counter\nstatus: pending\ncurrent_step: 0\nestimated_steps: 1\n---\n# Counter\n'
                    '## Steps\n### Step 0: fix counter\n```yaml\nlocal_checks:\n  - command: '+command+'\n```\n')
    args['dispatch'].update(step=0,slug='counter')
    context=resolve_context(**args);context['worker_sandbox_adapter']='macos-brokered-shell'
    return context,root,plan


def test_red_never_commits_or_advances_and_retains_full_output(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path);base=git(root,'rev-parse','HEAD')
    result=accept_step(context,default_timeout=10)
    assert not result['accepted'] and git(root,'rev-parse','HEAD')==base
    assert 'current_step: 0' in plan.read_text()
    check=result['attempts'][0]['check_observations'][0]
    assert Path(check['stderr_path']).exists() and 'AssertionError' in Path(check['stderr_path']).read_text()


def test_green_driver_commits_and_advances_exactly_once_without_second_gate(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    result=accept_step(context,default_timeout=10)
    assert result['accepted'] and len(result['commits'])==1 and len(result['attempts'])==1
    assert result['tree']==result['attempts'][0]['working_tree']
    assert 'current_step: 1' in plan.read_text() and result['proof_provisional']
    assert '[plan:counter#step-0]' in git(root,'log','-1','--format=%B')
    with pytest.raises(ContextError,match='step changed'):
        accept_step(context,default_timeout=10)


def test_mutating_gate_cannot_cover_its_new_tree(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path,'printf mutated > counter.py')
    result=accept_step(context,default_timeout=10)
    assert not result['accepted'] and not result['attempts'][0]['gate_tree_unchanged']
    assert 'current_step: 0' in plan.read_text()


def test_commit_hook_changed_shipping_tree_is_regated_and_red_not_advanced(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    common=Path(git(root,'rev-parse','--git-common-dir'))
    if not common.is_absolute():common=(root/common).resolve()
    hook=common/'hooks/pre-commit';hook.write_text('#!/bin/sh\nprintf "def f(value): return value + 1\\n" > counter.py\ngit add counter.py\n');hook.chmod(0o700)
    result=accept_step(context,default_timeout=10)
    assert not result['accepted'] and len(result['attempts'])==2 and result['commits']
    assert 'current_step: 0' in plan.read_text()


def test_held_pointer_and_dirty_entry_refuse_before_gate(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path)
    plan.write_text(plan.read_text().replace('status: pending','status: blocked'))
    with pytest.raises(ContextError,match='held'):accept_step(context,default_timeout=10)
    plan.write_text(plan.read_text().replace('status: blocked','status: pending'))
    with pytest.raises(ContextError,match='clean frozen entry'):
        accept_step(dict(context,entry_dirty=True),default_timeout=10)


def test_owned_commit_hook_is_bounded_without_advancing(tmp_path,monkeypatch):
    monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    context,root,plan=fixture(tmp_path);base=git(root,'rev-parse','HEAD')
    (root/'counter.py').write_text('def f(value): return value + 2\n')
    common=Path(git(root,'rev-parse','--git-common-dir'))
    if not common.is_absolute():common=(root/common).resolve()
    import shlex
    marker=tmp_path/'hook-started';hook=common/'hooks/pre-commit'
    hook.write_text('#!/bin/sh\ntouch '+shlex.quote(str(marker))+"\nexec /usr/bin/python3 -c 'import threading;threading.Event().wait()'\n")
    hook.chmod(0o700)
    # Two seconds is a test judgment for the existing whole-second gate
    # parser plus sampled subsecond gate/index startup.
    # A slow valid host falsifies it; no production timeout default changes.
    with pytest.raises(ContextError,match='iteration deadline'):
        _accept_step(context,default_timeout=10,deadline=time.monotonic()+2)
    assert marker.exists() and git(root,'rev-parse','HEAD')==base
    assert 'current_step: 0' in plan.read_text()
