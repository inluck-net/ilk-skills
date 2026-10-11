"""A failed check alone cannot identify a subplan to strike."""
import json
import subprocess
import sys
from pathlib import Path
import pytest

@pytest.mark.parametrize('attribution,expected', [
    (None, []),
    ({'verdict':'unmeasured'}, []),
    ({'verdict':'owned','owner_slug':'other','owner_sha':None}, []),
    ({'verdict':'owned','owner_slug':'other','owner_sha':'a'*40,'iteration_base':'b'*40}, []),
    ({'verdict':'owned','owner_slug':'other','owner_sha':'a'*40,'iteration_base':'b'*40,'batch_base':'c'*40}, ['other']),
])
def test_actual_strike_target_reader(tmp_path, attribution, expected):
    rows=tmp_path/'checks'
    rows.write_text(json.dumps(dict(slug='victim',step=0,outcome='fail',verdict='red',attribution=attribution))+'\n')
    script=Path(__file__).resolve().parents[1]/'scripts/blocking_checks.py'
    result=subprocess.run([sys.executable,str(script),str(rows),'--strike-targets'],text=True,capture_output=True,timeout=5)
    assert result.returncode==0,result.stderr
    assert result.stdout.splitlines()==expected


def test_bash_caller_does_not_strike_when_attribution_is_unknown(tmp_path):
    import re, os
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    source=(scripts/'run_ilk_loop_claude.sh').read_text()
    command=re.search(r'^                q_slugs=\$\(.*\)$',source,re.M).group()
    rows=tmp_path/'checks';rows.write_text(json.dumps(dict(slug='victim',step=0,outcome='fail',verdict='red',attribution={'verdict':'unmeasured'}))+'\n')
    program='blocking_checks_script="$SCRIPT"\nlocal_checks_results="$ROWS"\n'+command+'\n[[ -z "$q_slugs" ]]\n'
    result=subprocess.run(['bash','-c',program],env=dict(os.environ,SCRIPT=str(scripts/'blocking_checks.py'),ROWS=str(rows)),capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr


def test_portable_powershell_reader_requires_measured_attribution(tmp_path):
    import re, os, shutil
    pwsh=os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:pytest.skip('PowerShell runtime unavailable')
    skills=Path(__file__).resolve().parents[2]
    runner=(skills/'ilk-loop/scripts/run_ilk_loop_claude.ps1').read_text()
    definition=re.search(r'^function Get-MeasuredStrikeTargets \{.*?^\}',runner,re.M|re.S).group()
    probe=tmp_path/'probe.ps1'
    probe.write_text("$ErrorActionPreference='Stop'\n$SkillRoot=$args[0]\nSet-Alias python $args[1]\n"+definition+"\n$rows=@([PSCustomObject]@{slug='victim';step=0;outcome='fail';verdict='red'})\nif (@(Get-MeasuredStrikeTargets -Records $rows).Count -ne 0) {throw 'invented strike'}\n$rows[0] | Add-Member attribution ([PSCustomObject]@{verdict='owned';owner_slug='actual-owner';owner_sha=('a'*40);iteration_base=('b'*40);batch_base=('c'*40)})\nif ((@(Get-MeasuredStrikeTargets -Records $rows) -join ',') -ne 'actual-owner') {throw 'lost measured owner'}\nWrite-Output 'measured strikes: PASS'\n")
    result=subprocess.run([pwsh,'-NoProfile','-File',str(probe),str(skills),sys.executable],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'measured strikes: PASS' in result.stdout
