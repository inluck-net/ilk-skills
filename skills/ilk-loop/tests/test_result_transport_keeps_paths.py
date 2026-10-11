"""The actual Bash result caller retains its single captured JSONL path."""
import json
import os
from pathlib import Path
import subprocess
import pytest
import run_result


def test_result_caller_reads_checks_path_with_spaces(tmp_path):
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    source=(scripts/'run_ilk_loop_claude.sh').read_text()
    function=source[source.index('_write_unattended_result() {'):
                    source.index('\n_finish_run_registry() {')]
    checks=tmp_path/'captured checks.jsonl'
    checks.write_text(json.dumps(dict(slug='selected-gate',step=0,cmd='focused-check',
        outcome='fail',exit_code=1,failed_count=1))+'\n')
    result=tmp_path/'result.json'
    env=dict(os.environ,_UNATTENDED_PROFILE='1',_UNATTENDED_RESULT_FILE=str(result),
        _RESULT_FILE_WRITTEN='0',_SKILL_ROOT=str(scripts.parent.parent),
        RUN_ID='fixture',_UNATTENDED_MASTER_SLUG='MASTER-fixture',PROJECT_PATH=str(tmp_path),
        _RUN_CHECKS_JSONL=str(checks),_INTEGRITY_VIOLATIONS_FILE='',JSONL_LOG='')
    command=function+'\n_write_unattended_result local_checks_failed fixture 1\n'
    called=subprocess.run(['bash','-c',command],env=env,capture_output=True,text=True,timeout=10)
    assert called.returncode==0,called.stderr
    rows=json.loads(result.read_text())['checks']
    assert len(rows)==1 and rows[0]['slug']=='selected-gate' and rows[0]['outcome']=='fail'


@pytest.mark.parametrize('capture', [None, '{broken', '[]'])
def test_unavailable_check_capture_never_becomes_an_empty_clean_list(tmp_path,capture):
    checks=tmp_path/'checks'
    if capture is not None:checks.write_text(capture+'\n')
    with pytest.raises(ValueError):run_result._read_checks_jsonl([str(checks)])
