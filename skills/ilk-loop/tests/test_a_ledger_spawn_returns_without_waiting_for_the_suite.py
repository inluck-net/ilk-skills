"""A ledger spawn returns at once, even to a caller reading its stdout.

Measured 2026-10-06 18:13-18:20 (run 20261006-174019): the runner's
`result=$(python3 suite_ledger.py spawn ...)` (run_ilk_loop_claude.sh
ledger_spawn_for_head) blocked for the whole background suite, because the
double-forked measurement inherited the caller's stdout pipe (lsof: the
measure's fd 1/2 were the write end of the runner's fd 4). The loop sat in
phase `between` until the suite ended or was killed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def test_spawn_returns_while_the_measurement_still_runs(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "c"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
                       cwd=project, check=True)
    marker = tmp_path / "measure-ran"
    wrapper = tmp_path / "wrap.py"
    wrapper.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(SCRIPTS)!r})
        import suite_ledger as s
        s.lookup = lambda *a, **k: None
        s._is_ledger_disabled = lambda *a, **k: False
        s._background_measure_command = lambda *a, **k: [
            "sh", "-c", "touch {marker}; echo noise; echo noise >&2; sleep 30"]
        from pathlib import Path
        print(s.spawn(Path({str(project)!r}), "HEAD"))
    """))
    env = {**os.environ, "HOME": str(tmp_path / "home"),
           "ILK_DATA_HOME": str(tmp_path / "data")}
    env.pop("ILK_WORKER_SESSION", None)
    t0 = time.monotonic()
    # capture_output waits for EOF on both pipes, exactly like bash $(...)
    r = subprocess.run([sys.executable, str(wrapper)], capture_output=True, text=True,
                       env=env, timeout=25)
    elapsed = time.monotonic() - t0
    assert "spawned" in r.stdout, (r.stdout, r.stderr)
    assert elapsed < 10, f"spawn held the caller's pipe for {elapsed:.1f}s"
    assert "noise" not in r.stdout + r.stderr
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert marker.exists(), "the measurement itself must still run"
    subprocess.run(["pkill", "-f", f"touch {marker}"], check=False)
