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
