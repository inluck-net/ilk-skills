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


@pytest.mark.parametrize('mutation',['edit','new-missing','delete','symlink'])
def test_referenced_instruction_inputs_are_frozen(tmp_path,mutation):
    context,root,plan=fixture(tmp_path);context['instruction_input_inventory_protocol']=2
    imported=root/'guides/team notes.md';imported.parent.mkdir();imported.write_text('Team guidance\n@../shared.txt\n')
    shared=root/'shared.txt';shared.write_text('original imported guidance')
    missing=root/'future.md'
    (root/'CLAUDE.md').write_text('Guidance @guides/team\\ notes.md and @future.md\n')
    context['instruction_input_inventory']=inventory(context)
    assert any(row['resolved_path']==str(shared) for row in context['instruction_input_inventory'])
    assert any(row['path']==str(missing) and row.get('present') is False for row in context['instruction_input_inventory'])
    if mutation=='edit':shared.write_text('changed')
    elif mutation=='new-missing':missing.write_text('new imported instruction')
    elif mutation=='delete':shared.unlink()
    else:
        alternate=root/'other.txt';alternate.write_text(shared.read_text());shared.unlink();shared.symlink_to(alternate)
    with pytest.raises(ContextError,match='instruction inputs changed'):verify(context)


def test_import_code_examples_are_literal_and_manual_cycles_are_bounded(tmp_path):
    context,root,plan=fixture(tmp_path);context['instruction_input_inventory_protocol']=2
    (root/'CLAUDE.md').write_text('`@literal.md`\n```\n@fenced.md\n```\n@level1.md\n')
    for level in range(1,6):(root/('level'+str(level)+'.md')).write_text('@level'+str(level+1)+'.md\n')
    (root/'level5.md').write_text('@level1.md\n')
    paths={row['path'] for row in inventory(context)}
    assert str(root/'level4.md') in paths
    assert str(root/'level5.md') in paths
    assert str(root/'literal.md') not in paths and str(root/'fenced.md') not in paths


def test_manual_bootstrap_consumes_global_ancestor_and_project_rules(tmp_path):
    from instruction_inputs import bootstrap_inventory
    context,root,plan=fixture(tmp_path)
    context['instruction_input_inventory_protocol']=2
    ancestor=root.parent/'CLAUDE.md';ancestor.write_text('ancestor instructions')
    project=root/'.claude/rules/team.md';project.parent.mkdir(parents=True);project.write_text('project rules')
    local=root/'CLAUDE.local.md';local.write_text('local instructions')
    nested=root/'nested/CLAUDE.md';nested.parent.mkdir();nested.write_text('nested instructions')
    home=tmp_path/'private-home';home.mkdir();global_file=home/'CLAUDE.md';global_file.write_text('global instructions')
    context['private_worker_home']=str(home)
    context['instruction_input_inventory']=inventory(context)
    row=next(row for row in context['instruction_input_inventory'] if row['path']==str(global_file))
    context['global_instruction_inventory']=[row]
    paths=[row['path'] for row in bootstrap_inventory(context)]
    assert paths[0]==str(global_file)
    assert str(ancestor) in paths and str(project) in paths and str(local) in paths
    assert str(nested) not in paths
    assert paths.index(str(ancestor))<paths.index(str(project))
