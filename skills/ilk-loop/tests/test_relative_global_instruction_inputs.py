"""Relative global imports retain semantics and immutable private copies."""
from pathlib import Path
import json,sys
import pytest
from test_owner_run_inputs import fixture
from run_inputs import prepare,pin_launch_resources,verify_launch_resources,InputPinError


def test_relative_global_imports_are_private_complete_and_stable(tmp_path):
    repo,home,_=fixture(tmp_path)
    (home/'CLAUDE.md').write_text('@guide.md\n')
    (home/'guide.md').write_text('global policy\n@nested/policy.md\n')
    (home/'nested').mkdir();(home/'nested/policy.md').write_text('nested policy\n@../guide.md\n')
    first=prepare(repo,home,tmp_path/'first')
    second=prepare(repo,home,tmp_path/'second')
    assert first['global_instruction_inventory_protocol']==2
    for manifest,destination in ((first,tmp_path/'first'),(second,tmp_path/'second')):
        pin_launch_resources(destination,project=repo,executables={'python':sys.executable,'claude':sys.executable})
        verify_launch_resources(destination)
        copied=Path(manifest['worker_home'])
        assert (copied/'guide.md').read_text()==(home/'guide.md').read_text()
        assert (copied/'nested/policy.md').read_text()==(home/'nested/policy.md').read_text()
        assert {row['source_path'] for row in manifest['global_instruction_inputs']}=={str(home/name) for name in ('CLAUDE.md','guide.md','nested/policy.md')}
    from driver_step import gate_input_fingerprint
    from instruction_inputs import referenced_inventory
    from run_inputs import _global_instruction_paths
    fingerprints=[]
    for manifest,destination in ((first,tmp_path/'first'),(second,tmp_path/'second')):
        context={'exec_tree':str(repo),'base_commit':'a'*40,'toolkit_sha':manifest['toolkit_sha'],
                 'toolkit_code_inventory_sha256':manifest['code_inventory_sha256'],
                 'instruction_input_inventory':referenced_inventory(_global_instruction_paths(Path(manifest['worker_home']))),
                 'worker_inputs_dir':str(destination),'plans':{}}
        fingerprints.append(gate_input_fingerprint(context,{'slug':'fixture','step':0}))
    assert fingerprints[0]==fingerprints[1]
    (home/'guide.md').write_text('later source edit')
    assert 'global policy' in (Path(first['worker_home'])/'guide.md').read_text()
    verify_launch_resources(tmp_path/'first')
    copied=Path(first['worker_home'])/'guide.md';copied.chmod(0o600);copied.write_text('tampered')
    with pytest.raises(InputPinError):verify_launch_resources(tmp_path/'first')


@pytest.mark.parametrize('reference',['absolute','outside','missing','protected','symlink'])
def test_unsupported_global_import_roles_refuse_without_private_home(tmp_path,reference):
    repo,home,_=fixture(tmp_path)
    outside=tmp_path/'outside.md';outside.write_text('outside')
    if reference=='absolute':token=str(outside)
    elif reference=='outside':token='../outside.md'
    elif reference=='missing':token='missing.md'
    elif reference=='protected':token='settings.json'
    else:
        (home/'alias.md').symlink_to(outside);token='alias.md'
    (home/'CLAUDE.md').write_text('@'+token+'\n')
    destination=tmp_path/'run'
    with pytest.raises(InputPinError):prepare(repo,home,destination)
    assert not destination.exists()


def test_private_relative_import_graph_cannot_grow_unobserved(tmp_path):
    repo,home,_=fixture(tmp_path)
    (home/'CLAUDE.md').write_text('@guide.md\n');(home/'guide.md').write_text('policy')
    destination=tmp_path/'run';manifest=prepare(repo,home,destination)
    pin_launch_resources(destination,project=repo,executables={'python':sys.executable,'claude':sys.executable})
    private=Path(manifest['worker_home'])
    (private/'rules').mkdir();(private/'rules/extra.md').write_text('new input')
    with pytest.raises(InputPinError,match='inventory changed'):verify_launch_resources(destination)
