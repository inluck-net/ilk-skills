"""Guards on verify-dispatch: own-checkout launcher + busy-project skip.

Two defects measured 2026-09-20 on chad-mbp:

1. The skill-root fallback list put ~/.codex/skills first, so the
   launchd-run repo scheduler dispatched verification sessions through
   a months-old codex install — stale ``running`` sentinels across six
   projects surfaced as permanent blocked rows on the status panel.
2. ``_dispatch_verification_on_drain`` Popen'd without checking the
   per-project sentinel: a busy project received extra engines
   (kira: five launches inside five seconds, 10:10:35-40).

Hermetic — mirrors test_dispatch_on_drain.py's fixture style.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts"


def _import_scan():
    sys.path.insert(0, str(SCRIPTS_ILK_WATCHDOG))
    sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
    for mod_name in ("scheduler_scan", "ilk_paths", "plan_status", "blacklist_status"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    import scheduler_scan
    return scheduler_scan


def _setup_project(tmp_path: Path, *, sentinel_pid: int | None) -> Path:
    """Scaffold a drain-ready project: shipped master+subplan, repo path,
    and optionally a terminal-state sentinel."""
    project_dir = tmp_path / "projects" / "test-proj"
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "MASTER-test.md").write_text(
        "---\n"
        "title: MASTER-test.md\n"
        "created: 2026-07-29T00:00:00+08:00\n"
        "status: active\n"
        "priority: 0\n"
        "---\n"
        "\n# MASTER-test.md\n"
        "\n## Sub-plan registry\n"
        "\n| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-07-29-work.md](./2026-07-29-work.md) | shipped |\n",
        encoding="utf-8",
    )
    (plans_dir / "2026-07-29-work.md").write_text(
        "---\n"
        "plan: 2026-07-29-work\n"
        "status: shipped\n"
        "current_step: 3\n"
        "estimated_steps: 3\n"
        "---\n"
        "\n# work\n",
        encoding="utf-8",
    )
    launcher_dir = project_dir / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir / "last-launch.json").write_text(
        json.dumps({"project_path": "/some/repo"}), encoding="utf-8",
    )
    if sentinel_pid is not None:
        (launcher_dir / "last-exit.json").write_text(
            json.dumps({"state": "running", "pid": sentinel_pid}),
            encoding="utf-8",
        )
    return project_dir


def test_own_checkout_wins_for_skill_root(monkeypatch):
    scan = _import_scan()
    here_skills = Path(scan.__file__).resolve().parent.parent.parent
    assert scan._SKILL_ROOT_CANDIDATES[0] == here_skills
    monkeypatch.delenv("ILK_SKILL_HOME", raising=False)
    assert scan._resolve_skill_root() == here_skills
    # The winning root must actually be a skills dir (ilk-watchdog lives in it).
    assert (here_skills / "ilk-watchdog").is_dir()


def test_busy_project_is_not_dispatched(tmp_path):
    scan = _import_scan()
    project_dir = _setup_project(tmp_path, sentinel_pid=os.getpid())
    launched = []
    scan._dispatch_verification_on_drain(
        project_dir,
        project_dir / "plans" / "MASTER-test.md",
        project_dir / "plans",
        _launch_fn=launched.append,
    )
    assert launched == []  # live run holds the project: no second engine
    assert not (project_dir / "runtime" / "verification-dispatched.json").exists()


def test_stale_running_sentinel_still_dispatches(tmp_path):
    scan = _import_scan()
    # pid 999999 does not exist -> the running sentinel is stale, and a
    # verification dispatch is exactly how the project gets unstuck.
    project_dir = _setup_project(tmp_path, sentinel_pid=999_999)
    launched = []
    scan._dispatch_verification_on_drain(
        project_dir,
        project_dir / "plans" / "MASTER-test.md",
        project_dir / "plans",
        _launch_fn=launched.append,
    )
    assert len(launched) == 1
