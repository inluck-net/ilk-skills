"""Sub-plan ``autoplan-judges-the-planned-repo`` step 0 — red-first pins.

AC-1..AC-4 are ``xfail(strict=True)`` until step 1 implements the design
(``_run_check``: fail closed on timeout/unrunnable, keep the output, lint
the planned repo).  AC-5 (clean lint + preflight still queues) passes at
base and is unpinned.

AC-1: a lint_cmd sleeping past ``CHECK_TIMEOUT_S`` ⇒ ``drafted`` with
      problem ``lint-timeout`` (today: ``queued`` — a timeout is a pass).
AC-2: a preflight_cmd naming a non-existent executable ⇒ ``drafted`` with
      ``preflight-unrunnable`` (today: ``queued`` — OSError is a pass).
AC-3: a lint_cmd printing ``WARN: x`` and exiting 1 ⇒ ``drafted`` with
      ``lint-exit-1``, audit ``lint_tail`` contains ``WARN: x``, and
      ``<data_root>/autoplan/runs/<run_id>.lint.txt`` exists.
AC-4: lint argv contains ``--git-cwd <repo>`` and the lint subprocess runs
      with cwd = the planned repo (today: no ``--git-cwd``, cwd inherited).
AC-5: clean lint + preflight still queues.
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
_LOOP_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts")
_SELF_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
_WATCHDOG_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-watchdog" / "scripts")
_FEEDBACK_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts")

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "autoplan"


# ── helpers (hermetic; mirror test_an_idle_window_plans_the_top_candidate) ────


def _load_module():
    for d in (_LOOP_SCRIPTS, _SELF_SCRIPTS, _WATCHDOG_SCRIPTS, _FEEDBACK_SCRIPTS):
        if d not in sys.path:
            sys.path.insert(0, d)
    import autoplan
    return autoplan


@pytest.fixture(autouse=True)
def _hermetic_env(tmp_path, monkeypatch):
    """Never touch the real ~/.ilk-data: pin HOME and the data-home vars."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_home = tmp_path / "ilk-data-home"
    data_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("ILK_DATA_DIR", str(data_home))


def _make_candidate() -> dict:
    return {
        "id": "sig-abc123",
        "title": "Missing feature X",
        "gap": "No support for X",
        "source": "triage",
        "status": "open",
        "seen_count": 1,
        "first_seen": "2026-10-03T12:00:00+0800",
        "relations": {
            "escalations": 0,
            "urgent": False,
            "autoplan_attempts": 0,
            "autoplan_blocked": False,
        },
        "evidence": [],
        "proposed_fix": "",
        "kind": "toolkit",
        "leverage": "medium",
        "severity": "medium",
    }


def _write_stub(path: Path, body: str) -> list[str]:
    """Write an executable python stub and return its argv prefix."""
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return [sys.executable, str(path)]


def _make_stub_claude(tmp_path) -> list[str]:
    """Stub planner: print an init event and copy the fixture batch."""
    init_event = json.dumps({
        "type": "system",
        "subtype": "init",
        "model": "claude-opus-test",
    })
    body = (
        "import json, os, sys, pathlib\n"
        f"print({init_event!r})\n"
        "plans_dir = pathlib.Path(os.environ.get('ILK_PLANS_DIR', '.'))\n"
        "fixture_dir = pathlib.Path(os.environ.get('ILK_FIXTURE_DIR', ''))\n"
        "for f in fixture_dir.iterdir():\n"
        "    if f.suffix == '.md':\n"
        "        (plans_dir / f.name).write_text(f.read_text())\n"
    )
    return _write_stub(tmp_path / "stub-claude.py", body)


