"""Transferred deadlines must retain the same origin in a fresh process."""
import os
from pathlib import Path
import subprocess
import sys
from iteration_clock import monotonic


def test_child_clock_uses_parent_deadline_origin():
    scripts=Path(__file__).parents[1]/'scripts'
    before=monotonic()
    cp=subprocess.run([sys.executable,'-c','from iteration_clock import monotonic;print(monotonic())'],
                      env=dict(os.environ,PYTHONPATH=str(scripts)),capture_output=True,text=True,check=True)
    after=monotonic()
    assert before<=float(cp.stdout)<=after
