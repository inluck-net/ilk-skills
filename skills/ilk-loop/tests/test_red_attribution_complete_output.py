"""Attribution reads complete failed-node evidence and refuses missing captures."""
import json
import os
import subprocess
from pathlib import Path
import pytest


def test_bash_attribution_prefers_the_retained_full_capture(tmp_path):
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    source=(scripts/'run_ilk_loop_claude.sh').read_text()
    start=source.index('                local _stdout_file=""')
    end=source.index('                local _effective_repo',start)
    full=tmp_path/'complete.stdout';full.write_text('FAILED tests/test_early.py::test_defect\n'+'later log noise\n'*500)
    program='_b_stdout_tail="later log noise"\n_b_stdout_path="$FULL"\nprobe() {\n'+source[start:end]+'\nprintf "%s\\n" "$_stdout_file"\n}\nprobe\n'
    result=subprocess.run(['bash','-c',program],env=dict(os.environ,FULL=str(full)),capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()==str(full)
    assert full.read_text().startswith('FAILED tests/test_early.py::test_defect')


def test_red_owner_missing_capture_never_falls_back_to_running_a_broad_command(tmp_path, monkeypatch, capsys):
    import red_owner,sys
    monkeypatch.setattr(red_owner,'attribute_red',lambda *a,**k:pytest.fail('missing output triggered a new test run'))
    monkeypatch.setattr(sys,'argv',['red_owner','--attribute','--repo',str(tmp_path),'--iteration-base','b'*40,'--head','c'*40,'--cmd','pytest','--stdout-file',str(tmp_path/'missing')])
    assert red_owner.main(sys.argv[1:])==0
    result=json.loads(capsys.readouterr().out)
    assert result['verdict']=='unmeasured' and result['owner_sha'] is None
