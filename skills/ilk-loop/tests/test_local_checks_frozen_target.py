"""The gate entry reads its frozen target/root and refuses changed identities."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from iteration_context import plan_snapshot,write_context
from test_shared_at_base_attribution import world


@pytest.mark.parametrize('change',['none','wrong-step','duplicate','changed-contract','foreign-project'])
def test_actual_gate_cli_uses_frozen_registry_or_refuses(tmp_path,change):
    context,rows,row=world(tmp_path)
    plan=Path(context['dispatch']['subplan_path']);root=Path(context['exec_tree'])
    # Frozen clone is green while frozen execution tree is red.
    plan.write_text(plan.read_text().replace('assert f(4)==6','assert f(4)==5'))
    context['plans'][plan.name]=plan_snapshot(plan)
    step='0';project=context['clone']
    if change=='wrong-step':step='1'
    elif change=='duplicate':context['plans']['duplicate.md']=dict(context['plans'][plan.name])
    elif change=='changed-contract':plan.write_text(plan.read_text().replace('assert f(4)==5','assert True'))
    elif change=='foreign-project':project=str(tmp_path/'foreign');Path(project).mkdir()
    ctx=tmp_path/'ctx';write_context(ctx,context)
    capture=tmp_path/'capture'
    env=dict(os.environ,ILK_ITER_CONTEXT=str(ctx),ILK_GATE_OUTPUT_DIR=str(capture))
    script=Path(__file__).resolve().parents[1]/'scripts/run_local_checks.py'
    result=subprocess.run([sys.executable,str(script),'--project',project,'--slug','counter','--step',step],env=env,capture_output=True,text=True,timeout=10)
    payload=json.loads(result.stdout)
    if change=='none':
        assert result.returncode==1 and payload['outcome']=='fail',payload
        assert payload['run_cwd']==str(root) and payload['head_sha']==context['base_commit']
        assert payload['results'][0]['stdout_path'] and capture.exists()
    else:
        assert result.returncode==2 and payload['outcome']=='inconclusive',payload
        assert not capture.exists() # no authored gate launched
