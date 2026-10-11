from iteration_clock import monotonic
import json
from pathlib import Path
import subprocess
import sys
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


def test_project_sources_preserve_hooks_restrictions_and_do_not_follow_edits(tmp_path):
    repo,home,_=fixture(tmp_path)
    directory=repo/'.claude';directory.mkdir()
    (repo/'guard.py').write_text('# team guard')
    shared=directory/'settings.json';local=directory/'settings.local.json'
    shared.write_text(json.dumps({'hooks':{'PreToolUse':[{'matcher':'Bash','hooks':[
        {'type':'command','command':'python3 guard.py'}]}]},
        'permissions':{'deny':['Read(secret)']},'env':{'TEAM':'shared'},'model':'team-model'}))
    local.write_text(json.dumps({'permissions':{'deny':['Write(proof)']},'env':{'TEAM':'local'}}))
    destination=tmp_path/'run';manifest=prepare(repo,home,destination,project=repo)
    settings=json.loads((destination/'worker-home/settings.json').read_text())
    assert settings['permissions']['deny']==['Read(secret)','Write(proof)']
    assert settings['env']['TEAM']=='local' and settings['model']=='team-model'
    assert len(settings['hooks']['PreToolUse'])==2
    assert manifest['cli_setting_sources']==''
    shared.write_text('{}');local.write_text('{}');(repo/'guard.py').write_text('# later')
    assert json.loads((destination/'worker-home/settings.json').read_text())==settings
    assert all(digest(Path(row['pinned_script']))==row['pinned_sha256'] for row in manifest['hooks'])
    assert 'private-token' not in (destination/'inputs.json').read_text()


@pytest.mark.parametrize('setting',[{'permissions':{'defaultMode':'bypassPermissions'}},
                                   {'modelSettings':{}},{'env':{'OTEL_ENDPOINT':'private'}}])
def test_unsupported_project_semantics_refuse_before_creating_home(tmp_path,setting):
    repo,home,_=fixture(tmp_path);directory=repo/'.claude';directory.mkdir()
    (directory/'settings.json').write_text(json.dumps(setting))
    destination=tmp_path/'run'
    with pytest.raises(InputPinError,match='adapter'):prepare(repo,home,destination,project=repo)
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
    with ShellBroker(context,deadline=monotonic()+10) as broker:
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


def test_thinking_profile_is_explicit_and_requires_adapter_in_selected_snapshot(tmp_path):
    repo,home,_=fixture(tmp_path)
    data=json.loads((home/'settings.json').read_text())
    data['env'].update(ANTHROPIC_MODEL='mimo-v2.6-pro',
                       ANTHROPIC_BASE_URL='https://token-plan-cn.xiaomimimo.com/anthropic')
    (home/'settings.json').write_text(json.dumps(data))
    destination=tmp_path/'unsupported'
    with pytest.raises(InputPinError,match='absent in pinned revision'):
        prepare(repo,home,destination,worker_thinking='disabled')
    assert not destination.exists()
    helper=repo/'skills/ilk-loop/scripts/provider_control.py';helper.write_text('# supported fixture adapter')
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','adapter'],check=True)
    manifest=prepare(repo,home,tmp_path/'supported',worker_thinking='disabled')
    assert manifest['worker_thinking']=='disabled'
    assert prepare(repo,home,tmp_path/'default')['worker_thinking']=='default'


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