def _setup_plan_env(tmp_path, data_root, toolkit):
    """Project dir + backlog + plans dir, as the idle-window tests build them."""
    project_dir = data_root / "projects" / "test-project"
    (project_dir / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)
    (project_dir / "runtime" / "launcher" / "last-launch.json").write_text(
        json.dumps({"project_path": str(toolkit)}) + "\n",
        encoding="utf-8",
    )
    backlog_dir = data_root / "ilk-skills-improvements"
    backlog_dir.mkdir(parents=True, exist_ok=True)
    (backlog_dir / "candidates.json").write_text(
        json.dumps([_make_candidate()], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    plans_dir = data_root / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    return plans_dir


def _run_plan(tmp_path, data_root, toolkit, manager_home, lint_cmd, preflight_cmd):
    """Run plan() once with the stub planner and the given lint/preflight argv."""
    mod = _load_module()
    plans_dir = _setup_plan_env(tmp_path, data_root, toolkit)
    stub_claude = _make_stub_claude(tmp_path)
    return mod.plan(
        candidate_id="sig-abc123",
        project_key="test-project",
        run_id="run-001",
        data_root=data_root,
        toolkit_repo=str(toolkit),
        manager_home=str(manager_home),
        claude_cmd=stub_claude,
        lint_cmd=lint_cmd,
        preflight_cmd=preflight_cmd,
        env_overrides={
            "ILK_PLANS_DIR": str(plans_dir),
            "ILK_REPO_DIR": str(toolkit),
            "ILK_FIXTURE_DIR": str(FIXTURE_DIR),
        },
    )


def _build_world(tmp_path):
    """data_root + fake toolkit + fake manager home."""
    data_root = Path(tmp_path) / "ilk-data"
    (data_root / "projects").mkdir(parents=True, exist_ok=True)
    (data_root / "autoplan").mkdir(exist_ok=True)
    (data_root / "audit").mkdir(exist_ok=True)

    toolkit = Path(tmp_path) / "toolkit"
    (toolkit / "commands").mkdir(parents=True, exist_ok=True)
    (toolkit / "commands" / "ilk-plan.md").write_text("# ilk-plan\n", encoding="utf-8")
    (toolkit / ".ilk-launch.json").write_text(
        json.dumps({"autoplan": {"enabled": True}}) + "\n", encoding="utf-8",
    )

    manager_home = Path(tmp_path) / ".claude-manager"
    manager_home.mkdir(parents=True, exist_ok=True)
    return data_root, toolkit, manager_home


def _read_audit_rows(data_root, kind):
    rows = []
    for f in (data_root / "audit").glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("kind") == kind:
                    rows.append(row)
    return rows


# ── AC-1: a timed-out lint is a problem ──────────────────────────────────────


@pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError), reason="not built yet")
def test_ac1_lint_timeout_drafts_with_lint_timeout(tmp_path, monkeypatch):
    """lint_cmd sleeps past CHECK_TIMEOUT_S ⇒ drafted, problem lint-timeout.

    Today the timeout is swallowed by _run_cmd and the master queues.
    """
    data_root, toolkit, manager_home = _build_world(tmp_path)

    lint = Path(tmp_path) / "sleepy-lint.py"
    lint_cmd = _write_stub(lint, "import time, sys\ntime.sleep(3)\nsys.exit(0)\n")
    preflight = Path(tmp_path) / "ok-preflight.py"
    preflight_cmd = _write_stub(preflight, "import sys\nsys.exit(0)\n")

    mod = _load_module()
    # Injectable per the design: module constant CHECK_TIMEOUT_S = 300.
    monkeypatch.setattr(mod, "CHECK_TIMEOUT_S", 1, raising=False)

    result = _run_plan(tmp_path, data_root, toolkit, manager_home,
                       lint_cmd, preflight_cmd)

    assert result["decision"] == "drafted", result
    assert "lint-timeout" in result.get("problems", []), result


# ── AC-2: an unrunnable preflight is a problem ───────────────────────────────


@pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError), reason="not built yet")
def test_ac2_preflight_unrunnable_drafts_with_preflight_unrunnable(tmp_path):
    """preflight_cmd naming a missing executable ⇒ drafted, preflight-unrunnable.

    Today the OSError is swallowed by _run_cmd and the master queues.
    """
    data_root, toolkit, manager_home = _build_world(tmp_path)

    lint = Path(tmp_path) / "ok-lint.py"
    lint_cmd = _write_stub(lint, "import sys\nsys.exit(0)\n")
    preflight_cmd = ["/nonexistent/definitely-not-a-real-preflight-bin"]

    result = _run_plan(tmp_path, data_root, toolkit, manager_home,
                       lint_cmd, preflight_cmd)

    assert result["decision"] == "drafted", result
    assert "preflight-unrunnable" in result.get("problems", []), result


