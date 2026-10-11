"""Old migration entry points must observe launch reservations before moving data."""
import importlib.util
from pathlib import Path
import time
import subprocess
import pytest
from run_registry import reserve
from ilk_paths import project_key,external_launcher_dir
ROOT=Path(__file__).resolve().parents[3]


def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tools/migration'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('entry',['plans','plans-ignore','runtime'])
def test_migration_refuses_canonical_launch_reservation_without_pid(tmp_path,monkeypatch,entry):
    monkeypatch.setenv('ILK_DATA_HOME',str(tmp_path/'data'))
    monkeypatch.setenv('ILK_DATA_DIR',str(tmp_path/'data'))
    project=tmp_path/'project';project.mkdir()
    subprocess.run(['git','init','-q',str(project)],check=True)
    plans=project/'docs/plans';plans.mkdir(parents=True)
    (plans/'fixture.md').write_text('untouched')
    legacy=project/'.ilk-launcher';legacy.mkdir();(legacy/'evidence.json').write_text('{}')
    key=project_key(project);runtime=external_launcher_dir(key)
    # No polling: a future expiry ensures the reservation is launching.
    reserve(runtime,run_id='launching-fixture',project_key=key,clone=project,
            exec_tree=project,toolkit_sha='a'*40,expires_at=time.time()+60)
    before={str(p.relative_to(project)):p.read_bytes() for p in project.rglob('*') if p.is_file()}
    if entry=='runtime':
        module=load('migrate_project_runtime_dirs')
        result=module._migrate_one_project(project,True)
    else:
        module=load('migrate_plans_to_external')
        argv=['--project',str(project),'--apply']
        if entry=='plans-ignore':argv.append('--ignore-running')
        result=module.main(argv)
    assert result!=0
    assert {str(p.relative_to(project)):p.read_bytes() for p in project.rglob('*') if p.is_file()}==before


def test_migration_guard_refuses_unknown_registry(tmp_path,monkeypatch):
    import migration_guard
    from run_registry import RegistryError
    def unknown(*args,**kwargs):raise RegistryError('broken ownership record')
    monkeypatch.setattr(migration_guard,'project_status',unknown)
    result=migration_guard.status(tmp_path)
    assert result['busy'] is True and result['source']=='unverified-registry'


def test_idle_guard_reads_both_canonical_and_legacy_roots(tmp_path,monkeypatch):
    import migration_guard
    calls=[]
    def idle(runtime,**kwargs):
        calls.append((runtime,kwargs['project']))
        return {'busy':False,'source':'registry','runs':[]}
    monkeypatch.setattr(migration_guard,'project_status',idle)
    result=migration_guard.status(tmp_path)
    assert result['busy'] is False
    assert {root for root,_ in calls}=={external_launcher_dir(project_key(tmp_path)),tmp_path/'.ilk-launcher'}
    assert all(project==tmp_path.resolve() for _,project in calls)
