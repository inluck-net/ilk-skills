"""Execute legacy caller control flow with private gate records and seams."""
import json
import os
from pathlib import Path
import re
import subprocess

import pytest


@pytest.mark.parametrize('initial', ['fail', 'inconclusive'])
def test_unmeasured_gate_never_enters_red_actions_or_green_ship(tmp_path, initial):
    root = Path(__file__).resolve().parents[3]
    source = (root/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh').read_text()
    start = source.index('      local blocking_checks_script=')
    end = source.index('    # Evaluate gate_outcome NOW', start)
    # The last fi belongs to the enclosing RUN_LOCAL_CHECKS conditional.
    control = source[start:end].rsplit('    fi', 1)[0]
    shipping = re.search(r'^post_iteration_ship\(\) \{.*?^\}', source, re.M | re.S).group()
    first = tmp_path/'first.jsonl'; retry = tmp_path/'retry.jsonl'
    first.write_text(json.dumps(dict(slug='work', step=0, command='check', outcome=initial))+'\n')
    retry.write_text(json.dumps(dict(slug='work', step=0, command='check', outcome='inconclusive'))+'\n')
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'), FIRST=str(first), RETRY=str(retry),
               SKILLS=str(root/'skills'))
    program = '''set -euo pipefail
_SKILL_ROOT="$SKILLS"
local_checks_results="$FIRST"
iter_stop_reason=''
PROJECT_PATH="$HOME"
LOCAL_CHECKS_SCRIPT=unused
LOCAL_CHECKS_TIMEOUT_SEC=1
selfmod_effective_repo() { echo "$1"; }
invoke_local_checks() { cp "$RETRY" "$5"; }
get_plans_dir() { echo 'UNEXPECTED RED ATTRIBUTION' >&2; exit 90; }
write_ship_proof_records() { echo 'UNEXPECTED GREEN PROOF' >&2; exit 91; }
driver_ship_if_complete() { echo 'UNEXPECTED SHIP' >&2; exit 92; }
caller() {
''' + control + '''
}
caller
[[ "$iter_stop_reason" == local_checks_inconclusive ]]
''' + shipping + '''
post_iteration_ship work inconclusive 2 1
echo 'inconclusive caller: PASS'
'''
    result = subprocess.run(['bash', '-c', program], env=env, capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'inconclusive caller: PASS' in result.stdout
    assert 'UNEXPECTED' not in result.stdout+result.stderr
    if initial == 'fail':
        observed = json.loads(first.read_text())
        assert observed['attempts'] == ['fail', 'inconclusive']
        assert len(observed['attempt_records']) == 2