# ── AC-3: a red lint keeps its output ────────────────────────────────────────


@pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError), reason="not built yet")
def test_ac3_lint_exit_1_keeps_tail_and_log_file(tmp_path):
    """lint exit 1 with ``WARN: x`` ⇒ drafted / lint-exit-1 / tail / log file."""
    data_root, toolkit, manager_home = _build_world(tmp_path)

    lint = Path(tmp_path) / "warn-lint.py"
    lint_cmd = _write_stub(
        lint, "import sys\nprint('WARN: x')\nsys.exit(1)\n",
    )
    preflight = Path(tmp_path) / "ok-preflight.py"
    preflight_cmd = _write_stub(preflight, "import sys\nsys.exit(0)\n")

    result = _run_plan(tmp_path, data_root, toolkit, manager_home,
                       lint_cmd, preflight_cmd)

    assert result["decision"] == "drafted", result
    assert "lint-exit-1" in result.get("problems", []), result

    log_file = data_root / "autoplan" / "runs" / "run-001.lint.txt"
    assert log_file.exists(), f"missing {log_file}"

    rows = _read_audit_rows(data_root, "autoplan-drafted")
    assert rows, "no autoplan-drafted audit row"
    lint_tail = rows[-1].get("lint_tail") or ""
    assert "WARN: x" in lint_tail, rows[-1]


# ── AC-4: lint runs against the planned repo ─────────────────────────────────


@pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError), reason="not built yet")
def test_ac4_lint_gets_git_cwd_and_runs_in_the_repo(tmp_path):
    """Lint argv carries ``--git-cwd <repo>`` and the lint cwd is the repo."""
    data_root, toolkit, manager_home = _build_world(tmp_path)

    lint = Path(tmp_path) / "record-lint.py"
    lint_cmd = _write_stub(
        lint,
        "import json, os, sys, pathlib\n"
        "rec = pathlib.Path(__file__).parent / 'lint-record.json'\n"
        "rec.write_text(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv}))\n"
        "sys.exit(0)\n",
    )
    preflight = Path(tmp_path) / "ok-preflight.py"
    preflight_cmd = _write_stub(preflight, "import sys\nsys.exit(0)\n")

    result = _run_plan(tmp_path, data_root, toolkit, manager_home,
                       lint_cmd, preflight_cmd)

    rec = json.loads((tmp_path / "lint-record.json").read_text(encoding="utf-8"))
    assert "--git-cwd" in rec["argv"], rec
    assert str(toolkit) in rec["argv"], rec
    git_cwd = rec["argv"][rec["argv"].index("--git-cwd") + 1]
    assert git_cwd == str(toolkit), rec
    assert Path(rec["cwd"]).resolve() == toolkit.resolve(), rec


# ── AC-5: clean checks still queue (passes at base — unpinned) ───────────────


def test_ac5_clean_lint_and_preflight_still_queue(tmp_path):
    """A clean lint + preflight leaves the master queued."""
    data_root, toolkit, manager_home = _build_world(tmp_path)

    lint = Path(tmp_path) / "ok-lint.py"
    lint_cmd = _write_stub(lint, "import sys\nsys.exit(0)\n")
    preflight = Path(tmp_path) / "ok-preflight.py"
    preflight_cmd = _write_stub(preflight, "import sys\nsys.exit(0)\n")

    result = _run_plan(tmp_path, data_root, toolkit, manager_home,
                       lint_cmd, preflight_cmd)

    assert result["decision"] == "queued", result

    from plan_status import parse_frontmatter
    masters = list((data_root / "plans").glob("MASTER-*.md"))
    assert len(masters) == 1
    fm = parse_frontmatter(masters[0].read_text(encoding="utf-8-sig"))
    assert fm.get("status") == "queued"
