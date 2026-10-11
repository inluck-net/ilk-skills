"""Portable preflight refusal; no claim of native Windows enforcement."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest


def test_powershell_refuses_measured_red_before_runtime_writes(tmp_path):
    pwsh=os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:pytest.skip('explicit portable PowerShell runtime unavailable')
    repo=Path(__file__).resolve().parents[3]
    project=tmp_path/'project';project.mkdir()
    logs=tmp_path/'must-not-exist'
    binaries=tmp_path/'bin';binaries.mkdir()
    (binaries/'python').symlink_to(sys.executable)
    env={**os.environ,'PATH':str(binaries)+os.pathsep+os.environ['PATH'],'HOME':str(tmp_path/'home'),'ILK_DATA_HOME':str(tmp_path/'data'),
         'ILK_DATA_DIR':str(tmp_path/'data'),'ILK_SKILL_HOME':str(repo/'skills'),
         'ILK_MEASURED_RED_PROGRESS':'1'}
    for key in ('ILK_DOTSOURCE_ONLY','ILK_WORKER_MODE','ILK_WORKER_TOOLS','ILK_WORKER_THINKING','ILK_ENABLE_UPGRADE_DRAIN'):
        env.pop(key,None)
    result=subprocess.run([pwsh,'-NoProfile','-File',str(repo/'skills/ilk-loop/scripts/run_ilk_loop_claude.ps1'),
        '-ProjectPath',str(project),'-LogDir',str(logs),'-JsonlLogPath',str(logs/'summary.jsonl')],
        env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode!=0
    assert 'legacy default only' in result.stdout+result.stderr
    assert not logs.exists()
