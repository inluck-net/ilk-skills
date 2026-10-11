"""Rolling diagnostic policy never reuses a green or permits a strike."""
import json
import pytest
from check_verdict_history import record_history,read_history


def context(tmp_path, iteration):return dict(runtime_dir=str(tmp_path),run_id='fixture',iteration=iteration)
def record(outcomes):return dict(slug='a',step=0,command='check',outcome='pass',attempts=outcomes)


def test_second_flaky_is_blocking_but_never_red(tmp_path):
    one=record(['fail','pass']);assert not record_history(context(tmp_path,1),[one])['blocked']
    two=record(['fail','pass']);result=record_history(context(tmp_path,2),[two])
    assert result['blocked'] and two['flaky_blocking']
    assert two['verdict']=='flaky' and two['outcome']=='inconclusive'
    assert two['attempts']==['fail','pass']
    assert record_history(context(tmp_path,2),[two])['blocked']


def test_green_runs_expire_the_previous_flaky(tmp_path):
    record_history(context(tmp_path,1),[record(['fail','pass'])])
    for iteration in range(2,11):record_history(context(tmp_path,iteration),[record(['pass'])])
    result=record_history(context(tmp_path,11),[record(['fail','pass'])])
    assert not result['blocked']
    history=read_history(tmp_path/'launcher/local-check-verdict-history.json')
    assert len(next(iter(history.values())))==10


def test_repeat_is_idempotent_and_changed_attempts_refuse(tmp_path):
    record_history(context(tmp_path,1),[record(['fail','pass'])])
    assert not record_history(context(tmp_path,1),[record(['fail','pass'])])['blocked']
    with pytest.raises(ValueError,match='identity changed'):
        record_history(context(tmp_path,1),[record(['pass'])])


def test_corrupt_history_cannot_reset_the_counter(tmp_path):
    path=tmp_path/'launcher/local-check-verdict-history.json';path.parent.mkdir();path.write_text('{broken')
    with pytest.raises(ValueError):record_history(context(tmp_path,1),[record(['pass'])])
    assert path.read_text()=='{broken'


def frozen_context(tmp_path, iteration):
    data=context(tmp_path,iteration)
    data['schema_version']=1
    for name in ('clone','exec_tree','plans_dir','data_root','code_root'):data[name]=str(tmp_path/name)
    path=tmp_path/'ctx.json';path.write_text(json.dumps(data));return path


def test_bash_rolling_caller_blocks_second_flaky(tmp_path):
    import os,subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[3];source=(root/'skills/ilk-loop/scripts/run_ilk_loop_claude.sh').read_text()
    start=source.index('    # Include green executions');end=source.index('    # Evaluate gate_outcome NOW',start)
    record_history(context(tmp_path,1),[record(['fail','pass'])])
    frozen=frozen_context(tmp_path,2);rows=tmp_path/'rows.jsonl';rows.write_text(json.dumps(record(['fail','pass']))+'\n')
    program='_SKILL_ROOT="$SKILLS"\nlocal_checks_results="$ROWS"\niter_stop_reason=""\ncaller() {\n'+source[start:end]+'\n}\ncaller\n[[ "$iter_stop_reason" == local_checks_inconclusive ]]\n'
    env=dict(os.environ,ILK_ITER_CONTEXT=str(frozen),SKILLS=str(root/'skills'),ROWS=str(rows))
    result=subprocess.run(['bash','-c',program],env=env,text=True,capture_output=True,timeout=5)
    assert result.returncode==0,result.stdout+result.stderr
    observed=json.loads(rows.read_text());assert observed['flaky_blocking'] and observed['verdict']=='flaky'


def test_portable_powershell_history_counts_green_and_flaky(tmp_path):
    import os,re,subprocess,shutil,sys
    from pathlib import Path
    pwsh=os.environ.get('ILK_TEST_PWSH') or shutil.which('pwsh')
    if not pwsh:pytest.skip('PowerShell runtime unavailable')
    skills=Path(__file__).resolve().parents[2]
    source=(skills/'ilk-loop/scripts/run_ilk_loop_claude.ps1').read_text()
    definition=re.search(r'^function Update-LocalCheckHistory \{.*?^\}',source,re.M|re.S).group()
    record_history(context(tmp_path,1),[record(['fail','pass'])])
    frozen=frozen_context(tmp_path,2);probe=tmp_path/'probe.ps1'
    probe.write_text("$ErrorActionPreference='Stop'\n$SkillRoot=$args[0]\nSet-Alias python $args[1]\n"+definition+"\n$r=[PSCustomObject]@{slug='a';step=0;command='check';outcome='pass';attempts=@('fail','pass')}\n$result=Update-LocalCheckHistory -Records @($r)\nif (-not $result.Decision.blocked -or -not $result.Records[0].flaky_blocking) {throw 'second flaky escaped'}\n$result=Update-LocalCheckHistory -Records $result.Records\nif (-not $result.Decision.blocked) {throw 'repeated call escaped'}\nWrite-Output 'rolling history: PASS'\n")
    env=dict(os.environ,ILK_ITER_CONTEXT=str(frozen))
    result=subprocess.run([pwsh,'-NoProfile','-File',str(probe),str(skills),sys.executable],env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'rolling history: PASS' in result.stdout


def test_legacy_history_lock_contention_never_waits_forever(tmp_path):
    import os
    if os.name=='nt':pytest.skip('POSIX contention probe; Windows uses existing msvcrt adapter')
    import fcntl
    from plan_state import TransitionError
    directory=tmp_path/'launcher';directory.mkdir();lock=directory/'local-check-verdict-history.lock'
    with lock.open('a+b') as held:
        fcntl.flock(held.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(TransitionError,match='lock busy'):
            record_history(context(tmp_path,1),[record(['pass'])])
    assert not (directory/'local-check-verdict-history.json').exists()
