"""Execute the batched bootstrap shell command; retain exact instruction reads."""
import shlex
import subprocess
import sys
from pathlib import Path
from worker_iteration import instructions


def test_code_only_bootstrap_batches_exact_plan_and_known_root_instructions(tmp_path):
    code=tmp_path/'pinned code';wrapper=code/'skills/ilk-loop/scripts/worker_shell.py'
    wrapper.parent.mkdir(parents=True);wrapper.write_text('# fixture')
    plan=tmp_path/'chosen plan.md';plan.write_text('PLAN\n')
    agents=tmp_path/'AGENTS.md';agents.write_text('INSTRUCTIONS\n')
    ctx=tmp_path/'ctx.json';ctx.write_text('{}')
    context=dict(code_root=str(code),exec_tree=str(tmp_path),worker_shell_python=sys.executable,
                 run_id='fixture',iteration=1,
                 plans_dir=str(tmp_path),plans={plan.name:{'path':str(plan)}},
                 dispatch={'slug':'chosen','step':0,'subplan_path':str(plan)},
                 root_instruction_inventory=[{'path':str(agents),'sha256':'fixture'}])
    text=instructions(context,ctx)
    marker='First group these required independent reads in ONE Bash call:\n'
    command=text.split(marker,1)[1].splitlines()[0]
    inner=shlex.split(command)[5]
    result=subprocess.run(['bash','-c',inner],capture_output=True,text=True)
    assert result.returncode==0 and result.stdout=='PLAN\nINSTRUCTIONS\n'
    assert 'nested instructions relevant to files you edit' in text
    assert 'no queue or toolkit bootstrap discovery is needed' in text
