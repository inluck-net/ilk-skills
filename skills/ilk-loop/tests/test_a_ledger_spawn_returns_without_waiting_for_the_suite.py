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


# ── Class guard: no detached spawn inherits its caller's stdio ──────────────
#
# The 2026-10-06 hang was one instance of a class: a child started in a new
# session outlives its caller and, with inherited fds, keeps the caller's
# stdout pipe open (a `$(...)` reader never sees EOF). A sibling seen the same
# day: autoplan's real-mode tick spawned with stdout=PIPE that nothing read.
# These rules are checked over every tracked non-test .py file.

import ast  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_DRAIN = ("communicate", "wait", "_stream_output", "_read_init_event", "_kill_group",
          "read", "readline")


def _tracked_sources() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=_REPO,
                         capture_output=True, text=True, check=True).stdout.split()
    return [_REPO / f for f in out if "/tests/" not in f and not f.startswith("tests/")]


def _violations() -> list[str]:
    bad: list[str] = []
    for path in _tracked_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = path.relative_to(_REPO)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            src = ast.get_source_segment(text, fn) or ""
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                kws = {k.arg: k.value for k in node.keywords if k.arg}
                sns = kws.get("start_new_session")
                if isinstance(sns, ast.Constant) and sns.value is True:
                    for stream in ("stdout", "stderr"):
                        if stream not in kws:
                            bad.append(f"{rel}:{node.lineno} {fn.name}: detached Popen inherits {stream}")
                        elif (ast.unparse(kws[stream]).endswith("PIPE")
                              and not any(f".{d}(" in src or f"{d}(" in src for d in _DRAIN)):
                            bad.append(f"{rel}:{node.lineno} {fn.name}: detached Popen {stream}=PIPE never drained")
                if isinstance(node.func, ast.Attribute) and node.func.attr == "setsid":
                    if not ("dup2(" in src and ", 1)" in src and ", 2)" in src):
                        bad.append(f"{rel}:{node.lineno} {fn.name}: setsid child keeps fds 1/2")
    return bad


def test_no_detached_spawn_inherits_or_strands_its_callers_stdio() -> None:
    assert _violations() == []


def test_the_guard_sees_the_original_defect(tmp_path: Path, monkeypatch) -> None:
    # Positive control: the pre-fix _spawn_detached shape must be flagged.
    bad_src = tmp_path / "bad.py"
    bad_src.write_text("import os\ndef spawn():\n    os.setsid()\n    os.execvp('x', ['x'])\n")
    monkeypatch.setattr(sys.modules[__name__], "_REPO", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "_tracked_sources", lambda: [bad_src])
    assert any("setsid child keeps fds 1/2" in v for v in _violations())
