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
