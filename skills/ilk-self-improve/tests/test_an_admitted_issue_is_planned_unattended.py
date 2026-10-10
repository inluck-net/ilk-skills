"""Sub-plan ``an-admitted-issue-is-planned-unattended`` — plan-issue.

``autoplan.py plan-issue`` turns one admitted GitHub issue into a queued
master inside the issue's own worktree, unattended.  The CLI contract is the
MASTER's "Contract already sent to gh-resolve" block and is binding.

One test per AC of the sub-plan.  The red-first pins from step 0 are gone:
``plan_issue`` is built (step 2).

AC-1: a stub planner that writes one MASTER + one work sub-plan, with lint and
      preflight stubs exiting 0, ⇒ outcome ``queued``, return 0, MASTER status
      ``queued``, and ``master_path`` / ``plans_dir`` inside the plans dir
      resolved from the fixture ``project_root`` (not any toolkit repo).
AC-2: a lint stub exiting 1 ⇒ outcome ``drafted``, problems contain
      ``lint-exit-1``, return 3, MASTER stays ``draft``.
AC-3: a stub planner that writes nothing and ends with
      ``AUTOPLAN: unplannable vague`` ⇒ outcome ``failed``, return 1.
AC-4: input missing ``project_root`` ⇒ outcome ``failed`` with
      ``bad-input: project_root``, return 1, and no planner is spawned.
AC-5: the stub planner's argv prompt carries the issue body verbatim, the
      base_branch, every scope path, and its cwd is ``project_root``.
AC-6: after a run, ``<data_root>/autoplan/state.json``, ``inflight.json`` and
      ``paused.json`` are unchanged/absent — plan-issue never touches the RSI
      lane's state (MASTER judgment call d).
AC-7: ``autoplan.py plan-issue --help`` exits 0.

The fixture ``project_root`` is a tmp git repo on ``main`` whose
``.ilk-launch.json`` declares ``ship.verification_subplan: optional``, so the
single work sub-plan passes ``check_master`` without a batch-verification
sub-plan (the rail order 1 waived).  Hermetic: HOME / ILK_DATA_HOME /
ILK_DATA_DIR all point into tmp_path; no real ``~/.ilk-data``, no real claude.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SELF_SCRIPTS = _HERE.parent / "scripts"
_LOOP_SCRIPTS = _HERE.parent.parent / "ilk-loop" / "scripts"
_WATCHDOG_SCRIPTS = _HERE.parent.parent / "ilk-watchdog" / "scripts"
_FEEDBACK_SCRIPTS = _HERE.parent.parent / "ilk-feedback" / "scripts"
for _d in (_LOOP_SCRIPTS, _SELF_SCRIPTS, _WATCHDOG_SCRIPTS, _FEEDBACK_SCRIPTS):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import autoplan  # noqa: E402
from ilk_paths import external_plans_dir, project_key as ilk_project_key  # noqa: E402
from plan_status import parse_frontmatter  # noqa: E402

_AUTOPLAN_PY = _SELF_SCRIPTS / "autoplan.py"

_ISSUE = {
    "repo": "acme/frobber",
    "number": 4242,
    "title": "frobnicator crashes on empty input",
    "body": "The frobnicator crashes\nwhen the input is empty.\nSee `src/frob.py`.",
    "url": "https://github.com/acme/frobber/issues/4242",
}
_BASE_BRANCH = "dev"
_SCOPE_PATHS = ["src/frob.py", "tests/test_frob.py", "docs/frob.md"]
_WRITE_TARGETS = ["src/frob.py"]
_LOCAL_CHECKS = ["python3 -m pytest tests/test_frob.py -q"]
_RUN_ID = "gh-resolve-run-001"


# ── helpers (hermetic; mirror test_autoplan_judges_the_planned_repo) ─────────


@pytest.fixture(autouse=True)
def _hermetic_env(tmp_path, monkeypatch):
    """Never touch the real ~/.ilk-data: pin HOME and the data-home vars."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_home = tmp_path / "ilk-data"
    data_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("ILK_DATA_DIR", str(data_home))
    return data_home


