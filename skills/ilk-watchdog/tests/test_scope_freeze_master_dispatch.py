"""Execute the actual non-tmux launcher call with a pinned master."""
import json
import os
from pathlib import Path
import re
import subprocess


def test_non_tmux_dispatch_preserves_exact_master_argv(tmp_path):
    source=(Path(__file__).resolve().parents[1]/'scripts/scheduler.sh').read_text()
    start=source.rindex('        local master_flag=""')
    end=source.index('        local launch_cmd=',start)
    setup=source[start:end]
    command=re.search(r'elif (bash "\$LAUNCH_SCRIPT"[^\n]+); then',source)[1]
    stub=tmp_path/'launch.sh';stub.write_text('python3 - "$@" <<\'END\'\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\nEND\n')
    script='main() {\n'+setup+'\n'+command+'\n}\nmain\n'
    master="MASTER-selected with space.md"
    env=dict(os.environ,LAUNCH_SCRIPT=str(stub),drepo=str(tmp_path),slot_home=str(tmp_path/'worker'),dmaster=master,local_checks_flag=' --run-local-checks')
    cp=subprocess.run(['bash','-c',script],env=env,capture_output=True,text=True,timeout=10)
    assert cp.returncode==0,cp.stderr
    argv=json.loads(cp.stdout)
    assert '--master' in argv
    assert argv[argv.index('--master')+1]==master
