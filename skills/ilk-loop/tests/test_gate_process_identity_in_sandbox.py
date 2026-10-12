"""Gate fixtures need real PID identity without executing setuid /bin/ps."""
import json
import ctypes
import errno
import os
from pathlib import Path
import subprocess
import sys
import signal

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run_registry as registry


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS seatbelt regression")
def test_gate_reads_real_parent_identity_when_setuid_ps_is_denied():
    expected = registry.process_identity(os.getpid())
    # The production gate denies writes/network and foreign-process signals.
    # Keep these restrictions: the fix must use a read-only native probe.
    policy = ('(version 1)(allow default)(deny file-write*)(deny network*)'
              '(deny signal)(allow signal (target same-sandbox))')
    program = '''
import json,subprocess,sys
sys.path.insert(0,sys.argv[1])
import run_registry
try:
    subprocess.run(['/bin/ps','-p',sys.argv[2]],check=True,capture_output=True)
except PermissionError:
    pass
else:
    raise AssertionError('negative control: setuid ps unexpectedly allowed')
print(json.dumps(run_registry.process_identity(int(sys.argv[2]))))
'''
    # Judgment: 10s bounds this read-only probe, normally <1s. A healthy
    # native probe needing >10s would falsify this test-only allowance.
    result = subprocess.run(
        ['/usr/bin/sandbox-exec', '-p', policy, sys.executable, '-c',
         program, str(SCRIPTS), str(os.getpid())],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == expected


def _native_response(monkeypatch, *, state=2, count=None, error=0, observed_pid=271):
    class Query:
        def __call__(self, pid, flavor, arg, buffer, size):
            assert flavor == 3 and size == 136  # SDK ABI, not a tuning default.
            info = buffer._obj
            info.pbi_pid = observed_pid
            info.pbi_pgid = 271
            info.pbi_status = state
            info.pbi_start_tvsec = 1791781366
            ctypes.set_errno(error)
            return size if count is None else count
    class Library:
        proc_pidinfo = Query()
    monkeypatch.setattr(ctypes, 'CDLL', lambda *a, **kw: Library())


@pytest.mark.parametrize('state,live,group', [(2, True, True), (5, False, True)])
def test_native_zombie_never_grants_driver_authority(monkeypatch, state, live, group):
    _native_response(monkeypatch, state=state)
    assert bool(registry._darwin_process_identity(271, include_zombie=False)) == live
    assert bool(registry._darwin_process_identity(271, include_zombie=True)) == group


@pytest.mark.parametrize('count,error', [(0, errno.EPERM), (0, 0), (8, 0)])
def test_unknown_native_probe_refuses_instead_of_reporting_idle(monkeypatch, count, error):
    _native_response(monkeypatch, count=count, error=error)
    with pytest.raises(registry.RegistryError, match='denied or incomplete'):
        registry._darwin_process_identity(271, include_zombie=False)


def test_native_missing_process_is_distinct_from_denied_probe(monkeypatch):
    _native_response(monkeypatch, count=0, error=errno.ESRCH)
    assert registry._darwin_process_identity(271, include_zombie=False) is None


def test_native_probe_rejects_wrong_pid_and_abi_overflow(monkeypatch):
    _native_response(monkeypatch, observed_pid=272)
    with pytest.raises(registry.RegistryError, match='contradicts'):
        registry._darwin_process_identity(271, include_zombie=False)
    with pytest.raises(registry.RegistryError, match='ABI'):
        registry._darwin_process_identity(2**32+271, include_zombie=False)


def test_native_unknown_state_refuses_instead_of_reporting_idle(monkeypatch):
    _native_response(monkeypatch, state=6)
    with pytest.raises(registry.RegistryError, match="state unknown"):
        registry._darwin_process_identity(271, include_zombie=False)


@pytest.mark.skipif(sys.platform != 'darwin', reason='production Mac gate profile')
def test_nested_red_gates_keep_their_authority_and_scratch_in_gate_sandbox(tmp_path):
    from worker_sandbox import profile
    root = SCRIPTS.parents[2]
    control = tmp_path / 'control'
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    context = {name: str(control / name) for name in
               ('data_root', 'plans_dir', 'runtime_dir', 'code_root')}
    for path in context.values():
        Path(path).mkdir(parents=True)
    context.update(exec_tree=str(root), clone=str(root), mode='none')
    home = scratch / 'home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home), TMPDIR=str(scratch),
               PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=str(Path(pytest.__file__).parents[1]))
    for name in list(env):
        if name.startswith('ANTHROPIC_') or name in (
                'ILK_DATA_HOME', 'ILK_DATA_DIR', 'ILK_ITER_CONTEXT',
                'ILK_WORKER_MODE', 'ILK_WORKER_SESSION', 'ILK_RUN_INPUTS',
                'ILK_CODE_ROOT', 'ILK_CLAUDE_BIN', 'MCP_CONFIG_PATH',
                'ILK_RUN_LOCK_HELD', 'ILK_TOOLKIT_SOURCE_ROOT'):
            env.pop(name, None)
    prefix = 'skills/ilk-loop/tests/test_red_gate_stops_the_run.py::'
    nodes = [prefix + name for name in (
        'test_nested_driver_fixture_does_not_inherit_worker_authority',
        'test_red_gate_ends_the_run_as_a_ship_integrity_violation',
        'test_a_rejected_gate_clears_the_intent_for_its_own_slug')]
    # Existing project pytest bound is 60s; these three local stub fixtures
    # measured 48s in this sandbox replay. No production timeout is raised.
    process = subprocess.Popen(
        ['/usr/bin/sandbox-exec', '-p', profile(context, temporary=scratch, shell=True),
         sys.executable, '-m', 'pytest', *nodes, '-q', '--timeout-method=signal'],
        cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=60)
    except BaseException:
        # Only this fixture's freshly created group; no live workers.
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
        raise
    assert process.returncode == 0, stdout + stderr
    assert '3 passed' in stdout
