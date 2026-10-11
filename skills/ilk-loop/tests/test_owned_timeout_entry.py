"""Real archived owned entry reaches its original cap without an ungated commit."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from run_inputs import prepare, pin_launch_resources
from run_registry import project_status, signal_run

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Mac owned entry adapter')


def test_owned_deadline_keeps_diff_without_false_integrity_violation(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('timeout_fixture', Path(__file__).with_name('test_pre_loop_exit_sentinel.py'))
    fixtures = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
    world = fixtures._build_world_shipped(tmp_path, with_steps=True)
    work = world['project']
    def git(*args):
        return subprocess.check_output(['git', '-C', str(work), *args], text=True).strip()
    git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
    (work/'counter.py').write_text('value = 1\n'); git('add', '.'); git('commit', '-qm', 'fixture counter')
    head = git('rev-parse', 'HEAD')
    plan = world['plans']/(fixtures.STEM+'.md')
    plan.write_text('---\nplan: '+fixtures.SLUG+'\nstatus: pending\ncurrent_step: 0\nestimated_steps: 1\nverification_tier: loop-verified\n---\n# Counter\n## Steps\n### Step 0: fix counter\n```yaml\nlocal_checks:\n  - command: python3 -I -c "from pathlib import Path;assert Path(\'counter.py\').read_text()==\'value = 2\\n\'"\n```\n')
    home = tmp_path/'source-home'; home.mkdir()
    (home/'settings.json').write_text(json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'fixture-token',
        'ANTHROPIC_BASE_URL': 'https://example.invalid', 'ANTHROPIC_MODEL': 'fixture-model'}}))
    cli = world['bin']/'claude'
    cli.write_text('#!'+sys.executable+'\n'+"""import json,time
from pathlib import Path
Path('counter.py').write_text('value = 2\\n')
print(json.dumps({'type':'system','subtype':'init','session_id':'fixture','model':'fixture-model'}),flush=True)
time.sleep(120)
""")
    cli.chmod(0o700)
    inputs = tmp_path/'inputs'; manifest = prepare(ROOT, home, inputs, project=work, worker_tools='native')
    pin_launch_resources(inputs, project=work, executables={'python': sys.executable, 'claude': str(cli)})
    code = Path(manifest['code_root'])
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(world['data_home']),
        ILK_DATA_DIR=str(world['data_home']), ILK_SKILL_HOME=str(code/'skills'),
        ILK_CODE_ROOT=str(code), ILK_RUN_INPUTS=str(inputs), ILK_WORKER_MODE='code-only',
        CLAUDE_CONFIG_DIR=manifest['worker_home'], ILK_CLAUDE_BIN=str(cli),
        ILK_ITERATION_TIMEOUT_SEC='15',
        PATH=str(world['bin'])+os.pathsep+os.environ['PATH'])
    env.pop('PYTHONPATH', None)
    for name in ('ILK_SKIP_MAIN', 'ILK_ITER_CONTEXT', 'ILK_WORKER_SESSION', 'ILK_ITER_RECORD_STAGE'):
        env.pop(name, None)
    runtime = world['data_home']/'projects'/world['key']/'runtime/launcher'
    try:
        # Existing runner test affordance supplies a 15s original cap; the
        # 45s outer fixture bound is a judgment based on sampled ~12s startup
        # plus cleanup. A valid slower close falsifies this fixture bound.
        result = subprocess.run(['bash', str(code/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh'),
            '--project-path', str(work), '--max-iterations', '1',
            '--iteration-timeout-min', '1', '--run-local-checks',
            '--mcp-config-path', str(inputs/'mcp.json')], env=env,
            capture_output=True, text=True, timeout=45, start_new_session=True)
        (tmp_path/'entry.stdout').write_text(result.stdout)
        (tmp_path/'entry.stderr').write_text(result.stderr)
        terminal = json.loads((runtime/'last-exit.json').read_text())
        assert terminal['state'] == 'timeout', (terminal, result.stderr[-1600:])
        assert git('rev-parse', 'HEAD') == head
        assert (work/'counter.py').read_text() == 'value = 2\n'
        assert 'current_step: 0' in plan.read_text() and 'status: pending' in plan.read_text()
        rows = [json.loads(line) for line in (world['data_home']/'projects'/world['key']/'logs/.ilk-loop.log').read_text().splitlines()]
        closed = [row for row in rows if row.get('record_phase') == 'close']
        assert len(closed) == 1 and closed[0]['stop_reason'] == 'timeout'
        assert closed[0]['driver_integrity_passed'] is False
        assert closed[0]['state_audit']['explained'] is None
        assert not project_status(runtime)['busy']
    finally:
        if runtime.exists():
            for row in project_status(runtime)['runs']:
                if row['liveness'] == 'running':
                    signal_run(runtime, row['run_id'])
