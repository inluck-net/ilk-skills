"""Pins for D4: the watchdog never relaunches an operator stop or a held project.

On 2026-09-28 at 22:58:25 and 23:03:59 the watchdog relaunched ilk-skills with
``--force`` after operator stops, while both its masters were parked.  The
runner's TERM trap wrote ``state: interrupted`` -- the same state a crash
writes -- and the watchdog took its verdict from the postmortem
(``source=postmortem`` -> ``max-iter-bound``, a whitelist label).

AC-1: a SIGTERM to the runner writes an additive ``stopped_by: signal:TERM``
      on the interrupted sentinel (through the real main()).
AC-2: relaunch_guard: operator stop -> 11; held -> 12; a live run.lock holder
      or a fresh launch inside its window -> 10 (alive); a crash-interrupted
      sentinel -> 0.
AC-3: through the real watchdog loop, with collect.py stubbed to answer
      ``max-iter-bound`` exactly as in the incident: an operator stop and a
      held project are NOT relaunched; the crash control IS.
AC-4: scheduler.sh resolves the sentinel through ilk_paths (runtime/launcher/),
      not the dead runtime/last-exit.json.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SKILLS = _REPO / "skills"
_GUARD = _SKILLS / "ilk-watchdog" / "scripts" / "relaunch_guard.py"
_DRIVER = _SKILLS / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_LOOP_SCRIPTS = _SKILLS / "ilk-loop" / "scripts"
if str(_LOOP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LOOP_SCRIPTS))

import ilk_paths  # noqa: E402

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _keyed(data_home: Path, project: Path) -> tuple[Path, Path]:
    """Return (plans_dir, launcher_dir) for *project* under *data_home*."""
    prev = os.environ.get("ILK_DATA_HOME")
    os.environ["ILK_DATA_HOME"] = str(data_home)
    try:
        key = ilk_paths.project_key(project)
        return ilk_paths.external_plans_dir(key), ilk_paths.external_launcher_dir(key)
    finally:
        if prev is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = prev


def _world(root: Path, *, held: bool = False) -> dict:
    project = root / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    (project / "README.md").write_text("x\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "init")
    data_home = root / ".ilk-data"
    plans, launcher = _keyed(data_home, project)
    plans.mkdir(parents=True)
    launcher.mkdir(parents=True)
    (plans / "2026-09-29-aaa.md").write_text(
        "---\nplan: aaa\nstatus: in-progress\ncurrent_step: 0\n"
        "estimated_steps: 2\n---\n\n### Step 0 — x\n\n### Step 1 — y\n")
    hold = ('parked_at: 2026-09-28T22:50:00\nparked_reason: "operator"\n'
            'hold: human\n') if held else ""
    status = "blocked" if held else "active"
    (plans / "MASTER-2026-09-29a.md").write_text(
        f"---\nmaster_plan: 2026-09-29a\nstatus: {status}\n{hold}---\n\n"
        "| # | Slug |\n|---|---|\n| 1 | 2026-09-29-aaa.md |\n")
    return {"project": project, "data_home": data_home, "plans": plans,
            "launcher": launcher, "root": root}


def _sentinel(launcher: Path, *, stopped_by: str | None, ended: datetime) -> None:
    d = {"state": "interrupted", "pid": None, "run_id": "20260928-225800",
         "started_at": (ended - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S%z") or
         (ended - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S"),
         "ended_at": ended.strftime("%Y-%m-%dT%H:%M:%S"),
         "stopped_reason": "operator stop (SIGTERM)" if stopped_by
         else "runner exited without a terminal state"}
    if stopped_by:
        d["stopped_by"] = stopped_by
    (launcher / "last-exit.json").write_text(json.dumps(d))


def _guard(w: dict) -> tuple[int, dict]:
    r = subprocess.run(
        [sys.executable, str(_GUARD), "--project", str(w["project"]),
         "--launcher-dir", str(w["launcher"])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        env={**os.environ, "ILK_DATA_HOME": str(w["data_home"])})
    return r.returncode, json.loads(r.stdout)


# ── AC-2: the guard ────────────────────────────────────────────────────────

def test_guard_refuses_an_operator_stop(tmp_path: Path) -> None:
    w = _world(tmp_path)
    _sentinel(w["launcher"], stopped_by="signal:TERM", ended=datetime.now())
    rc, out = _guard(w)
    assert rc == 11 and out["verdict"] == "operator-stop", out


def test_guard_refuses_a_held_project(tmp_path: Path) -> None:
    w = _world(tmp_path, held=True)
    _sentinel(w["launcher"], stopped_by=None, ended=datetime.now())
    rc, out = _guard(w)
    assert rc == 12 and out["held_by"] == "MASTER-2026-09-29a.md", out


def test_guard_allows_a_crash(tmp_path: Path) -> None:
    w = _world(tmp_path)
    _sentinel(w["launcher"], stopped_by=None, ended=datetime.now())
    rc, out = _guard(w)
    assert rc == 0, out


def test_guard_sees_a_fresh_launch_as_alive(tmp_path: Path) -> None:
    """The scheduler dispatched after the old run ended; the new runner has
    not taken run.lock yet.  Relaunching --force over it starts a duplicate."""
    w = _world(tmp_path)
    ended = datetime.now() - timedelta(seconds=30)
    _sentinel(w["launcher"], stopped_by=None, ended=ended)
    (w["launcher"] / "last-launch.json").write_text(json.dumps({
        "pid": os.getpid(),
        "started_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}))
    rc, out = _guard(w)
    assert rc == 10 and out["verdict"] == "alive", out


def test_guard_sees_a_live_lock_holder_as_alive(tmp_path: Path) -> None:
    w = _world(tmp_path)
    _sentinel(w["launcher"], stopped_by="signal:TERM", ended=datetime.now())
    # A process whose command line reads as an ilk runner.
    proc = subprocess.Popen(["bash", "-c", "exec -a run_ilk_loop_claude.sh sleep 30"])
    try:
        time.sleep(0.3)
        (w["launcher"] / "run.lock").write_text(json.dumps({"pid": proc.pid}))
        rc, out = _guard(w)
        assert rc == 10, out
    finally:
        proc.kill()
        proc.wait()
    # Its death leaves stale metadata behind; that is not a live run.
    rc, out = _guard(w)
    assert rc == 11, out


# ── AC-3: through the real watchdog loop ───────────────────────────────────

@pytest.fixture(scope="module")
def skill_copy(tmp_path_factory) -> Path:
    """A copy of skills/ with launch.sh and collect.py stubbed."""
    dst = tmp_path_factory.mktemp("skills-copy") / "skills"
    shutil.copytree(_SKILLS, dst, ignore=shutil.ignore_patterns(
        "tests", "__pycache__", "*.pyc", ".pytest_cache"))
    (dst / "ilk-launcher" / "scripts" / "launch.sh").write_text(
        "#!/usr/bin/env bash\necho \"$@\" >> \"$WATCHDOG_TEST_LAUNCHES\"\nexit 0\n")
    (dst / "ilk-feedback" / "scripts" / "collect.py").write_text(
        "import os, sys\n"
        "p = os.path.join(os.environ['WATCHDOG_TEST_DIR'], 'postmortem.md')\n"
        "open(p, 'w').write('---\\nclassification: max-iter-bound\\n---\\n')\n"
        "print(p)\n")
    return dst


def _run_watchdog(skills: Path, w: dict) -> tuple[subprocess.CompletedProcess, str]:
    launches = w["root"] / "launches.txt"
    env = {**os.environ,
           "HOME": str(w["root"]),
           "ILK_DATA_HOME": str(w["data_home"]),
           "ILK_SKILL_HOME": str(skills),
           "WATCHDOG_TEST_LAUNCHES": str(launches),
           "WATCHDOG_TEST_DIR": str(w["root"]),
           "ILK_NOTIFY": "0"}
    env.pop("ILK_DATA_DIR", None)
    proc = subprocess.run(
        ["gtimeout", "25", "bash", str(skills / "ilk-watchdog" / "scripts" / "watchdog.sh"),
         "--project-path", str(w["project"]), "--poll-interval-sec", "60",
         "--max-restarts", "3"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env)
    return proc, launches.read_text() if launches.exists() else ""


def _activity(w: dict) -> str:
    logs = list(w["data_home"].rglob("activity.log"))
    return "\n".join(p.read_text(errors="replace") for p in logs)


@_NEEDS_GTIMEOUT
def test_the_watchdog_does_not_relaunch_an_operator_stop(tmp_path: Path, skill_copy: Path) -> None:
    w = _world(tmp_path)
    # Ended after the watchdog starts would be impossible; a sentinel ended a
    # moment before it with no live loop routes to classify, as at 22:58:25.
    _sentinel(w["launcher"], stopped_by="signal:TERM", ended=datetime.now())
    proc, launches = _run_watchdog(skill_copy, w)
    log = _activity(w) + proc.stdout + proc.stderr
    assert "max-iter-bound" in log, "the stubbed postmortem was not consulted:\n" + log[-3000:]
    assert launches == "", f"relaunched an operator stop: {launches!r}\n{log[-3000:]}"
    assert "STOPPED BY OPERATOR" in log, log[-3000:]


@_NEEDS_GTIMEOUT
def test_the_watchdog_does_not_relaunch_a_held_project(tmp_path: Path, skill_copy: Path) -> None:
    w = _world(tmp_path, held=True)
    _sentinel(w["launcher"], stopped_by=None, ended=datetime.now())
    proc, launches = _run_watchdog(skill_copy, w)
    log = _activity(w) + proc.stdout + proc.stderr
    assert launches == "", f"relaunched a held project: {launches!r}\n{log[-3000:]}"
    assert "HELD" in log, log[-3000:]


@_NEEDS_GTIMEOUT
def test_control_the_watchdog_still_relaunches_a_crash(tmp_path: Path, skill_copy: Path) -> None:
    w = _world(tmp_path)
    _sentinel(w["launcher"], stopped_by=None, ended=datetime.now())
    proc, launches = _run_watchdog(skill_copy, w)
    log = _activity(w) + proc.stdout + proc.stderr
    assert "--force" in launches, f"a crash was not relaunched\n{log[-3000:]}"


# ── AC-1: the runner records the signal ───────────────────────────────────

@_NEEDS_GTIMEOUT
def test_sigterm_to_the_runner_is_recorded_as_an_operator_stop(tmp_path: Path) -> None:
    w = _world(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    started = tmp_path / "agent-started"
    (bin_dir / "claude").write_text(
        f"#!/usr/bin/env bash\ntouch {shlex.quote(str(started))}\nsleep 60\n")
    (bin_dir / "claude").chmod(0o755)
    (tmp_path / ".claude").mkdir()
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source {shlex.quote(str(_DRIVER))} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
main --project-path {shlex.quote(str(w["project"]))} \\
     --max-iterations 1 --iteration-timeout-min 2 --model test-model
"""
    env = {**os.environ,
           "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
           "HOME": str(tmp_path), "ILK_DATA_HOME": str(w["data_home"])}
    env.pop("ILK_DATA_DIR", None)
    env.pop("ILK_DOTSOURCE_ONLY", None)
    proc = subprocess.Popen(["bash", "-c", script], env=env, cwd=str(tmp_path),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    try:
        deadline = time.time() + 90
        while not started.exists() and time.time() < deadline:
            time.sleep(0.5)
        assert started.exists(), "the stub agent never started"
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    d = json.loads((w["launcher"] / "last-exit.json").read_text())
    assert d["state"] == "interrupted", (d, out[-2000:])
    assert d.get("stopped_by") == "signal:TERM", (d, out[-2000:])


# ── AC-4: scheduler sentinel path ─────────────────────────────────────────

def test_scheduler_resolves_the_sentinel_through_ilk_paths(tmp_path: Path) -> None:
    data_home = tmp_path / "data"
    d = data_home / "projects" / "k1"
    script = f"""
source {shlex.quote(str(_SKILLS / "ilk-loop" / "scripts" / "_ilk_skill_root.sh"))}
_SKILL_ROOT={shlex.quote(str(_SKILLS))}
PYTHON=python3
eval "$(sed -n '/^sentinel_path_for_data_dir()/,/^}}/p' {shlex.quote(str(_SKILLS / "ilk-watchdog" / "scripts" / "scheduler.sh"))})"
sentinel_path_for_data_dir {shlex.quote(str(d))}
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "ILK_DATA_HOME": str(data_home)})
    assert r.stdout.strip() == str(d / "runtime" / "launcher" / "last-exit.json"), (
        r.stdout + r.stderr)
    text = (_SKILLS / "ilk-watchdog" / "scripts" / "scheduler.sh").read_text()
    assert "/runtime/last-exit.json" not in text, "a hand-joined dead sentinel path remains"
