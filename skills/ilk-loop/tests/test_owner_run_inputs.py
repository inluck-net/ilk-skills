import json
from pathlib import Path
import subprocess
import pytest
from run_inputs import prepare,InputPinError,digest


def fixture(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    (repo/'skills').mkdir();(repo/'commands').mkdir()
    (repo/'skills/keep').write_text('pinned')
    (repo/'hooks').mkdir();(repo/'hooks/worker-write-scope.py').write_text('# observed')
    wrapper=repo/'skills/ilk-loop/scripts';wrapper.mkdir(parents=True)
    (wrapper/'worker_shell.py').write_text('# pinned wrapper')
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','snapshot'],check=True)
    home=tmp_path/'active';home.mkdir();(home/'hooks').mkdir()
    hook=home/'hooks/guard.py';hook.write_text('# production hook');hook.chmod(0o700)
    settings={'env':{'ANTHROPIC_AUTH_TOKEN':'private-token','ANTHROPIC_MODEL':'fixture-model'},
              'hooks':{'PreToolUse':[{'matcher':'Write','hooks':[{'type':'command','command':str(hook)}]}]}}
    (home/'settings.json').write_text(json.dumps(settings))
    return repo,home,hook


def test_snapshot_and_private_hooks_do_not_follow_later_source_edits(tmp_path):
    repo,home,hook=fixture(tmp_path);original=(home/'settings.json').read_bytes()
    destination=tmp_path/'run';manifest=prepare(repo,home,destination,observe_scope=True)
    hook.write_text('# changed live hook');(repo/'skills/keep').write_text('changed live code')
    assert (destination/'code/skills/keep').read_text()=='pinned'
    assert digest(Path(manifest['hooks'][0]['pinned_script']))==manifest['hooks'][0]['pinned_sha256']
    assert 'private-token' not in (destination/'inputs.json').read_text()
    settings=destination/'worker-home/settings.json'
    assert json.loads(settings.read_text())['env']['ANTHROPIC_AUTH_TOKEN']=='private-token'
    assert settings.stat().st_mode & 0o777==0o600
    assert (home/'settings.json').read_bytes()==original
    assert len(manifest['hooks'])==2
    with pytest.raises(InputPinError,match='already exists'):prepare(repo,home,destination)


def test_unknown_hook_wrapper_is_refused_without_prepared_home(tmp_path):
    repo,home,_=fixture(tmp_path)
    data=json.loads((home/'settings.json').read_text())
    data['hooks']['PreToolUse'][0]['hooks'][0]['command']='echo ok'
    (home/'settings.json').write_text(json.dumps(data));destination=tmp_path/'run'
    with pytest.raises(InputPinError,match='explicit script'):prepare(repo,home,destination)
    assert not destination.exists()


def test_explicit_interpreter_hook_is_copied_and_rewritten(tmp_path):
    import sys
    repo,home,hook=fixture(tmp_path)
    data=json.loads((home/'settings.json').read_text())
    data['hooks']['PreToolUse'][0]['hooks'][0]['command']=f'/usr/bin/python3 {hook}'
    (home/'settings.json').write_text(json.dumps(data))
    manifest=prepare(repo,home,tmp_path/'run')
    settings=json.loads((tmp_path/'run/worker-home/settings.json').read_text())
    command=settings['hooks']['PreToolUse'][0]['hooks'][0]['command']
    assert str(hook) not in command
    assert manifest['hooks'][0]['pinned_script'] in command


def test_known_nested_sandbox_failure_cannot_activate_enforcement(tmp_path):
    import sys
    from run_inputs import bind_context
    from iteration_context import write_context
    repo,home,_=fixture(tmp_path);destination=tmp_path/'run';manifest=prepare(repo,home,destination,observe_scope=True)
    base=destination/'worker-home/settings.json';before=base.read_bytes()
    work=tmp_path/'consumer';work.mkdir();temporary=tmp_path/'private-tmp';temporary.mkdir()
    context=dict(schema_version=1,run_id='test',iteration=1,clone=str(work),exec_tree=str(work),
                 code_root=manifest['code_root'],plans_dir=str(tmp_path/'plans'),runtime_dir=str(tmp_path/'runtime'),
                 data_root=str(tmp_path/'data'),mode='none',worker_tmp_dir=str(temporary),
                 worker_shell_python=sys.executable,worker_sandbox_adapter='macos-two-layer')
    path=tmp_path/'ctx.json';write_context(path,context)
    with pytest.raises(InputPinError,match='nested Mac sandbox'):
        bind_context(destination,path)
    assert base.read_bytes()==before
    assert 'context_bindings' not in json.loads((destination/'inputs.json').read_text())


def test_brokered_binding_is_immutable_and_leaves_base_home_unchanged(tmp_path):
    import sys,time
    from run_inputs import bind_context
    from iteration_context import write_context
    from worker_broker import ShellBroker
    repo,home,_=fixture(tmp_path);destination=tmp_path/'run';manifest=prepare(repo,home,destination,observe_scope=True)
    base=destination/'worker-home/settings.json';before=base.read_bytes()
    work=tmp_path/'consumer';work.mkdir();temporary=tmp_path/'private-tmp';temporary.mkdir()
    context=dict(schema_version=1,run_id='test',iteration=1,clone=str(work),exec_tree=str(work),
                 code_root=manifest['code_root'],plans_dir=str(tmp_path/'plans'),runtime_dir=str(tmp_path/'runtime'),
                 data_root=str(tmp_path/'data'),mode='none',worker_tmp_dir=str(temporary),
                 worker_shell_python=sys.executable,private_worker_home=manifest['worker_home'],worker_inputs_dir=str(destination))
    with ShellBroker(context,deadline=time.monotonic()+10) as broker:
        context.update(broker.descriptor());path=tmp_path/'ctx.json';write_context(path,context)
        bound=bind_context(destination,path)
        assert base.read_bytes()==before and bound.stat().st_mode & 0o777==0o400
        settings=json.loads(bound.read_text());full=[entry for entry in settings['hooks']['PreToolUse'] if entry['matcher']=='*']
        assert len(full)==1 and '--mode enforce --context' in full[0]['hooks'][0]['command']
        assert 'private-token' not in (destination/'inputs.json').read_text()
        with pytest.raises(FileExistsError):bind_context(destination,path)


def test_release_pin_uses_manifest_commit_and_resource_hashes(tmp_path):
    from run_inputs import pin_launch_resources,verify_launch_resources
    repo,home,_=fixture(tmp_path)
    sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    release=tmp_path/'release';release.mkdir()
    (release/'.ilk-release.json').write_text(json.dumps({'sha':sha,'source_repo':str(repo)}))
    (repo/'skills/keep').write_text('later clone change')
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','later'],check=True)
    destination=tmp_path/'run';manifest=prepare(release,home,destination)
    assert manifest['toolkit_sha']==sha and (destination/'code/skills/keep').read_text()=='pinned'
    config=tmp_path/'mcp.json';config.write_text('{"mcpServers":{"example":{"env":{"TOKEN":"private-mcp"}}}}')
    binary=tmp_path/'runtime';binary.write_text('fixed executable')
    pin_launch_resources(destination,project=repo,mcp=config,executables={'fixture':binary})
    assert 'private-mcp' not in (destination/'inputs.json').read_text()
    verify_launch_resources(destination)
    config.write_text('{}') # live config edit does not alter pinned input
    verify_launch_resources(destination)
    binary.write_text('changed executable')
    with pytest.raises(InputPinError,match='changed'):verify_launch_resources(destination)


@pytest.mark.parametrize('quoted',[False,True])
def test_plan_script_uses_pinned_code_with_space_in_path(tmp_path,quoted):
    from run_local_checks import pin_gate_command
    code=tmp_path/'pinned code';script=code/'skills/ilk-loop/scripts/example.py';script.parent.mkdir(parents=True)
    script.write_text('print("pinned")')
    raw='~/.claude/skills/ilk-loop/scripts/example.py'
    command='python3 '+('"'+raw+'"' if quoted else raw)
    rewritten=pin_gate_command(command,{'ILK_CODE_ROOT':str(code),'HOME':str(tmp_path)})
    cp=subprocess.run(['bash','-c',rewritten],capture_output=True,text=True)
    assert cp.returncode==0 and cp.stdout.strip()=='pinned',cp.stderr
