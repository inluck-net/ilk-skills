"""Named instruction pinning detects ignored/nested, ancestor and added inputs."""
from pathlib import Path
import pytest
from instruction_inputs import inventory,verify
from iteration_context import ContextError
from test_owner_driver_step import fixture


@pytest.mark.parametrize('mutation',['edit','new','delete','symlink'])
def test_instruction_inventory_retains_nested_ignored_and_ancestor_inputs(tmp_path,mutation):
    context,root,plan=fixture(tmp_path)
    (root/'.gitignore').write_text('ignored/\n')
    target=root/'ignored/CLAUDE.md';target.parent.mkdir();target.write_text('nested instruction')
    ancestor=root.parent/'AGENTS.md';ancestor.write_text('ancestor instruction')
    context['instruction_input_inventory']=inventory(context)
    paths={row['path'] for row in context['instruction_input_inventory']}
    assert str(target) in paths and str(ancestor) in paths
    verify(context)
    if mutation=='edit':target.write_text('changed')
    elif mutation=='new':(root/'CLAUDE.local.md').write_text('new instructions')
    elif mutation=='delete':target.unlink()
    else:
        alternate=root/'other.md';alternate.write_text('nested instruction')
        target.unlink();target.symlink_to(alternate)
    with pytest.raises(ContextError,match='instruction inputs changed'):verify(context)


def test_recovery_instruction_observation_uses_supplied_driver_deadline(tmp_path):
    import driver_step
    from iteration_clock import monotonic
    context,root,plan=fixture(tmp_path)
    context['instruction_input_inventory']=inventory(context)
    context['iteration_deadline_monotonic']=monotonic()-1
    with pytest.raises(ContextError,match='deadline'):verify(context)
    token=driver_step._deadline.set(monotonic()+10)
    try:verify(context)
    finally:driver_step._deadline.reset(token)


@pytest.mark.parametrize('mutation',['edit','new','delete','symlink'])
def test_rule_inputs_are_frozen_including_ignored_and_ancestor(tmp_path,mutation):
    context,root,plan=fixture(tmp_path)
    target=root/'ignored/.claude/rules/guide.md';target.parent.mkdir(parents=True);target.write_text('frozen')
    (root/'.gitignore').write_text('ignored/\n')
    ancestor=root.parent/'.claude/rules/team.md';ancestor.parent.mkdir(parents=True);ancestor.write_text('team')
    context['instruction_input_inventory']=inventory(context)
    paths={row['path'] for row in context['instruction_input_inventory']}
    assert str(target) in paths and str(ancestor) in paths
    if mutation=='edit':target.write_text('changed')
    elif mutation=='new':(target.parent/'new.md').write_text('new')
    elif mutation=='delete':target.unlink()
    else:
        other=root/'ordinary.md';other.write_text('frozen');target.unlink();target.symlink_to(other)
    with pytest.raises(ContextError,match='instruction inputs changed'):verify(context)


def test_symlinked_rules_directory_observes_target_files(tmp_path):
    context,root,plan=fixture(tmp_path)
    shared=tmp_path/'shared-rules';shared.mkdir();target=shared/'guide.md';target.write_text('frozen')
    rules=root/'.claude/rules';rules.parent.mkdir();rules.symlink_to(shared,target_is_directory=True)
    context['instruction_input_inventory']=inventory(context)
    assert any(row['resolved_path']==str(target) for row in context['instruction_input_inventory'])
    target.write_text('changed')
    with pytest.raises(ContextError,match='instruction inputs changed'):verify(context)
