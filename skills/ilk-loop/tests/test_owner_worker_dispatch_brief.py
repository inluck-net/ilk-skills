"""Execute the production prompt composition with hermetic driver/stub worker.

Pins frontmatter identity, exact startup paths, retained notices and unchanged
worker gate commands. No real endpoint, release switch or running batch data.
"""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[3]
RUNNER = ROOT / 'skills/ilk-loop/scripts/run_ilk_loop_claude.sh'


def function(source, name):
    match = re.search(r'^'+name+r'\(\) \{.*?^\}', source, re.M | re.S)
    assert match
    return match[0]


def dispatch(tmp_path, source=None):
    source = RUNNER.read_text() if source is None else source
    plans = tmp_path / 'plans'
    plans.mkdir()
    master = plans / 'MASTER-owner.md'
    master.write_text('---\nbase_sha: frozen123\n---\n| 1 | 2026-10-11-chosen.md |\n')
    selected = plans / '2026-10-11-chosen.md'
    selected.write_text('''---
plan: chosen
current_step: 0
---
Part of [MASTER](./MASTER-owner.md).
## Steps
### Step 0 — implement
```yaml
local_checks:
  - command: "printf green"
    timeout: 30
```
''')
    (plans / 'wrong-chosen.md').write_text('---\nplan: unrelated\n---\n')
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'gate-red-1.txt').write_text('stdout: failing test evidence\n')
    start = source.index('    # -- Prior suite result carry-forward')
    end = source.index('    # ── Pre-iteration record', start)
    helpers = '\n'.join(function(source, name) for name in
                        ['find_subplan_file_by_slug','build_worker_gate_notice','build_worker_dispatch_brief'])
    script = helpers + '''
get_plans_dir() { printf '%s' "$PLANS"; }
_runtime_file() { printf '%s' "$RUN_LOG_DIR/nonexistent"; }
read_prior_suite_result_for_prompt() { printf 'prior suite evidence'; }
main() {
local i=2 PRE_ITER_TARGET='chosen 0' PROMPT='continue selected work'
local STEER_INTERJECTION_TEXT='operator instruction'
''' + source[start:end] + '''
# Local worker receives the same prompt argument as invoke_claude_iteration.
python3 - "$iter_prompt" <<'END'
import json,sys
print('CAPTURE='+json.dumps(sys.argv[1]))
END
}
main
'''
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'), PLANS=str(plans),
               RUN_LOG_DIR=str(run), PROJECT_PATH=str(tmp_path/'project'),
               _SKILL_ROOT=str(ROOT/'skills'), ILK_MASTER='MASTER-owner.md')
    cp = subprocess.run(['bash','-c',script], env=env, text=True, capture_output=True, timeout=20)
    assert cp.returncode == 0, cp.stderr
    prompt = json.loads(next(l.removeprefix('CAPTURE=') for l in cp.stdout.splitlines() if l.startswith('CAPTURE=')))
    return prompt, selected, master


def test_actual_prompt_keeps_all_context_and_notices(tmp_path):
    prompt, selected, master = dispatch(tmp_path)
    for text in ['prior suite evidence','operator instruction','continue selected work',
                 str(selected),str(master),'Selected step: 0','frozen123',
                 str(ROOT/'skills/ilk-loop/SKILL.md'),'printf green',
                 'Never weaken or delete an assertion','gate-red-1.txt']:
        assert text in prompt
    assert 'wrong-chosen.md' not in prompt


def test_prefix_loss_negative_control(tmp_path):
    source = RUNNER.read_text()
    broken = source.replace('${STEER_INTERJECTION_TEXT}\n\n${iter_prompt}"',
                            '${STEER_INTERJECTION_TEXT}\n\n${PROMPT}"')
    assert broken != source
    prompt, _, _ = dispatch(tmp_path, broken)
    assert 'prior suite evidence' not in prompt


def test_actual_summary_adds_cost_without_changing_red_authority(tmp_path):
    source = RUNNER.read_text()
    start = source.index('    # Write JSONL record via Python')
    end = source.index('    # Restore set -e:', start)
    log = tmp_path / 'iter-01.log'
    stream = {'type':'system','subtype':'init','model':'actual-model','session_id':'s'}
    Path(str(log)+'.jsonl').write_text(json.dumps(stream)+'\n')
    out = tmp_path / 'summary.jsonl'
    script = '''
main() {
local PROJECT_PATH=/tmp/project MODEL=configured-model MAX_BUDGET_USD=0 RUN_ID=fixture
local i=1 iter_dur_sec=7 ITER_EXIT_CODE=0 total_new=1 iter_stop_reason=local_checks_failed
local new_commits_json='{}' local_checks_json='[{"outcome":"failed"}]' _SHIP_GAP_JSON=''
local wip_preserved=0 iter_tool_calls=0 iter_test_invocations=0 _amend_flag=/nonexistent
''' + source[start:end] + '\n}\nmain\n'
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'), iter_log=str(log),JSONL_LOG=str(out),
               _SKILL_ROOT=str(ROOT/'skills'))
    cp = subprocess.run(['bash','-c',script],env=env,capture_output=True,text=True,timeout=20)
    assert cp.returncode == 0, cp.stderr
    record = json.loads(out.read_text())
    assert record['model'] == 'configured-model'
    assert record['worker_cost_observation']['models_actual_init'] == ['actual-model']
    assert record['worker_cost_observation']['worker_elapsed_seconds'] is None
    assert record['duration_sec'] == 7
    assert record['stop_reason'] == 'local_checks_failed'
    assert record['local_checks'] == [{'outcome':'failed'}]