def _git(repo: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"


def _make_project_root(tmp_path: Path) -> Path:
    """Fixture worktree: a tmp git repo on ``main``, waiving the verify
    sub-plan so a single work sub-plan is a clean batch (order 1's key)."""
    repo = tmp_path / "worktree"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "frob.py").write_text("def frob(x):\n    return x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / ".ilk-launch.json").write_text(
        json.dumps({
            "ship": {
                "suite": {"command": "python3 -m pytest"},
                "verification_subplan": "optional",
            },
        }) + "\n",
        encoding="utf-8",
    )
    return repo


def _write_stub(path: Path, body: str) -> list[str]:
    """Write an executable python stub and return its argv prefix."""
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return [sys.executable, str(path)]


_STUB_BODY = """\
import json, os, pathlib, sys

rec = os.environ.get("ILK_STUB_RECORD")
if rec:
    pathlib.Path(rec).write_text(json.dumps({
        "argv": sys.argv,
        "cwd": os.getcwd(),
    }), encoding="utf-8")

print(json.dumps({"type": "system", "subtype": "init",
                  "model": "claude-opus-test"}))

if os.environ.get("ILK_STUB_WRITE", "1") == "1":
    plans_dir = pathlib.Path(os.environ.get("ILK_PLANS_DIR", "."))
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "MASTER-2026-10-10-frob-batch.md").write_text(
        MASTER_TEXT, encoding="utf-8")
    (plans_dir / "2026-10-10-frob-work.md").write_text(
        WORK_TEXT, encoding="utf-8")

for line in (os.environ.get("ILK_STUB_FINAL") or "").splitlines():
    print(line)
"""

_MASTER_TEXT = """\
---
master_plan: 2026-10-10-frob-batch
batch_date: 2026-10-10
source_status: admitted
total_tickets: 1
status: draft
current_subplan: 2026-10-10-frob-work.md
---

# MASTER plan: frob batch

## Sub-plan registry

| # | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|
| 0 | [2026-10-10-frob-work.md](./2026-10-10-frob-work.md) | 1 | 2 | pending |
"""

_WORK_TEXT = """\
---
plan: frob-work
status: pending
current_step: 0
tickets: []
priority: P1
estimated_steps: 2
last_updated: 2026-10-10
verification_tier: loop-verified
batch_verification: false
scope_paths:
  - "src/frob.py"
---

# Sub-plan: frob work

## Steps

### Step 0 — fix it

Edit `src/frob.py`.

### Step 1 — test it

Run the checks.
"""


def _make_stub_claude(tmp_path: Path) -> list[str]:
    body = (
        "MASTER_TEXT = " + repr(_MASTER_TEXT) + "\n"
        "WORK_TEXT = " + repr(_WORK_TEXT) + "\n"
        + _STUB_BODY
    )
    return _write_stub(tmp_path / "stub-claude.py", body)


def _ok_check(tmp_path: Path, name: str) -> list[str]:
    return _write_stub(tmp_path / name, "import sys\nsys.exit(0)\n")


def _red_check(tmp_path: Path, name: str) -> list[str]:
    return _write_stub(tmp_path / name, "import sys\nsys.exit(1)\n")


def _read_json_file(path: Path):
    """Read JSON, converting I/O and parse faults into AssertionError so the
    red-first pin's ``raises=`` tuple stays honest."""
    assert path.exists(), f"missing {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise AssertionError(f"unparseable {path}: {exc}") from exc


def _read_master_status(plans_dir: Path) -> str:
    masters = sorted(plans_dir.glob("MASTER-*.md"))
    assert len(masters) == 1, f"expected exactly one MASTER, got {masters}"
    fm = parse_frontmatter(masters[0].read_text(encoding="utf-8-sig"))
    return (fm.get("status") or "").strip()


