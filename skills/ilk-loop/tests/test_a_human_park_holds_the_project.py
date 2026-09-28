"""Pins for D3 park/hold (design §4, §7 D3; agreed with gh-resolve-59).

A human park writes ``hold: human`` and holds the whole PROJECT.  Until
2026-09-29 park and stall shared ``status: blocked``, which removes one master
from the queue and nothing else: the scheduler promoted and dispatched the
project's other masters, and the watchdog relaunched it, while the operator
believed it was stopped.

AC-1: a park without --auto writes hold: human; --yield adds yield: true;
      --auto (the runner's ship-integrity park) writes no hold.
AC-2: --unpark refuses a human hold without --release-hold (condition A:
      gh-resolve's reaper unparks auto-parks with a plain --unpark); with it,
      the master is queued and every park field is gone.  An auto-park still
      unparks with a plain --unpark.
AC-3: plan_status.project_held_by names the holding master; a yielded park
      and an auto-park hold nothing.
AC-4: scheduler_scan leaves a held project out and says `skip-held`, even
      when another master of the project is queued.
AC-5: promote_next_master refuses a held project (exit 1, nothing written).
AC-6: promote_next_master refuses while the selfmod worktree holds commits
      that are not on the clone's HEAD, and promotes once they are.
AC-7: loop_status --json carries held_by; the text says "HELD by".
AC-8: the runner classifies a held project blocked-no-runnable, and its
      terminal sentinel carries an additive held_by.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"
_WATCHDOG = _REPO / "skills" / "ilk-watchdog" / "scripts"
_PARK = _SCRIPTS / "park_master.py"
_PROMOTE = _SCRIPTS / "promote_next_master.py"
_LOOP_STATUS = _SCRIPTS / "loop_status.py"
_RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"

for _p in (_SCRIPTS, _WATCHDOG):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from plan_status import parse_frontmatter, project_held_by  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────

def _master(plans: Path, name: str, status: str, subplan: str,
            extra: str = "") -> Path:
    p = plans / name
    p.write_text(
        "---\n"
        f"master_plan: {name[:-3]}\n"
        f"status: {status}\n"
        f"{extra}"
        "---\n\n# batch\n\n| # | Slug | Status |\n|---|---|---|\n"
        f"| 1 | {subplan} | pending |\n",
        encoding="utf-8",
    )
    return p


def _subplan(plans: Path, fname: str, status: str = "pending") -> None:
    slug = fname[len("2026-09-29-"):-3]
    (plans / fname).write_text(
        f"---\nplan: {slug}\nstatus: {status}\ncurrent_step: 0\n"
        "estimated_steps: 1\n---\n\n### Step 0 — do it\n",
        encoding="utf-8",
    )


def _two_masters(plans: Path) -> tuple[Path, Path]:
    """A queued master and an active one, each with one pending sub-plan."""
    plans.mkdir(parents=True, exist_ok=True)
    _subplan(plans, "2026-09-29-aaa.md")
    _subplan(plans, "2026-09-29-bbb.md")
    a = _master(plans, "MASTER-2026-09-29a.md", "active", "2026-09-29-aaa.md")
    b = _master(plans, "MASTER-2026-09-29b.md", "queued", "2026-09-29-bbb.md")
    return a, b


def _run(script: Path, *args: str, cwd: Path | None = None,
         env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, cwd=cwd, env={**os.environ, **(env or {})},
    )


def _park(plans: Path, *args: str) -> subprocess.CompletedProcess:
    return _run(_PARK, "--plans-dir", str(plans), *args)


def _fm(p: Path) -> dict:
    return parse_frontmatter(p.read_text(encoding="utf-8"))


# ── AC-1 ────────────────────────────────────────────────────────────────────

def test_a_human_park_writes_a_hold(tmp_path: Path) -> None:
    a, _ = _two_masters(tmp_path)
    r = _park(tmp_path, "--master", a.name, "--reason", "stop, redesign #56")
    assert r.returncode == 0, r.stdout + r.stderr
    fm = _fm(a)
    assert fm["status"] == "blocked"
    assert fm.get("hold") == "human", fm
    assert "yield" not in fm, fm
    assert fm.get("parked_reason") == "stop, redesign #56"


def test_yield_writes_a_yielding_hold(tmp_path: Path) -> None:
    a, _ = _two_masters(tmp_path)
    r = _park(tmp_path, "--master", a.name, "--reason", "r", "--yield")
    assert r.returncode == 0, r.stdout + r.stderr
    fm = _fm(a)
    assert fm.get("hold") == "human" and fm.get("yield") == "true", fm


def test_an_auto_park_writes_no_hold(tmp_path: Path) -> None:
    a, _ = _two_masters(tmp_path)
    r = _park(tmp_path, "--owner-of", "aaa", "--auto",
              "--reason", "ship_integrity_violation: run R1 slug=aaa")
    assert r.returncode == 0, r.stdout + r.stderr
    fm = _fm(a)
    assert fm["status"] == "blocked"
    assert "hold" not in fm, fm
    assert fm["parked_reason"] == "ship_integrity_violation: run R1 slug=aaa"


def test_the_runner_auto_park_passes_auto() -> None:
    """Both runners' ship-integrity parks are machine parks.

    Without --auto they would now write a human hold that the gh-resolve
    reaper's plain --unpark cannot lift -- the reverse of condition A.
    """
    for runner, needle in (
        (_RUNNER, "--auto \\"),
        (_SCRIPTS / "run_ilk_loop_claude.ps1", "--auto --reason $parkReason"),
    ):
        text = runner.read_text(encoding="utf-8")
        i = text.find("park_master.py")
        assert i >= 0, runner
        assert needle in text[i:i + 1500], f"{runner.name}: park call lacks --auto"


# ── AC-2 ────────────────────────────────────────────────────────────────────

def test_unpark_refuses_a_human_hold(tmp_path: Path) -> None:
    a, _ = _two_masters(tmp_path)
    assert _park(tmp_path, "--master", a.name, "--reason", "r").returncode == 0
    before = a.read_bytes()
    r = _park(tmp_path, "--unpark", "--master", a.name)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "--release-hold" in r.stdout
    assert a.read_bytes() == before, "a refused unpark must write nothing"


def test_release_hold_unparks_and_strips_every_park_field(tmp_path: Path) -> None:
    a, _ = _two_masters(tmp_path)
    assert _park(tmp_path, "--master", a.name, "--reason", "r",
                 "--yield").returncode == 0
    r = _park(tmp_path, "--unpark", "--master", a.name, "--release-hold")
    assert r.returncode == 0, r.stdout + r.stderr
    fm = _fm(a)
    assert fm["status"] == "queued"
    for k in ("hold", "yield", "parked_at", "parked_reason"):
        assert k not in fm, (k, fm)


def test_a_plain_unpark_still_lifts_an_auto_park(tmp_path: Path) -> None:
    a, _ = _two_masters(tmp_path)
    assert _park(tmp_path, "--owner-of", "aaa", "--auto",
                 "--reason", "x").returncode == 0
    r = _park(tmp_path, "--unpark", "--master", a.name)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _fm(a)["status"] == "queued"


# ── AC-3 ────────────────────────────────────────────────────────────────────

def test_project_held_by(tmp_path: Path) -> None:
    a, _ = _two_masters(tmp_path)
    assert project_held_by(tmp_path) is None
    assert _park(tmp_path, "--master", a.name, "--reason", "why").returncode == 0
    held = project_held_by(tmp_path)
    assert held is not None and held["master"] == a.name
    assert held["reason"] == "why"


def test_a_yielded_park_holds_nothing(tmp_path: Path) -> None:
    a, _ = _two_masters(tmp_path)
    assert _park(tmp_path, "--master", a.name, "--reason", "r",
                 "--yield").returncode == 0
    assert project_held_by(tmp_path) is None


def test_the_hold_survives_a_status_edit(tmp_path: Path) -> None:
    """A worker that flips a held master's status does not release the project."""
    a, _ = _two_masters(tmp_path)
    assert _park(tmp_path, "--master", a.name, "--reason", "r").returncode == 0
    a.write_text(a.read_text().replace("status: blocked", "status: queued"))
    assert project_held_by(tmp_path) is not None


