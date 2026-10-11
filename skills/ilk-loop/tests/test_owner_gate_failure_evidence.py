import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS=Path(__file__).parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from run_local_checks import run_one
from emit_jsonl_record import build_record


def test_complete_outputs_are_kept_per_attempt(tmp_path,monkeypatch):
    monkeypatch.setenv('ILK_GATE_OUTPUT_DIR',str(tmp_path/'output'))
    # First failing ID falls outside the display tail. The artifact keeps it.
    command="printf 'FAILED tests/early.py::test_red\\n'; python3 -c \"print('x'*5000)\"; printf diagnostic >&2; exit 1"
    a=run_one({'command':command,'timeout':10},'step',tmp_path)
    b=run_one({'command':command,'timeout':10},'step',tmp_path)
    assert not a.passed and not b.passed
    assert 'tests/early.py' not in a.stdout_tail
    assert 'FAILED tests/early.py::test_red' in Path(a.stdout_path).read_text()
    assert Path(a.stderr_path).read_text()=='diagnostic'
    assert a.attempt_id!=b.attempt_id
    assert a.ended_monotonic>=a.started_monotonic
    row=build_record('work',0,'fail',1,vars(a),{'results':[vars(a)]})
    assert row['stdout_path']==a.stdout_path
    assert row['check_observations'][0]['attempt_id']==a.attempt_id
    assert row['outcome']=='fail'


def test_existing_retry_reader_and_complete_output_extractor(tmp_path):
    output=tmp_path/'full.log';output.write_text('FAILED tests/early.py::test_red\n')
    results=tmp_path/'checks.jsonl';results.write_text(json.dumps({'outcome':'fail','check_observations':[{'stdout_path':str(output)}]})+'\n')
    env=dict(os.environ,HOME=str(tmp_path),ILK_DATA_HOME=str(tmp_path/'data'),ILK_DATA_DIR=str(tmp_path/'data'),ILK_DOTSOURCE_ONLY='1',RUN_LOG_DIR=str(tmp_path),RESULTS=str(results))
    code='''source "$1"
RUN_LOG_DIR="$HOME"
_write_gate_red_ids work 0 '["tests/red.py::test_red"]'
_read_gate_red_ids work 0
_extract_failing_ids "$RESULTS"
'''
    cp=subprocess.run(['bash','-c',code,'fixture',str(SCRIPTS/'run_ilk_loop_claude.sh')],env=env,capture_output=True,text=True,timeout=20)
    assert cp.returncode==0,cp.stderr
    rows=[json.loads(line) for line in cp.stdout.splitlines() if line.startswith('[')]
    assert rows[0]==['tests/red.py::test_red']
    assert 'tests/early.py::test_red' in rows[1]
