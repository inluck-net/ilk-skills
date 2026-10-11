"""Unknown blame does not establish a green gate or excuse a red shipment."""
import pytest
from ship_integrity import evaluate_ship,check_final_step_gate

@pytest.mark.parametrize('attribution',[{'verdict':'unmeasured'},{'verdict':'owned','owner_slug':None}])
def test_unknown_attribution_cannot_excuse_a_raw_red(attribution):
    result=evaluate_ship('shipped',[{'command':'focused gate'}],
        dict(all_passed=False,results=[dict(command='focused gate',passed=False,exit_code=1)],attribution=attribution),gate_slug='fixture')
    assert not result.ok
    assert result.unmeasured


def test_unmeasured_node_attribution_is_not_a_final_pass():
    row=dict(slug='fixture',step=0,outcome='fail',
        attribution=dict(verdict='unmeasured',nodes=[dict(verdict='unmeasured')]))
    assert not check_final_step_gate([row],'fixture',0,None,dispatched=True)


def test_unmeasured_node_cannot_hide_under_preexisting_summary():
    row=dict(slug='fixture',step=0,outcome='fail',
        attribution=dict(verdict='pre-existing',nodes=[dict(verdict='pre-existing'),dict(verdict='unmeasured')]))
    assert not check_final_step_gate([row],'fixture',0,None,dispatched=True)


def test_unknown_node_cannot_excuse_integrity_red():
    attr=dict(verdict='pre-existing',nodes=[dict(verdict='unmeasured')])
    result=evaluate_ship('shipped',[dict(command='focused gate')],
        dict(all_passed=False,results=[dict(command='focused gate',passed=False,exit_code=1)],attribution=attr))
    assert not result.ok


def test_measured_green_does_not_require_ownership_attribution():
    result=evaluate_ship('shipped',[dict(command='focused gate')],
        dict(all_passed=True,results=[dict(command='focused gate',passed=True,exit_code=0)],
             attribution=dict(verdict='unmeasured')))
    assert result.ok and 'gate green' in result.reason


@pytest.mark.parametrize('verdict',['inherited','pre-existing'])
def test_known_nonowned_red_keeps_legacy_integrity_honesty(verdict):
    attr=dict(verdict=verdict,nodes=[dict(verdict=verdict)])
    result=evaluate_ship('shipped',[dict(command='focused gate')],
        dict(all_passed=False,attribution=attr))
    assert result.ok and 'gate green' not in result.reason


@pytest.mark.parametrize('attribution',[{'verdict':'unmeasured'},{'verdict':'owned','owner_slug':None}])
def test_actual_bash_unknown_attribution_does_not_revert(tmp_path,attribution):
    import importlib.util,json,subprocess
    from pathlib import Path
    path=Path(__file__).with_name('test_ship_audit.py')
    spec=importlib.util.spec_from_file_location('audit_fixture',path)
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    plans,plan=fixture._make_plans_dir(tmp_path);before=plan.read_bytes()
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    for step in range(3):
        subprocess.run(['git','-C',str(tmp_path),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
            'commit','--allow-empty','-qm',f'fixture [plan:test-slug#step-{step}]'],check=True)
    history=plans/'results.jsonl'
    history.write_text(json.dumps(dict(slug='test-slug',step=2,outcome='fail',attribution=attribution))+'\n')
    result=fixture._source_runner_and_call(f"test_ship_integrity '{plans}' '{history}'",project_path=str(tmp_path))
    assert result.returncode==0,result.stdout+result.stderr
    assert 'unmeasured' in (result.stdout+result.stderr).lower()
    assert plan.read_bytes()==before


def test_actual_powershell_integrity_keeps_unknown_attribution(tmp_path):
    import importlib.util,json,subprocess,sys,os,shutil,re
    from pathlib import Path
    pwsh=os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:pytest.skip('PowerShell runtime unavailable')
    spec=importlib.util.spec_from_file_location('audit_fixture',Path(__file__).with_name('test_ship_audit.py'))
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    project=tmp_path/'project with spaces';project.mkdir()
    plans,plan=fixture._make_plans_dir(project);before=plan.read_bytes()
    subprocess.run(['git','init','-q',str(project)],check=True)
    for step in range(3):
        subprocess.run(['git','-C',str(project),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
            'commit','--allow-empty','-qm',f'fixture [plan:test-slug#step-{step}]'],check=True)
    rows=tmp_path/'check records.jsonl'
    rows.write_text(json.dumps(dict(slug='test-slug',step=2,outcome='fail',attribution=dict(verdict='unmeasured')))+'\n')
    skills=Path(__file__).resolve().parents[2]
    source=(skills/'ilk-loop/scripts/run_ilk_loop_claude.ps1').read_text()
    definition=re.search(r'^function Test-ShipIntegrity \{.*?^\}',source,re.M|re.S).group()
    script=tmp_path/'probe.ps1'
    script.write_text("$ErrorActionPreference='Stop'\n$SkillRoot=$args[0]\nSet-Alias python $args[1]\n$FixturePlans=$args[2]\nfunction Get-PlansDir { param($Project) return $FixturePlans }\n"+
        definition+"\n$rows=@(Get-Content -LiteralPath $args[4] | ForEach-Object {$_ | ConvertFrom-Json})\n$v=@(Test-ShipIntegrity -ProjectPath $args[3] -LocalChecksRun $rows)\nif ($v.Count -ne 0) {throw ($v | ConvertTo-Json -Depth 40)}\nWrite-Output 'unknown integrity: UNMEASURED, no rollback'\n")
    result=subprocess.run([pwsh,'-NoProfile','-File',str(script),str(skills),sys.executable,str(plans),str(project),str(rows)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'unknown integrity: UNMEASURED, no rollback' in result.stdout
    assert plan.read_bytes()==before