# ── AC-4 ────────────────────────────────────────────────────────────────────

def test_scheduler_scan_skips_a_held_project(tmp_path: Path, capsys) -> None:
    import scheduler_scan

    project_dir = tmp_path / "projects" / "k-held"
    plans = project_dir / "plans"
    a, b = _two_masters(plans)
    # Control: runnable before the park.
    assert scheduler_scan._scan_one_project(project_dir) is not None

    assert _park(plans, "--master", a.name, "--reason", "r").returncode == 0
    # MASTER-b is still `queued` with a pending sub-plan: before D3 the scan
    # returned the project for promotion and dispatch.
    assert _fm(b)["status"] == "queued"
    capsys.readouterr()
    assert scheduler_scan._scan_one_project(project_dir) is None
    err = capsys.readouterr().err
    assert f"skip-held: k-held ({a.name})" in err, err


# scheduler.sh copying that line into scheduler.log is pinned at runtime in
# skills/ilk-watchdog/tests/test_scheduler_scan_error_reason.sh (AC-3).


# ── AC-5 ────────────────────────────────────────────────────────────────────

def test_promote_refuses_a_held_project(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    a, b = _two_masters(plans)
    # Make MASTER-a a stalled active so a normal promote WOULD act.
    _subplan(plans, "2026-09-29-aaa.md", status="blocked")
    ctl = _run(_PROMOTE, "--plans-dir", str(plans), "--dry-run")
    assert json.loads(ctl.stdout)["promoted"] == b.name, ctl.stdout

    assert _park(plans, "--master", a.name, "--reason", "r").returncode == 0
    before = {p.name: p.read_bytes() for p in plans.iterdir()}
    r = _run(_PROMOTE, "--plans-dir", str(plans))
    assert r.returncode == 1, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["refused"] == "held" and out["held_by"] == a.name
    assert out["promoted"] is None
    assert {p.name: p.read_bytes() for p in plans.iterdir()} == before


# ── AC-6 ────────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


def test_promote_refuses_while_selfmod_commits_are_unmerged(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-q")
    (clone / "f").write_text("0")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "init")

    data = tmp_path / "data"
    plans = data / "plans"
    _, b = _two_masters(plans)
    # Nothing active: promotion would pick MASTER-b.
    (plans / "MASTER-2026-09-29a.md").write_text(
        (plans / "MASTER-2026-09-29a.md").read_text().replace(
            "status: active", "status: shipped"))
    _subplan(plans, "2026-09-29-aaa.md", status="shipped")

    wt = data / "runtime" / "worktrees" / "selfmod-batch"
    wt.parent.mkdir(parents=True)
    _git(clone, "worktree", "add", "-q", "-b", "selfmod-batch", str(wt))
    # Control: a worktree with nothing unmerged does not refuse.
    r = _run(_PROMOTE, "--plans-dir", str(plans), "--dry-run")
    assert r.returncode == 0 and json.loads(r.stdout)["promoted"] == b.name, r.stdout

    (wt / "f").write_text("1")
    _git(wt, "commit", "-qam", "batch work")
    r = _run(_PROMOTE, "--plans-dir", str(plans))
    assert r.returncode == 1, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["refused"] == "selfmod-unmerged", out
    assert _fm(b)["status"] == "queued", "a refusal must not promote"

    # Once the clone has the commit, promotion proceeds.
    _git(clone, "merge", "-q", "--ff-only", "selfmod-batch")
    r = _run(_PROMOTE, "--plans-dir", str(plans), "--dry-run")
    assert r.returncode == 0 and json.loads(r.stdout)["promoted"] == b.name, r.stdout


# ── AC-7 / AC-8: loop_status and the runner ────────────────────────────────

def _project_with_held_master(tmp_path: Path) -> tuple[Path, Path, Path]:
    import ilk_paths

    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-q")
    (project / "x").write_text("x")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "init")
    data_home = tmp_path / "ilk-data"
    prev = os.environ.get("ILK_DATA_HOME")
    os.environ["ILK_DATA_HOME"] = str(data_home)
    try:
        key = ilk_paths.project_key(project)
    finally:
        if prev is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = prev
    plans = data_home / "projects" / key / "plans"
    a, _ = _two_masters(plans)
    assert _park(plans, "--master", a.name, "--reason", "operator stop").returncode == 0
    return project, data_home, plans


