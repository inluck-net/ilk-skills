import pytest
from provider_provenance import configured_provider

@pytest.mark.parametrize("url,origin", [
    ("https://user:secret@api.example:443/private/key?token=secret#secret", "https://api.example:443"),
    ("https://[::1]:8443/v1/messages?key=secret", "https://[::1]:8443"),
    ("", None), ("malformed-secret", None), ("https://host:secret", None),
])
def test_public_provenance_excludes_url_secrets(url, origin):
    row = configured_provider(url)
    assert row == {"origin": origin, "identity_basis": "configured URL; actual provider unobserved"}
    assert "secret" not in str(row)


def test_powershell_records_only_sanitized_configured_origin(tmp_path):
    import json
    import os
    import re
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    pwsh = os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:
        pytest.skip('PowerShell runtime unavailable')
    skills = Path(__file__).resolve().parents[2]
    source = (skills / 'ilk-loop/scripts/run_ilk_loop_claude.ps1').read_text()
    definition = re.search(r'^function Get-ProviderProvenance \{.*?^\}', source, re.M | re.S)
    assert definition, 'PowerShell needs the shared sanitized provenance adapter'
    assert 'base_url          = $env:ANTHROPIC_BASE_URL' not in source
    probe = tmp_path / 'provenance.ps1'
    probe.write_text("$ErrorActionPreference='Stop'\n$SkillRoot=$args[0]\nSet-Alias python $args[1]\n" +
        definition.group() + "\nGet-ProviderProvenance | ConvertTo-Json -Compress\n")
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'),
               ANTHROPIC_BASE_URL='https://user:secret@api.example:443/private/key?token=secret#secret')
    result = subprocess.run([pwsh, '-NoProfile', '-File', str(probe), str(skills), sys.executable],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == configured_provider(env['ANTHROPIC_BASE_URL'])
    assert 'secret' not in result.stdout + result.stderr
