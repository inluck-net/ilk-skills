"""Exercise actual core/launcher entry decisions; never launch a real worker."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / 'skills/ilk-loop/scripts/run_ilk_loop_claude.sh'
LAUNCHER = ROOT / 'skills/ilk-launcher/scripts/launch.sh'


def environment(tmp_path, *, mode=None, managed=True):
    env = dict(os.environ, HOME=str(tmp_path), ILK_SKILL_HOME=str(ROOT/'skills'),
               ILK_DATA_HOME=str(tmp_path/'data'), ILK_DATA_DIR=str(tmp_path/'data'))
    for key in ('ILK_WORKER_MODE', 'ILK_WORKER_TOOLS', 'ILK_RUN_INPUTS', 'ILK_CODE_ROOT'):
        env.pop(key, None)
    if mode is not None: env['ILK_WORKER_MODE'] = mode
    if managed: env['ILK_RUN_INPUTS'] = str(tmp_path/'frozen-inputs')
    return env


def core_entry(tmp_path, *, mode=None, managed=True, platform='Darwin'):
    script = '''
export ILK_DOTSOURCE_ONLY=1 ILK_RUN_LOCK_HELD=1
source "$1"
parse_args() { :; }
uname() { printf '%s\\n' "$TEST_PLATFORM"; }
preflight() { python3 -c 'import os,json;print(json.dumps(os.environ.get("ILK_WORKER_MODE")))'; exit 0; }
main
'''
    env = environment(tmp_path, mode=mode, managed=managed)
    env['TEST_PLATFORM'] = platform
    # Existing10s launcher-fixture allowance. These read-only entry stubs
    # normally complete <1s; slower healthy startup would falsify that basis.
    return subprocess.run(['bash', '-c', script, 'fixture', str(CORE)],
                          env=env, capture_output=True, text=True, timeout=10)


def test_core_managed_mac_defaults_to_owned_profile(tmp_path):
    result = core_entry(tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == 'code-only'


@pytest.mark.parametrize('managed,platform', [(False, 'Darwin'), (True, 'Linux')])
def test_unprepared_or_nonmac_entry_retains_declared_legacy_path(tmp_path, managed, platform):
    result = core_entry(tmp_path, managed=managed, platform=platform)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == 'legacy'


@pytest.mark.parametrize('mode', ['', 'legacy'])
def test_core_explicit_legacy_selection_is_preserved(tmp_path, mode):
    result = core_entry(tmp_path, mode=mode)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == mode


def test_core_unknown_profile_refuses_before_preflight(tmp_path):
    result = core_entry(tmp_path, mode='typo')
    assert result.returncode != 0 and not result.stdout
    assert 'Unsupported worker mode' in result.stderr


def test_launcher_resolves_each_engine_without_leaking_a_previous_default(tmp_path):
    capture = tmp_path/'profiles.jsonl'
    script = '''
export ILK_SKIP_MAIN=1
source "$1"
uname() { printf 'Darwin\\n'; }
runner_script_for_engine() {
  python3 -c 'import os,json;open(os.environ["CAPTURE"],"a").write(json.dumps([os.environ.get("ILK_WORKER_MODE"),os.environ.get("ILK_WORKER_TOOLS")])+"\\n")'
  return 1
}
start_ilk_window project fixture 1 60 false false '' claude-worker '' true || :
start_ilk_window project fixture 1 60 false false '' claude-manager '' true || :
test -z "${ILK_WORKER_MODE+x}" && test -z "${ILK_WORKER_TOOLS+x}"
'''
    env = environment(tmp_path, managed=False)
    env['CAPTURE'] = str(capture)
    result = subprocess.run(['bash', '-c', script, 'fixture', str(LAUNCHER)],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert [json.loads(line) for line in capture.read_text().splitlines()] == [
        ['code-only', 'native'], ['legacy', 'shell']]
