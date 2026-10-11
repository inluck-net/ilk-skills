"""Run the PowerShell JSON transport and clock adapter without a provider."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import pytest


@pytest.mark.parametrize('malformed',[False,True])
def test_powershell_phase_clocks_keep_missing_and_overlapping_intervals(tmp_path,malformed):
    pwsh=os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:pytest.skip('PowerShell runtime unavailable')
    skills=Path(__file__).resolve().parents[2]
    runner=(skills/'ilk-loop/scripts/run_ilk_loop_claude.ps1').read_text()
    definitions=[]
    for name in ('Get-PhaseClock','Get-DriverPhaseObservation'):
        match=re.search(r'^function '+name+r' \{.*?^\}',runner,re.M|re.S)
        assert match;definitions.append(match.group())
    probe=tmp_path/'probe.ps1'
    probe.write_text("$ErrorActionPreference='Stop'\n$SkillRoot=$args[0]\nSet-Alias python $args[1]\n"
        +'\n'.join(definitions)
        +'\n$a=Get-PhaseClock\n$b=Get-PhaseClock\nif ($b -lt $a) { throw "clock reversed" }\n'
        +('Remove-Item Alias:python\nfunction python { "not-json"; $global:LASTEXITCODE=0 }\n' if malformed else '')
        +'Get-DriverPhaseObservation -Boundaries @{ worker_turn=@(2,5); integrity=@(3,6); missing=@($null,$null) } | ConvertTo-Json -Depth 20 -Compress\n')
    env=dict(os.environ,HOME=str(tmp_path),ILK_DATA_HOME=str(tmp_path/'data'),ILK_DATA_DIR=str(tmp_path/'data'))
    # 15s is a fixture allowance for local runtime startup, not a loop default;
    # repeated valid startup overruns would show the allowance is too narrow.
    cp=subprocess.run([pwsh,'-NoProfile','-File',str(probe),str(skills),sys.executable],env=env,
        capture_output=True,text=True,timeout=15)
    assert cp.returncode==0,cp.stdout+cp.stderr
    result=json.loads(cp.stdout)
    if malformed:
        assert result['observation_complete'] is False and result['capture_error']
        return
    spans={row['name']:row for row in result['intervals']}
    assert spans['worker_turn']['duration_sec']==spans['integrity']['duration_sec']==3
    assert spans['missing']['duration_sec'] is None and spans['missing']['observation_complete'] is False
    assert all(row['clock_basis']=='powershell-stopwatch' for row in spans.values())
    assert result['identical_driver_gate_count'] is None and 'total_seconds' not in result
    assert "$jsonlRecord['driver_phase_observation'] = Get-DriverPhaseObservation" in runner
