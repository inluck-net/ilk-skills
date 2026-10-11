"""Portable scheduler readers prefer the current launcher sentinel location."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import pytest


def test_scheduler_reads_launcher_sentinel_before_legacy_copy(tmp_path):
    pwsh = os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:
        pytest.skip('PowerShell runtime unavailable')
    root = Path(__file__).resolve().parents[3]
    runner = (root/'skills/ilk-watchdog/scripts/scheduler.ps1').read_text()
    definitions = []
    for name in ('Get-LauncherSentinelPath', 'Test-RunningPid'):
        match = re.search(r'^function '+name+r' \{.*?^\}', runner, re.M | re.S)
        assert match, name+' needs a portable scheduler reader'
        definitions.append(match.group())
    probe = tmp_path/'scheduler.ps1'
    probe.write_text("$ErrorActionPreference='Stop'\n"+'\n'.join(definitions)+"""
$data=$args[0]
$launcher=Join-Path $data 'runtime/launcher'
New-Item -ItemType Directory -Path $launcher -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $launcher 'running.pid'), [string]$PID)
$canonical=Join-Path $launcher 'last-exit.json'
$old=Join-Path $data 'runtime/last-exit.json'
[IO.File]::WriteAllText($old, '{"state":"shipped"}')
[IO.File]::WriteAllText($canonical, '{"state":"running"}')
if ((Get-LauncherSentinelPath $data) -ne $canonical) { throw 'wrong sentinel path' }
if (-not (Test-RunningPid $data)) { throw 'legacy terminal freed a current running controller' }
[IO.File]::WriteAllText($canonical, '{"state":"local_checks_inconclusive"}')
if (Test-RunningPid $data) { throw 'canonical terminal was not read' }
Remove-Item -LiteralPath $canonical
if ((Get-LauncherSentinelPath $data) -ne $old) { throw 'legacy migration fallback missing' }
'canonical scheduler sentinel: PASS'
""")
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'))
    result = subprocess.run([pwsh, '-NoProfile', '-File', str(probe), str(tmp_path/'project')],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'canonical scheduler sentinel: PASS' in result.stdout
