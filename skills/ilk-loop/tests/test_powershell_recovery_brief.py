"""Exercise the portable PowerShell recovery adapters, without a provider."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


def test_frozen_brief_retains_interrupted_diagnostics_without_queue_discovery(tmp_path):
    pwsh = os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:
        pytest.skip('PowerShell runtime unavailable')
    skills = Path(__file__).resolve().parents[2]
    runner = (skills/'ilk-loop/scripts/run_ilk_loop_claude.ps1').read_text()
    definitions = []
    for name in ('Get-FrozenWorkerBrief', 'Save-InterruptedWorkerDiagnostic'):
        match = re.search(r'^function ' + name + r' \{.*?^\}', runner, re.M | re.S)
        assert match, 'missing PowerShell recovery adapter: ' + name
        definitions.append(match.group())
    # The frozen identity is usable even if queue discovery is now impossible.
    plan = tmp_path/'plans/deleted-plan.md'
    dispatch = dict(slug='work', step=0, subplan_path=str(plan),
                    master_path=str(plan.parent/'deleted-master.md'), batch_base_sha='base123')
    context = dict(schema_version=1, run_id='old', iteration=1, dispatch=dispatch,
                   recovery_dir=str(tmp_path/'recovery'))
    for name in ('clone', 'exec_tree', 'plans_dir', 'runtime_dir', 'data_root', 'code_root'):
        context[name] = str(tmp_path/name)
    context['plans_dir'] = str(plan.parent)
    context['plans'] = {plan.name: dict(path=str(plan))}
    frozen = tmp_path/'ctx.json'; frozen.write_text(json.dumps(context))
    stream = tmp_path/'worker.log.jsonl'
    stream.write_text(json.dumps(dict(type='assistant', message=dict(content=[
        dict(type='text', text='diagnostic context'),
        dict(type='tool_use', id='unfinished', name='Bash')
    ]))) + '\n' + '{unfinished stream\n')
    probe = tmp_path/'probe.ps1'
    probe.write_text("$ErrorActionPreference='Stop'\n$SkillRoot=$args[0]\nSet-Alias python $args[1]\n"
        + '\n'.join(definitions)
        + '\n$c=Get-Content -Raw $env:ILK_ITER_CONTEXT | ConvertFrom-Json\n'
        + 'Save-InterruptedWorkerDiagnostic -LogFile $args[2] -Context $c\nGet-FrozenWorkerBrief\n')
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'), ILK_ITER_CONTEXT=str(frozen))
    env.pop('ILK_WORKER_SESSION', None)
    result = subprocess.run([pwsh, '-NoProfile', '-File', str(probe), str(skills),
                             sys.executable, str(stream)[:-6]], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(plan) in result.stdout
    assert 'Interrupted worker diagnostic context:' in result.stdout
    assert 'rerun required gates after changes' in result.stdout
    salvage = json.loads((Path(context['recovery_dir'])/'last-worker-salvage.json').read_text())
    assert salvage['diagnostic_only'] is True
    assert salvage['dispatch'] == dispatch
    assert salvage['pending_tools'] == [dict(id='unfinished', name='Bash')]
    assert salvage['malformed_rows'] == 1
    assert not any(key in salvage for key in ('proof', 'all_passed', 'accepted'))
    assert not plan.exists(), 'formatting must not reconstruct or mutate a plan'
    assert '$dispatchContext.Brief = Get-FrozenWorkerBrief' in runner
    assert 'Save-InterruptedWorkerDiagnostic -LogFile $iterLog -Context $iterationContext' in runner


def test_powershell_confirmation_uses_plan_identity_and_retains_both_raw_results(tmp_path):
    pwsh = os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:
        pytest.skip('PowerShell runtime unavailable')
    skills = Path(__file__).resolve().parents[2]
    runner = (skills/'ilk-loop/scripts/run_ilk_loop_claude.ps1').read_text()
    definition = re.search(r'^function Confirm-LocalCheckAttempts \{.*?^\}', runner, re.M | re.S)
    assert definition
    first = [dict(slug=slug, step=0, outcome='fail', raw={'output': slug+'-first'})
             for slug in ('a', 'b')]
    retry = [dict(slug='a', step=0, outcome='pass', raw={'output': 'a-retry'}),
             dict(slug='b', step=0, outcome='inconclusive', raw={'output': 'b-retry'})]
    data = tmp_path/'data.json'; data.write_text(json.dumps(dict(first=first, retry=retry)))
    probe = tmp_path/'confirm.ps1'
    probe.write_text("$ErrorActionPreference='Stop'\n$SkillRoot=$args[0]\nSet-Alias python $args[1]\n"
        + definition.group() + '\n$d=Get-Content -Raw $args[2] | ConvertFrom-Json\n'
        + 'Confirm-LocalCheckAttempts -First $d.first -Rerun $d.retry | ConvertTo-Json -Depth 40 -Compress\n')
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'private-data'),
               ILK_DATA_DIR=str(tmp_path/'private-data'))
    result = subprocess.run([pwsh, '-NoProfile', '-File', str(probe), str(skills),
                             sys.executable, str(data)], env=env, capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed['Decision']['blocked'] and observed['Decision']['inconclusive']
    records = {r['slug']: r for r in observed['Records']}
    assert records['a']['outcome'] == 'pass' and records['a']['verdict'] == 'flaky'
    assert records['b']['outcome'] == 'inconclusive'
    for slug, record in records.items():
        assert [a['raw']['output'] for a in record['attempt_records']] == [slug+'-first', slug+'-retry']
