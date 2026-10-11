"""Incomplete work and missing observation cannot fabricate tamper or proof."""
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh'


@pytest.mark.parametrize('audit_exit', [2, 3])
def test_actual_audit_control_preserves_unknown_separately_from_measured_tamper(tmp_path, audit_exit):
    source = RUNNER.read_text()
    start = source.index('    local _state_audit_json=""')
    end = source.index('    _integrity_ended=', start)
    control = source[start:end]
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'), AUDIT_EXIT=str(audit_exit),
               ILK_WORKER_MODE='code-only')
    program = """set -euo pipefail
_ILK_SCRIPT_DIR=unused
ILK_ITER_CONTEXT=unused
_master_tampered=0
_gates_tampered=0
_ship_integrity_passed=0
iter_stop_reason=timeout
stop_reason=timeout
python3() {
  if [[ "$AUDIT_EXIT" == 3 ]]; then echo '{"explained":false,"issues":[{"reason":"actual unexplained state"}]}'; fi
  return "$AUDIT_EXIT"
}
probe() {
""" + control + """
echo "$_state_audit_json"
}
probe
echo "$_master_tampered $iter_stop_reason $_ship_integrity_passed"
"""
    result = subprocess.run(['bash', '-c', program], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr
    lines = result.stdout.splitlines()
    observed = json.loads(lines[0])
    if audit_exit == 2:
        assert observed['explained'] is None and observed['observation_complete'] is False
        assert lines[1] == '0 timeout 0'
    else:
        assert observed['explained'] is False
        assert lines[1] == '1 ship_integrity_violation 0'


def test_owned_timeout_never_commits_ungated_wip(tmp_path):
    source = RUNNER.read_text()
    function = re.search(r'^preserve_dirty_tree_on_timeout\(\) \{.*?^\}', source, re.M | re.S).group()
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'), ILK_WORKER_MODE='code-only')
    program = "set -euo pipefail\ngit(){ echo 'unexpected Git mutation' >&2; exit 90; }\n" + function + "\npreserve_dirty_tree_on_timeout\n"
    result = subprocess.run(['bash', '-c', program], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == '0', result.stderr
    assert 'unexpected Git' not in result.stderr


def test_missing_acceptance_observes_the_original_deadline_without_ambient_imports(tmp_path):
    from iteration_clock import monotonic
    source = RUNNER.read_text()
    marker = '        if [[ "$ITER_COMPLETED" -eq 0 ]] && python3 -c '
    start = source.index(marker)
    end = source.index('        fi', start)+len('        fi')
    control = source[start:end]
    context = dict(schema_version=1, iteration_deadline_monotonic=monotonic()-1)
    for name in ('clone', 'exec_tree', 'plans_dir', 'runtime_dir', 'data_root', 'code_root'):
        context[name] = str(tmp_path/name)
    frozen = tmp_path/'context.json'; frozen.write_text(json.dumps(context))
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'), ILK_ITER_CONTEXT=str(frozen),
               SCRIPTS=str(ROOT/'skills/ilk-loop/scripts'))
    env.pop('PYTHONPATH', None)
    program = 'set -euo pipefail\nITER_COMPLETED=0\n_ILK_SCRIPT_DIR="$SCRIPTS"\n_driver_stop_reason=code_only_acceptance_failed\n'+control+'\necho "$_driver_stop_reason"\n'
    result = subprocess.run(['bash', '-c', program], env=env, capture_output=True, text=True, timeout=10, cwd=tmp_path)
    assert result.returncode == 0 and result.stdout.strip() == 'timeout', result.stderr
