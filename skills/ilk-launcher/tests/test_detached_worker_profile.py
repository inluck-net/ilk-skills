"""The detached command must carry explicit profile selection itself."""
import json
import os
from pathlib import Path
import re
import subprocess

import pytest


@pytest.mark.parametrize('mode,drain', [('code-only', '1'), ('code-only', ''), ('', '')])
def test_profile_survives_an_empty_terminal_environment(tmp_path, mode, drain):
    source = (Path(__file__).resolve().parents[1]/'scripts/launch.sh').read_text()
    helper = re.search(r'^pinned_runner_command\(\) \{.*?^\}', source, re.M | re.S)
    assert helper
    runner = tmp_path/'fake runner.sh'
    runner.write_text("""#!/bin/bash
python3 -c 'import json,os;print(json.dumps({key:os.environ.get(key) for key in ("ILK_WORKER_MODE","ILK_ENABLE_UPGRADE_DRAIN","ILK_CODE_ROOT")}))'
""")
    args = ['home', 'skills', 'pinned code', 'inputs', 'source', 'home', 'cli', '',
            str(runner), 'project', '1', '2', 'mcp', 'logs', 'jsonl', 'true', 'false', mode, drain]
    command = subprocess.run(['bash', '-c', helper.group()+'\npinned_runner_command "$@"',
                              'fixture', *args], capture_output=True, text=True, check=True).stdout
    # A new Terminal need not inherit the source process's profile. Clearing
    # an ambient mode is also explicit; default launches cannot inherit opt-in.
    env = dict(PATH=os.environ['PATH'], HOME=str(tmp_path), ILK_WORKER_MODE='ambient-poison',
               ILK_ENABLE_UPGRADE_DRAIN='ambient-poison')
    result = subprocess.run(['bash', '-c', command], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(ILK_WORKER_MODE=mode,
        ILK_ENABLE_UPGRADE_DRAIN=drain, ILK_CODE_ROOT='pinned code')


def test_drain_cannot_be_enabled_for_a_legacy_detached_worker():
    source = (Path(__file__).resolve().parents[1]/'scripts/launch.sh').read_text()
    helper = re.search(r'^pinned_runner_command\(\) \{.*?^\}', source, re.M | re.S)
    args = ['value']*17+['', '1']
    result = subprocess.run(['bash', '-c', helper.group()+'\npinned_runner_command "$@"',
                             'fixture', *args], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and not result.stdout
    assert 'upgrade drain requires owned code-only dispatch' in result.stderr
