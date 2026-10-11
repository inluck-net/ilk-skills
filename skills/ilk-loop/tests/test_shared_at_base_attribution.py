"""Portable caller annotations use actual frozen at-base code and full output."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import pytest
from blocking_checks import attribute_records,strike_targets
from iteration_context import plan_snapshot,write_context
from test_owner_driver_step import fixture
from driver_step import git


def world(tmp_path):
    context,root,plan=fixture(tmp_path)
    renamed=plan.with_name('2026-10-12-counter.md');plan.rename(renamed);plan=renamed
    context['dispatch']['subplan_path']=str(plan)
    context['plans']={}
    base=context['base_commit']
    (root/'counter.py').write_text('def f(value): return value + 0\n')
    git(root,'add','.');git(root,'commit','-qm','[plan:actual-owner#step-0] break counter')
    head=git(root,'rev-parse','HEAD')
    context.pop('worker_sandbox_adapter')
    context.update(base_commit=head,repository_bases={str(root):head})
    context['dispatch']['batch_base_sha']=base
    context['plans'][plan.name]=plan_snapshot(plan)
    stdout=tmp_path/'stdout';stderr=tmp_path/'stderr';stdout.write_text('');stderr.write_text('AssertionError\n')
    command=sys.executable+' -I -c "from pathlib import Path;exec(Path(\'counter.py\').read_text());assert f(4)==5"'
    row=dict(slug='counter',step=0,outcome='fail',verdict='red',raw={'results':[dict(command=command,scope='step',outcome='fail',output_complete=True,stdout_path=str(stdout),stderr_path=str(stderr))]})
    path=tmp_path/'rows';path.write_text(json.dumps(row)+'\n')
    return context,path,row


def test_actual_inherited_failure_names_measured_owner(tmp_path):
    context,path,row=world(tmp_path)
    result=attribute_records(path,context=context,budget_s=10)
    updated=json.loads(path.read_text());attr=updated['attribution']
    assert result['measured']==1 and attr['verdict']=='inherited',attr
    assert attr['owner_slug']=='actual-owner' and attr['batch_base_green'] is True
    assert updated['outcome']=='fail' and strike_targets(path)==['actual-owner']


@pytest.mark.parametrize('change',['missing-capture','incomplete','wrong-step','no-context'])
def test_missing_evidence_never_grants_strike(tmp_path,monkeypatch,change):
    context,path,row=world(tmp_path);monkeypatch.delenv('ILK_ITER_CONTEXT',raising=False)
    if change=='missing-capture':row['raw']['results'][0]['stdout_path']=str(tmp_path/'missing')
    elif change=='incomplete':row['raw']['results'][0]['output_complete']=False
    elif change=='wrong-step':row['step']=1
    else:context=None
    path.write_text(json.dumps(row)+'\n');attribute_records(path,context=context,budget_s=10)
    updated=json.loads(path.read_text())
    assert updated['attribution']['verdict']=='unmeasured' and updated['outcome']=='fail'
    assert strike_targets(path)==[]


def test_portable_powershell_invokes_actual_measurement(tmp_path):
    pwsh=os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:pytest.skip('PowerShell runtime unavailable')
    context,path,row=world(tmp_path);ctx=tmp_path/'ctx';write_context(ctx,context)
    skills=Path(__file__).resolve().parents[2];source=(skills/'ilk-loop/scripts/run_ilk_loop_claude.ps1').read_text()
    definition=re.search(r'^function Set-LocalCheckAttribution \{.*?^\}',source,re.M|re.S).group()
    probe=tmp_path/'probe.ps1';probe.write_text("$ErrorActionPreference='Stop'\n$SkillRoot=$args[0]\nSet-Alias python $args[1]\n$env:ILK_ITER_CONTEXT=$args[2]\n"+definition+"\n$rows=@(Get-Content -LiteralPath $args[3] | ForEach-Object {$_ | ConvertFrom-Json})\n$result=@(Set-LocalCheckAttribution -Records $rows)\nif ($result.Count -ne 1 -or $result[0].attribution.verdict -ne 'inherited' -or $result[0].attribution.owner_slug -ne 'actual-owner' -or $result[0].outcome -ne 'fail') {throw ($result | ConvertTo-Json -Depth 40)}\nWrite-Output 'actual attribution: PASS'\n")
    result=subprocess.run([pwsh,'-NoProfile','-File',str(probe),str(skills),sys.executable,str(ctx),str(path)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'actual attribution: PASS' in result.stdout


def test_one_existing_budget_is_shared_across_records(tmp_path,monkeypatch):
    import red_owner
    context,path,row=world(tmp_path)
    path.write_text(''.join(json.dumps(row)+'\n' for _ in range(3)))
    clock=[0.0];calls=[]
    monkeypatch.setattr(red_owner,'_clock',lambda:clock[0])
    def measure(*args):
        calls.append(args[-1]);clock[0]+=0.1
        return dict(verdict='pre-existing',owner_sha=None,owner_slug=None)
    monkeypatch.setattr(red_owner,'attribute_red',measure)
    attribute_records(path,context=context,budget_s=0.15)
    updated=[json.loads(line) for line in path.read_text().splitlines()]
    assert len(calls)==2 and calls[1]<calls[0]
    assert updated[-1]['attribution']['verdict']=='unmeasured'
    assert all(record['outcome']=='fail' for record in updated)
