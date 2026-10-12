"""Default runner refuses ambiguous identities and failed dispatch briefs."""
import os
from pathlib import Path
import subprocess
import pytest
from test_owner_worker_dispatch_brief import function,dispatch,ROOT,RUNNER


def test_default_slug_lookup_refuses_duplicates(tmp_path):
    plans=tmp_path/'plans';plans.mkdir()
    for name in ('one-target.md','two-target.md'):
        (plans/name).write_text('---\nplan: target\nstatus: in-progress\n---\n')
    script=function(RUNNER.read_text(),'find_subplan_file_by_slug')+'\nfind_subplan_file_by_slug "$PLANS" target\n'
    env=dict(os.environ,PLANS=str(plans),_SKILL_ROOT=str(ROOT/'skills'))
    for key in ('ILK_ITER_CONTEXT','ILK_DISPATCH_SLUG','ILK_DISPATCH_SUBPLAN'):env.pop(key,None)
    cp=subprocess.run(['bash','-c',script],env=env,capture_output=True,text=True,timeout=10)
    assert cp.returncode!=0 and not cp.stdout.strip()


def test_failed_brief_refuses_actual_prompt_dispatch(tmp_path):
    source=RUNNER.read_text().replace('build_worker_dispatch_brief() {','build_worker_dispatch_brief() {\n echo fixture-brief-failure >&2; return 37\n',1)
    with pytest.raises(AssertionError,match='dispatch brief refused'):
        dispatch(tmp_path,source)


def source():
    return Path(os.environ.get('ILK_SCOPE_RUNNER_SOURCE',str(RUNNER))).read_text()


def test_reentry_head_lookup_refuses_duplicate_files_without_notes(tmp_path):
    plans=tmp_path/'project/docs/plans';plans.mkdir(parents=True)
    for name in ('one-target.md','two-target.md'):
        (plans/name).write_text('---\nplan: target\nstatus: in-progress\ncurrent_step: 0\n---\n### Step 0 — repair\n')
    before={p:p.read_bytes() for p in plans.glob('*.md')}
    script='\n'.join(function(source(),name) for name in ('find_subplan_file_by_slug','_stamp_reentry_note'))+'\n_stamp_reentry_note target 0\n'
    env=dict(os.environ,PROJECT_PATH=str(plans.parents[1]),_SKILL_ROOT=str(ROOT/'skills'),_ILK_SCRIPT_DIR=str(ROOT/'skills/ilk-loop/scripts'))
    for key in ('ILK_ITER_CONTEXT','ILK_DISPATCH_SLUG','ILK_DISPATCH_SUBPLAN'):env.pop(key,None)
    cp=subprocess.run(['bash','-c',script],env=env,capture_output=True,text=True,timeout=10)
    assert cp.returncode!=0 and {p:p.read_bytes() for p in before}==before,cp.stderr


def test_integrity_head_lookup_refuses_duplicate_owner_files(tmp_path):
    plans=tmp_path/'plans';plans.mkdir()
    for name in ('one-owner.md','two-owner.md'):
        (plans/name).write_text('---\nplan: owner\nstatus: in-progress\ncurrent_step: 9\n---\n')
    text=source();start=text.index('        local _from_step_file="$f"');end=text.index('        local _from_step=""',start)
    script=function(text,'find_subplan_file_by_slug')+'\nmain() {\nlocal plans_dir="$PLANS" _revert_target_slug=owner slug=gate f=gate.md\n'+text[start:end]+'\n}\nmain\n'
    env=dict(os.environ,PLANS=str(plans),_SKILL_ROOT=str(ROOT/'skills'))
    for key in ('ILK_ITER_CONTEXT','ILK_DISPATCH_SLUG','ILK_DISPATCH_SUBPLAN'):env.pop(key,None)
    cp=subprocess.run(['bash','-c',script],env=env,capture_output=True,text=True,timeout=10)
    assert cp.returncode!=0,cp.stderr


def test_powershell_failed_brief_also_refuses_dispatch(tmp_path):
    import re
    import shutil
    pwsh=os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:pytest.skip('explicit portable PowerShell runtime unavailable')
    text=RUNNER.with_suffix('.ps1').read_text()
    definition=re.search(r'^function Get-WorkerDispatchContext \{.*?^\}',text,re.M|re.S)[0]
    probe=tmp_path/'probe.ps1'
    probe.write_text('''$ErrorActionPreference='Stop'
$SkillRoot='fixture'
function Get-PlansDir { return 'fixture-plans' }
function python {
  if ($args -contains '--brief') { $global:LASTEXITCODE=37; return 'failed fixture' }
  $global:LASTEXITCODE=0; return '{"subplan_path":"fixture-plan"}'
}
'''+definition+"\nGet-WorkerDispatchContext -Project fixture -Target ([PSCustomObject]@{slug='target';step=0;master=$null;fname=$null})\n")
    cp=subprocess.run([pwsh,'-NoProfile','-File',str(probe)],capture_output=True,text=True,timeout=10)
    assert cp.returncode!=0 and 'dispatch brief refused' in cp.stderr