def _write_input(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "issue.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _base_input(project_root: Path) -> dict:
    return {
        "issue": dict(_ISSUE),
        "project_root": str(project_root),
        "base_branch": _BASE_BRANCH,
        "scope_paths": list(_SCOPE_PATHS),
        "write_targets": list(_WRITE_TARGETS),
        "local_checks": list(_LOCAL_CHECKS),
        "run_id": _RUN_ID,
    }


def _run_plan_issue(
    tmp_path: Path,
    data_root: Path,
    project_root: Path,
    *,
    input_payload: dict | None = None,
    lint_cmd: list[str] | None = None,
    preflight_cmd: list[str] | None = None,
    stub_write: bool = True,
    stub_final: str = "",
    record_path: Path | None = None,
):
    """Call ``plan_issue`` once with a stub planner.  Returns (rc, outcome)."""
    outcome_path = tmp_path / "outcome.json"
    input_path = _write_input(tmp_path, input_payload or _base_input(project_root))
    stub_claude = _make_stub_claude(tmp_path)
    plans_dir = external_plans_dir(ilk_project_key(project_root))

    env_overrides = {
        "ILK_PLANS_DIR": str(plans_dir),
        "ILK_STUB_WRITE": "1" if stub_write else "0",
    }
    if stub_final:
        env_overrides["ILK_STUB_FINAL"] = stub_final
    if record_path is not None:
        env_overrides["ILK_STUB_RECORD"] = str(record_path)

    rc = autoplan.plan_issue(
        input_path=input_path,
        outcome_path=outcome_path,
        data_root=data_root,
        manager_home=str(tmp_path / ".claude-manager"),
        claude_cmd=stub_claude,
        lint_cmd=lint_cmd or _ok_check(tmp_path, "ok-lint.py"),
        preflight_cmd=preflight_cmd or _ok_check(tmp_path, "ok-preflight.py"),
        env_overrides=env_overrides,
    )
    outcome = _read_json_file(outcome_path)
    return rc, outcome


# ── AC-1: a clean run queues the master in the project's own plans dir ───────


def test_ac1_clean_run_queues_in_the_project_plans_dir(tmp_path, _hermetic_env):
    data_root = _hermetic_env
    project_root = _make_project_root(tmp_path)

    rc, outcome = _run_plan_issue(tmp_path, data_root, project_root)

    assert rc == 0, (rc, outcome)
    assert outcome.get("outcome") == "queued", outcome

    expected_plans = data_root / "projects" / ilk_project_key(project_root) / "plans"
    plans_dir = Path(outcome.get("plans_dir") or "")
    master_path = Path(outcome.get("master_path") or "")
    assert plans_dir.resolve() == expected_plans.resolve(), (plans_dir, expected_plans)
    assert master_path.parent.resolve() == expected_plans.resolve(), master_path
    assert master_path.exists(), f"missing {master_path}"

    assert _read_master_status(expected_plans) == "queued"
    assert outcome.get("planner_log"), outcome


# ── AC-2: a red lint drafts and returns 3 ────────────────────────────────────


def test_ac2_red_lint_drafts_and_returns_3(tmp_path, _hermetic_env):
    data_root = _hermetic_env
    project_root = _make_project_root(tmp_path)

    rc, outcome = _run_plan_issue(
        tmp_path, data_root, project_root,
        lint_cmd=_red_check(tmp_path, "red-lint.py"),
    )

    assert rc == 3, (rc, outcome)
    assert outcome.get("outcome") == "drafted", outcome
    problems = outcome.get("problems") or []
    assert "lint-exit-1" in problems, outcome

    expected_plans = data_root / "projects" / ilk_project_key(project_root) / "plans"
    assert _read_master_status(expected_plans) == "draft"


# ── AC-3: an unplannable issue fails with 1 ──────────────────────────────────


def test_ac3_unplannable_issue_fails_with_1(tmp_path, _hermetic_env):
    data_root = _hermetic_env
    project_root = _make_project_root(tmp_path)

    rc, outcome = _run_plan_issue(
        tmp_path, data_root, project_root,
        stub_write=False,
        stub_final="AUTOPLAN: unplannable vague",
    )

    assert rc == 1, (rc, outcome)
    assert outcome.get("outcome") == "failed", outcome
    problems = outcome.get("problems") or []
    assert problems, outcome


# ── AC-4: bad input fails before any planner is spawned ──────────────────────


def test_ac4_missing_project_root_is_bad_input(tmp_path, _hermetic_env):
    data_root = _hermetic_env
    project_root = _make_project_root(tmp_path)
    record_path = tmp_path / "stub-record.json"

    payload = _base_input(project_root)
    del payload["project_root"]

    rc, outcome = _run_plan_issue(
        tmp_path, data_root, project_root,
        input_payload=payload,
        record_path=record_path,
    )

    assert rc == 1, (rc, outcome)
    assert outcome.get("outcome") == "failed", outcome
    problems = outcome.get("problems") or []
    assert "bad-input: project_root" in problems, outcome
    assert not record_path.exists(), f"planner was spawned: {record_path}"


# ── AC-5: the prompt carries the issue and its constraints ───────────────────


def test_ac5_prompt_carries_the_issue_and_cwd_is_the_worktree(tmp_path, _hermetic_env):
    data_root = _hermetic_env
    project_root = _make_project_root(tmp_path)
    record_path = tmp_path / "stub-record.json"

    rc, outcome = _run_plan_issue(
        tmp_path, data_root, project_root,
        record_path=record_path,
    )
    assert rc == 0, (rc, outcome)

    rec = _read_json_file(record_path)
    assert Path(rec.get("cwd") or "").resolve() == project_root.resolve(), rec

    prompt = "\n".join(rec.get("argv") or [])
    assert _ISSUE["body"] in prompt, "issue body missing from prompt"
    assert _BASE_BRANCH in prompt, "base_branch missing from prompt"
    for sp in _SCOPE_PATHS:
        assert sp in prompt, f"scope path {sp} missing from prompt"
    for wt in _WRITE_TARGETS:
        assert wt in prompt, f"write target {wt} missing from prompt"
    for lc in _LOCAL_CHECKS:
        assert lc in prompt, f"local check {lc} missing from prompt"
    assert _RUN_ID in prompt, "run_id missing from prompt"


# ── AC-6: the RSI lane's state is never touched ──────────────────────────────


def test_ac6_plan_issue_leaves_lane_state_alone(tmp_path, _hermetic_env):
    data_root = _hermetic_env
    project_root = _make_project_root(tmp_path)

    autoplan_dir = data_root / "autoplan"
    autoplan_dir.mkdir(parents=True, exist_ok=True)
    state_file = autoplan_dir / "state.json"
    state_before = json.dumps({"consecutive_drafts": 1, "last_outcome": "drafted"}) + "\n"
    state_file.write_text(state_before, encoding="utf-8")

    rc, outcome = _run_plan_issue(tmp_path, data_root, project_root)
    assert rc == 0, (rc, outcome)

    assert state_file.read_text(encoding="utf-8") == state_before, "state.json was written"
    assert not (autoplan_dir / "inflight.json").exists(), "inflight.json was written"
    assert not (autoplan_dir / "paused.json").exists(), "paused.json was written"


# ── AC-7: the subcommand advertises itself ───────────────────────────────────


def test_ac7_plan_issue_help_exits_zero(tmp_path, _hermetic_env):
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["ILK_DATA_HOME"] = str(_hermetic_env)
    env["ILK_DATA_DIR"] = str(_hermetic_env)

    proc = subprocess.run(
        [sys.executable, str(_AUTOPLAN_PY), "plan-issue", "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--input" in proc.stdout, proc.stdout
    assert "--outcome" in proc.stdout, proc.stdout
