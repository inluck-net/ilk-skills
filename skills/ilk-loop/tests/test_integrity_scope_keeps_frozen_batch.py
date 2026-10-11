"""Execute the actual Bash reader's Python scope against competing masters."""
import json,os,re,subprocess,sys
from pathlib import Path
import pytest


def scope(tmp_path,context='valid',reader='integrity'):
    scripts=Path(__file__).parents[1]/'scripts';plans=tmp_path/'plans';plans.mkdir()
    for name,priority in [('frozen',0),('other',100)]:
        (plans/f'MASTER-{name}.md').write_text(f'---\nmaster_plan: {name}\nstatus: active\npriority: {priority}\n---\n[plan](./2026-10-12-{name}.md)\n')
        (plans/f'2026-10-12-{name}.md').write_text(f'---\nplan: {name}\nstatus: in-progress\ncurrent_step: 0\n---\n### Step 0: work\n')
    target=plans/'2026-10-12-frozen.md'
    data=dict(schema_version=1,plans_dir=str(plans),dispatch=dict(master_path=str(plans/'MASTER-frozen.md'),subplan_path=str(target),step=0),
        plans={target.name:dict(path=str(target),slug='frozen')},run_id='fixture',iteration=1,
        **{key:str(tmp_path/key) for key in ('clone','exec_tree','runtime_dir','data_root','code_root')})
    path=tmp_path/'context.json';path.write_text(json.dumps(data) if context=='valid' else '{')
    source=(scripts/'run_ilk_loop_claude.sh').read_text()
    if reader=='integrity':
        program=re.search(r'_active_subplans=\$\(python3 -c "(.*?)" "\$plans_dir"',source,re.S).group(1)
        arguments=[str(plans),str(scripts),'']
    else:
        section=source[source.index('ledger_record_point() {'):]
        program=re.search(r'master_basename=\$\(python3 -c "(.*?)" "\$\{_SKILL_ROOT\}',section,re.S).group(1)
        arguments=[str(scripts),str(plans)]
    env=dict(os.environ);env.pop('ILK_ITER_CONTEXT',None)
    if context!='absent':env['ILK_ITER_CONTEXT']=str(path)
    # 5s is a fixture margin for a measured sub-second read, not a default;
    # a legitimate slower host falsifies that margin.
    return subprocess.run([sys.executable,'-c',program,*arguments],env=env,capture_output=True,text=True,timeout=5)


def test_actual_reader_keeps_frozen_registry(tmp_path):
    result=scope(tmp_path)
    assert result.returncode==0,result.stderr
    assert result.stdout.splitlines()==['2026-10-12-frozen.md'],result.stdout


def test_invalid_frozen_reader_refuses_queue_fallback(tmp_path):
    result=scope(tmp_path,'invalid')
    assert result.returncode!=0,'invalid frozen context rediscovered another master'
    assert not result.stdout.strip()


def test_legacy_reader_still_selects_queue(tmp_path):
    result=scope(tmp_path,'absent')
    assert result.returncode==0,result.stderr
    assert result.stdout.splitlines()==['2026-10-12-other.md']


def test_actual_bash_caller_does_not_call_scope_failure_a_violation(tmp_path):
    scripts=Path(__file__).parents[1]/'scripts'
    source=(scripts/'run_ilk_loop_claude.sh').read_text()
    start=source.index('    local _ship_integrity_passed=0')
    end=source.index('    # Print ship-integrity diagnostics',start)
    branch=source[start:end]
    program="""set -u
python3() { return 0; }
get_plans_dir() { printf '%s' fixture; }
test_ship_integrity() { echo 'scope unavailable' >&2; return 2; }
probe() {
  local iter_stop_reason='' stop_reason='' _si_stderr=''
  local _ILK_SCRIPT_DIR=fixture local_checks_results='' heads_before_file='' heads_after_file=''
"""+branch+"""
  [[ $_ship_integrity_passed == 0 ]]
  [[ $stop_reason == local_checks_inconclusive && $iter_stop_reason == local_checks_inconclusive ]]
}
probe
"""
    result=subprocess.run(['/bin/bash','-c',program],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stdout+result.stderr


def test_ledger_point_keeps_frozen_master(tmp_path):
    result=scope(tmp_path,reader='ledger')
    assert result.returncode==0,result.stderr
    assert result.stdout.splitlines()==['MASTER-frozen.md'],result.stdout


def test_ledger_point_refuses_invalid_context(tmp_path):
    result=scope(tmp_path,'invalid',reader='ledger')
    assert result.returncode!=0 and not result.stdout.strip()


@pytest.mark.parametrize('registry',[[],{'test.md':[]},{'test.md':{'path':'/outside/test.md'}}])
def test_malformed_registry_is_observation_failure(tmp_path,registry):
    from iteration_context import integrity_scope,ContextError
    with pytest.raises(ContextError):
        integrity_scope(dict(plans_dir=str(tmp_path),plans=registry),tmp_path)
