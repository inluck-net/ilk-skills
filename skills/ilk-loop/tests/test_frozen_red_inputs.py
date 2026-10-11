"""A later master or repository HEAD cannot replace this dispatch's base."""
import subprocess
from pathlib import Path
import pytest
from iteration_context import resolve_context,red_inputs,ContextError
from test_iteration_context import fixture


def test_execution_root_and_base_are_frozen(tmp_path):
    args=fixture(tmp_path)
    plan=args['plans_dir']/'2026-10-12-step.md'
    (args['plans_dir']/'step.md').rename(plan)
    args['dispatch']['subplan_path']=str(plan)
    master=args['plans_dir']/'MASTER-original.md'
    master.write_text('---\nstatus: active\nbase_sha: original-base\n---\n# MASTER\n| 1 | [step](./2026-10-12-step.md) |\n')
    args['dispatch'].update(slug='step',step=0,master_path=str(master),batch_base_sha='original-base')
    context=resolve_context(**args);base=context['base_commit']
    subprocess.run(['git','-C',str(args['exec_tree']),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','later'],check=True)
    (tmp_path/'other-active-master.md').write_text('status: active\nbase_sha: wrong\n')
    result=red_inputs(context,'step',0)
    assert result==dict(exec_tree=str(args['exec_tree']),iteration_base=base,batch_base='original-base',master_path=str(master))
    with pytest.raises(ContextError,match='step differs'):red_inputs(context,'step',1)
    with pytest.raises(ContextError,match='not unique'):red_inputs(context,'other',0)


def test_member_base_comes_from_frozen_observation_map(tmp_path):
    args=fixture(tmp_path);member=args['clone']/'member';member.mkdir()
    subprocess.run(['git','init','-q',str(member)],check=True)
    subprocess.run(['git','-C',str(member),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','member base'],check=True)
    (args['plans_dir']/'step.md').write_text('---\nrepo: member\ncurrent_step: 0\n---\n')
    args['observe_tree'][str(member)]=str(member)
    context=resolve_context(**args);base=context['repository_bases'][str(member)]
    subprocess.run(['git','-C',str(member),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','later member'],check=True)
    assert red_inputs(context,'step',0)['iteration_base']==base
    assert red_inputs(context,'step',0)['exec_tree']==str(member)
    context.pop('repository_bases')
    with pytest.raises(ContextError,match='not frozen'):red_inputs(context,'step',0)


def test_actual_bash_reader_uses_frozen_values_instead_of_legacy_clone_defaults(tmp_path):
    import os
    from iteration_context import write_context
    args=fixture(tmp_path);args['dispatch'].update(slug='step',step=0,batch_base_sha='frozen-batch-base')
    context=resolve_context(**args);frozen=tmp_path/'ctx.json';write_context(frozen,context)
    root=Path(__file__).resolve().parents[3]
    source=(root/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh').read_text()
    anchor=source.index('# Attribute each blocking record.')
    start=source.index('                local _effective_repo',anchor)
    end=source.index('                local _attr_args=',start)
    program='''_SKILL_ROOT="$SKILLS"
PROJECT_PATH="$CLONE"
_iteration_base=wrong-clone-base
_batch_base=wrong-active-master-base
_b_slug=step
_b_step=0
selfmod_effective_repo(){ echo "$PROJECT_PATH"; }
probe(){
'''+source[start:end]+'''\nprintf '%s\\n' "$_effective_repo" "$_attr_iteration_base" "$_attr_batch_base" "$_attr_input_valid"
}
probe
'''
    result=subprocess.run(['bash','-c',program],env=dict(os.environ,ILK_ITER_CONTEXT=str(frozen),SKILLS=str(root/'skills'),CLONE=str(args['clone'])),text=True,capture_output=True,timeout=5)
    assert result.returncode==0,result.stderr
    assert result.stdout.splitlines()==[str(args['exec_tree']),context['base_commit'],'frozen-batch-base','1']