def test_actual_launcher_runs_from_private_inputs_without_changing_source_home(tmp_path,monkeypatch):
    import importlib.util,os,sys,time
    from ilk_paths import external_launcher_dir
    from run_registry import project_status,signal_run
    spec=importlib.util.spec_from_file_location('preloop_fixture',Path(__file__).with_name('test_pre_loop_exit_sentinel.py'))
    fixture_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture_module)
    root=tmp_path/'world';root.mkdir();world=fixture_module._build_world_shipped(root,with_steps=True)
    source_home=root/'source-home';source_home.mkdir()
    settings=source_home/'settings.json';settings.write_text(json.dumps({'env':{'ANTHROPIC_AUTH_TOKEN':'fixture-private-token','ANTHROPIC_BASE_URL':'https://example.invalid','ANTHROPIC_MODEL':'fixture-model'}}))
    original=settings.read_bytes()
    monkeypatch.setenv('ILK_DATA_HOME',str(world['data_home']));monkeypatch.setenv('ILK_DATA_DIR',str(world['data_home']))
    runtime=external_launcher_dir(world['key']);scripts=Path(__file__).parents[2]
    env=dict(os.environ,HOME=str(root),ILK_SKIP_MAIN='1',ILK_SKILL_HOME=str(scripts),PATH=str(world['bin'])+os.pathsep+os.environ['PATH'])
    call='source "$1"; start_ilk_window "$2" fixture 1 2 false false "" claude-worker "$3" true ""'
    try:
        cp=subprocess.run(['bash','-c',call,'fixture',str(scripts/'ilk-launcher/scripts/launch.sh'),str(world['project']),str(source_home)],env=env,capture_output=True,text=True,timeout=20)
        assert cp.returncode==0,(cp.stdout,cp.stderr)
        launch=json.loads((runtime/'last-launch.json').read_text())
        # Judgment: 20s observation bound for sampled ~1s pre-loop completion;
        # a slower valid startup falsifies this fixture, not a production cap.
        until=monotonic()+20
        terminal={}
        while monotonic()<until:
            if (runtime/'last-exit.json').exists():
                terminal=json.loads((runtime/'last-exit.json').read_text())
                if terminal.get('ended_at') and not project_status(runtime)['busy']:break
            time.sleep(0.05)
        assert terminal.get('state')=='shipped-unproven',(terminal,(Path(launch['log_file']).read_text()[-2000:]))
        assert terminal['toolkit_sha']==launch['toolkit_sha']
        assert terminal['run_id']==launch['run_id']
        inputs=Path(launch['log_dir'])/'inputs';manifest=json.loads((inputs/'inputs.json').read_text())
        assert Path(launch['loop_script']).is_relative_to(inputs/'code')
        assert 'fixture-private-token' not in (inputs/'inputs.json').read_text()
        assert settings.read_bytes()==original
        assert not list((runtime/'runs').glob('*.json'))
    finally:
        if runtime.exists():
            for row in project_status(runtime)['runs']:
                if row['liveness']=='running':signal_run(runtime,row['run_id'])


def test_global_instructions_are_private_frozen_inputs_not_live_home_links(tmp_path):
    from run_inputs import pin_launch_resources,verify_launch_resources
    repo,home,_=fixture(tmp_path)
    (home/'CLAUDE.md').write_text('original global instructions')
    destination=tmp_path/'run';manifest=prepare(repo,home,destination)
    pin_launch_resources(destination,project=repo,executables={'python':sys.executable,'claude':sys.executable})
    (home/'CLAUDE.md').write_text('later source edit')
    copied=Path(manifest['global_instruction_inputs'][0]['path'])
    assert copied.read_text()=='original global instructions'
    verify_launch_resources(destination)
    copied.chmod(0o600);copied.write_text('altered private instructions')
    with pytest.raises(InputPinError,match='changed'):verify_launch_resources(destination)


@pytest.mark.parametrize('mutation',['edit','add','manifest'])
def test_complete_code_inventory_detects_changed_or_added_runtime_inputs(tmp_path,mutation):
    from run_inputs import verify_code_snapshot
    repo,home,_=fixture(tmp_path);destination=tmp_path/'run';manifest=prepare(repo,home,destination)
    expected=manifest['code_inventory_sha256'];verify_code_snapshot(destination,expected=expected)
    code=Path(manifest['code_root'])
    cache=code/'__pycache__';cache.mkdir();(cache/'generated.pyc').write_bytes(b'fixture cache')
    verify_code_snapshot(destination,expected=expected)
    if mutation=='edit':(code/'skills/keep').write_text('altered snapshot')
    elif mutation=='add':(code/'unexpected_import.py').write_text('new runtime input')
    else:
        data=json.loads((destination/'inputs.json').read_text());data['code_inventory']=[]
        (destination/'inputs.json').write_text(json.dumps(data))
    with pytest.raises(InputPinError,match='changed'):verify_code_snapshot(destination,expected=expected)