def test_red_notice_contains_stdout_evidence(tmp_path):
    source = RUNNER.read_text()
    start = source.index('                  python3 -c "\nimport json, sys\nfor line in sys.stdin:')
    end = source.index(' || true', start)
    snippet = source[start:end]
    results = tmp_path/'checks.jsonl'
    results.write_text(json.dumps({'command':'failing gate','stdout_tail':'FAILED focused-case',
                                    'stderr_tail':'diagnostic'})+'\n')
    notice = tmp_path/'notice.txt'
    env = dict(os.environ, HOME=str(tmp_path), ILK_DATA_HOME=str(tmp_path/'data'),
               ILK_DATA_DIR=str(tmp_path/'data'), local_checks_results=str(results),
               _gate_red_file=str(notice))
    cp = subprocess.run(['bash','-c',snippet],env=env,capture_output=True,text=True,timeout=20)
    assert cp.returncode == 0, cp.stderr
    assert 'stdout: FAILED focused-case' in notice.read_text()
    assert 'stderr: diagnostic' in notice.read_text()


def test_detached_entry_delivers_brief_to_stub_cli(tmp_path):
    import shutil
    import sys
    skills = tmp_path/'skills'
    shutil.copytree(ROOT/'skills',skills)
    shutil.copytree(ROOT/'tools/loop-performance',tmp_path/'tools/loop-performance')
    project = tmp_path/'project';project.mkdir()
    (project/'README.md').write_text('fixture\n')
    for args in [('init','-q'),('add','README.md'),('commit','-qm','fixture')]:
        subprocess.run(['git','-c','user.name=fixture','-c','user.email=fixture@example.com',*args],cwd=project,check=True,capture_output=True)
    base = subprocess.check_output(['git','rev-parse','HEAD'],cwd=project,text=True).strip()
    scripts = ROOT/'skills/ilk-loop/scripts'
    sys.path.insert(0,str(scripts))
    from ilk_paths import project_key
    data = tmp_path/'data'
    plans = data/'projects'/project_key(project)/'plans';plans.mkdir(parents=True)
    master = plans/'MASTER-2026-10-11-owner-execution-plan.md'
    selected = plans/'2026-10-11-chosen.md'
    master.write_text(f'''---
status: active
base_branch: main
base_sha: {base}
---
# MASTER
| # | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|
| 1 | [{selected.name}](./{selected.name}) | fixture | 1 | pending |
''')
    selected.write_text(f'''---
plan: chosen
status: pending
current_step: 0
estimated_steps: 1
local_checks: []
---
Part of [MASTER](./{master.name}).
## Steps
### Step 0 — fixture
```yaml
local_checks:
  - command: "printf green"
    timeout: 30
```
''')
    (project/'.ilk-launch.json').write_text('{"ship":{"verification_subplan":"optional"}}')
    bin_dir = tmp_path/'bin';bin_dir.mkdir()
    stub = bin_dir/'claude'
    stub.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
if '-p' not in sys.argv:
 print('fixture-cli');sys.exit(0)
Path(os.environ['CAPTURE']).write_text(sys.argv[-1])
print(json.dumps({'type':'system','subtype':'init','model':'stub-model','session_id':'stub-session'}))
print(json.dumps({'type':'result','subtype':'success','duration_ms':1,'duration_api_ms':1,'result':'fixture complete'}))
''')
    stub.chmod(0o755)
    home = tmp_path/'home';home.mkdir()
    capture = tmp_path/'prompt.txt'
    env = {k:v for k,v in os.environ.items() if not k.startswith('ANTHROPIC_')}
    env.update(HOME=str(home),ILK_DATA_HOME=str(data),ILK_DATA_DIR=str(data),
               ILK_SKILL_HOME=str(skills),ILK_MASTER=master.name,
               CLAUDE_CONFIG_DIR=str(home/'.claude'),CAPTURE=str(capture),
               PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
    for name in ['BASH_ENV','ILK_WORKER_SESSION','ILK_RUN_LOCK_HELD']:
        env.pop(name,None)
    cp = subprocess.run(['bash','--noprofile',str(skills/'ilk-loop/scripts/run_ilk_loop_claude.sh'),
                         '--project-path',str(project),'--max-iterations','1',
                         '--iteration-timeout-min','1','--run-local-checks'],
                        cwd=tmp_path,env=env,capture_output=True,text=True,timeout=90)
    assert capture.exists(), cp.stdout[-2500:]+cp.stderr[-2500:]
    prompt = capture.read_text()
    assert str(selected) in prompt
    assert str(master) in prompt
    assert str(skills/'ilk-loop/SKILL.md') in prompt
    assert 'Selected step: 0' in prompt
    assert 'printf green' in prompt
    summaries = list(data.rglob('.ilk-loop.log'))
    assert summaries
    records = [json.loads(l) for l in summaries[0].read_text().splitlines() if l.strip()]
    measured = [r for r in records if 'worker_cost_observation' in r]
    assert measured, cp.stdout[-2500:]+cp.stderr[-2500:]
    assert measured[0]['worker_cost_observation']['models_actual_init'] == ['stub-model']
    assert measured[0]['dispatch']['subplan_path'] == str(selected)
    assert measured[0]['dispatch']['master_path'] == str(master)
    assert any(r.get('status') == 'started' and r.get('dispatch',{}).get('step') == 0 for r in records)
