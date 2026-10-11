"""Exercise real launcher preflight without touching production state."""
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/launch.sh"
SKILLS = SCRIPT.parents[2]


def function(name):
    text = SCRIPT.read_text()
    return re.search(r"^" + name + r"\(\) \{.*?^\}", text, re.M | re.S).group()


def run(code, root=SKILLS):
    return subprocess.run(["bash", "-c", 'set -e\n_SKILL_ROOT="$1"\n' + code,
                           "probe", str(root)], capture_output=True, text=True, timeout=10)


def test_missing_engine_refused_before_launch_state():
    code = function("runner_script_for_engine") + "\n" + function("start_ilk_window")
    code += '\ntest_running_pid() { echo GUARD_REACHED >&2; return 1; }\n'
    code += 'start_ilk_window /unused probe 1 1 false false "" codex\n'
    result = run(code)
    assert result.returncode != 0
    assert "GUARD_REACHED" not in result.stderr
    assert "not packaged" in result.stderr


def test_advertised_engines_have_packaged_runners():
    engines = re.search(r'^VALID_ENGINES="([^"]+)"', SCRIPT.read_text(), re.M).group(1).split()
    for engine in engines:
        result = run(function("runner_script_for_engine") + '\nrunner_script_for_engine "' + engine + '"')
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()).is_file()


def test_known_runner_removed_refuses(tmp_path):
    result = run(function("runner_script_for_engine") + '\nrunner_script_for_engine claude-worker', tmp_path)
    assert result.returncode != 0
    assert "not packaged" in result.stderr


def test_powershell_packaged_engine_preflight(tmp_path):
    import os
    import shutil
    import pytest
    pwsh = os.environ.get("ILK_TEST_PWSH") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell runtime unavailable")
    source = SCRIPT.with_suffix(".ps1").read_text()
    definitions = "\n".join(re.search(r"^function " + name + r" \{.*?^\}", source, re.M | re.S).group()
                              for name in ("Get-RunnerScript", "Start-ilkWindow"))
    engines = re.search(r"^\$ValidEngines = (.*)$", source, re.M).group(1)
    probe = tmp_path / "probe.ps1"
    probe.write_text("$ErrorActionPreference = 'Stop'\n$SkillRoot = $args[0]\n" + definitions + "\n" +
        "$advertised = " + engines + "\n" +
        "foreach ($engine in $advertised) { $null = Get-RunnerScript $engine }\n" +
        "function Test-RunningPid { throw 'GUARD_REACHED' }\n" +
        "try { Start-ilkWindow -EngineName codex; throw 'unexpected acceptance' }\n" +
        "catch { if ($_.Exception.Message -notmatch 'not packaged') { throw } }\n" +
        "$SkillRoot = $args[1]\n" +
        "try { Get-RunnerScript claude-worker; throw 'unexpected acceptance' }\n" +
        "catch { if ($_.Exception.Message -notmatch 'not packaged') { throw } }\n" +
        "Write-Output 'PACKAGED_ENGINE_PREFLIGHT_PASS'\n")
    result = subprocess.run([pwsh, "-NoProfile", "-File", str(probe), str(SKILLS), str(tmp_path)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PACKAGED_ENGINE_PREFLIGHT_PASS" in result.stdout
