import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('worker_scope',ROOT/'hooks/worker-write-scope.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def context(tmp_path):
    root=tmp_path/'work';root.mkdir()
    return {'exec_tree':str(root),'plans_dir':str(tmp_path/'plans'),
            'data_root':str(tmp_path/'data'),'runtime_dir':str(tmp_path/'runtime')}


@pytest.mark.parametrize('target,scope',[
    ('work/file.py','worktree'),('work/.git/index','git-metadata'),
    ('plans/step.md','plans'),('data/proof.json','data'),
    ('runtime/ledger.jsonl','runtime'),('foreign/file.py','outside-worktree'),
])
def test_native_writes_use_frozen_scope(tmp_path,target,scope):
    ctx=context(tmp_path)
    row=module.classify({'tool_name':'Write','tool_input':{'file_path':str(tmp_path/target)}},ctx)
    assert row['scope']==scope
    assert str(tmp_path) not in json.dumps(row)


def test_symlink_escape_is_outside_worktree(tmp_path):
    ctx=context(tmp_path);(tmp_path/'foreign').mkdir()
    (tmp_path/'work/link').symlink_to(tmp_path/'foreign',target_is_directory=True)
    row=module.classify({'tool_name':'Edit','tool_input':{'file_path':'link/new.py'},'cwd':str(tmp_path/'work')},ctx)
    assert row['scope']=='outside-worktree'


def test_shell_is_explicitly_not_a_proven_boundary(tmp_path):
    ctx=context(tmp_path)
    row=module.classify({'tool_name':'Bash','tool_input':{'command':'python3 -c "write secret elsewhere"'}},ctx)
    assert row['shell_classification']=='unclassified'
    assert 'secret' not in json.dumps(row)
    assert module.classify({'tool_name':'Bash','tool_input':{'command':'git -C repo commit -m message'}},ctx)['shell_classification']=='git-mutation-hint'


@pytest.mark.parametrize('mode,expected',[('observe',0),('enforce-native',2)])
def test_hook_observes_or_refuses_without_changing_target(tmp_path,mode,expected):
    from iteration_context import resolve_context,write_context
    import test_iteration_context as fixtures
    ctx=resolve_context(**fixtures.fixture(tmp_path));ctx['code_root']=str(ROOT)
    frozen=tmp_path/'ctx.json';write_context(frozen,ctx)
    env=dict(os.environ,ILK_ITER_CONTEXT=str(frozen),ILK_WORKER_SESSION='1',ILK_WRITE_SCOPE_MODE=mode)
    target=tmp_path/'outside.py'
    cp=subprocess.run([sys.executable,str(ROOT/'hooks/worker-write-scope.py')],input=json.dumps({'tool_name':'Write','tool_input':{'file_path':str(target)}}),env=env,capture_output=True,text=True,timeout=10)
    assert cp.returncode==expected,cp.stderr
    assert not target.exists()
    rows=[json.loads(line) for line in (tmp_path/'write-scope-1.jsonl').read_text().splitlines()]
    assert rows[0]['diagnostic_only'] and rows[0]['scope']=='outside-worktree'
    assert (tmp_path/'write-scope-1.jsonl').stat().st_mode & 0o777==0o600