def test_loop_status_reports_the_hold(tmp_path: Path) -> None:
    project, data_home, _ = _project_with_held_master(tmp_path)
    env = {"ILK_DATA_HOME": str(data_home)}
    j = _run(_LOOP_STATUS, "--json", cwd=project, env=env)
    data = json.loads(j.stdout)
    assert data["held_by"] == "MASTER-2026-09-29a.md", data
    assert data["next"] is None and data["stalled"] is False
    t = _run(_LOOP_STATUS, cwd=project, env=env)
    assert "HELD by MASTER-2026-09-29a.md (human park): operator stop" in t.stdout, t.stdout


def test_the_runner_classifies_a_held_project_and_records_it(tmp_path: Path) -> None:
    project, data_home, _ = _project_with_held_master(tmp_path)
    rd = tmp_path / "rd"
    rd.mkdir()
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        source '{_RUNNER}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        set +eE +o pipefail
        PROJECT_PATH='{project}'
        LOOP_STATUS_SCRIPT='{_LOOP_STATUS}'
        export ILK_DATA_HOME='{data_home}'
        classify_loop_status
        echo "CLASSIFIED=$CLASSIFIED_STATUS HELD=$HELD_BY"
        RUN_ID=R1
        _write_terminal_sentinel blocked-no-runnable '{rd}'
    """)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120)
    assert "CLASSIFIED=blocked-no-runnable HELD=MASTER-2026-09-29a.md" in r.stdout, (
        r.stdout + r.stderr)
    sentinel = json.loads((rd / "last-exit.json").read_text())
    assert sentinel["state"] == "blocked-no-runnable"
    assert sentinel["held_by"] == "MASTER-2026-09-29a.md", sentinel
