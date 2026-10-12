"""Unknown local process ownership must never read as an idle fleet."""
import subprocess
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import release_train


@pytest.mark.parametrize('mode',['timeout','missing','error'])
def test_failed_local_probe_is_unknown(monkeypatch,mode):
    def probe(*a,**k):
        if mode=='timeout':raise subprocess.TimeoutExpired(a[0],10)
        if mode=='missing':raise FileNotFoundError('pgrep')
        return subprocess.CompletedProcess(a[0],2,'','probe error')
    monkeypatch.setattr(release_train.subprocess,'run',probe)
    assert release_train._live_loops_local() is None


def test_no_matching_processes_is_a_successful_empty_probe(monkeypatch):
    monkeypatch.setattr(release_train.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a[0],1,'',''))
    assert release_train._live_loops_local()==[]
