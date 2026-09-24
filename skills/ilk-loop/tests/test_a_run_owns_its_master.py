"""Red-first: a run owns its master — ILK_MASTER pin + per-slug park reason.

Reproduces two defects:
  1. The scheduler dispatches a PROJECT, not a master. The runner then
     rediscovers "the active master" via loop_status.py, and with several
     non-terminal masters in one plans dir, the choice falls to priority
     order — not necessarily the master the scheduler meant.
  2. The ship-integrity park reason is built from ALL violating slugs and
     stamped onto every owning master, so M1's reason names M2's slug.

AC-1  Two active masters A (higher priority) and B; ILK_MASTER=B ⇒
      loop_status.py --json reports B. Unset ⇒ A (today).
AC-2  ILK_MASTER=B, B all-shipped, A active ⇒ loop_status reports B as
      all-shipped, not A (no rollover).
AC-3  ILK_MASTER=MASTER-missing.md ⇒ today's pick plus a notice naming
      the missing file.
AC-4  launch.sh --dry-run --master X … output shows ILK_MASTER=X.
AC-5  The scheduler's --dry-run --once dispatch JSON for a project with
      one active master contains "master": "<that basename>", and its
      command contains --master.
AC-6  Violations on slugs s1 (owned by M1) and s2 (owned by M2) in one
      iteration ⇒ M1's parked_reason names s1 and not s2; M2's names s2
      and not s1.

New-behaviour ACs get @pytest.mark.xfail(strict=True, reason="red-first").
The "unset ⇒ today" halves are unmarked.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_ILK_LOOP = _REPO / "skills" / "ilk-loop" / "scripts"
SCRIPTS_ILK_WATCHDOG = _REPO / "skills" / "ilk-watchdog" / "scripts"
SCRIPTS_ILK_LAUNCHER = _REPO / "skills" / "ilk-launcher" / "scripts"
LOOP_STATUS = SCRIPTS_ILK_LOOP / "loop_status.py"
LAUNCH_SH = SCRIPTS_ILK_LAUNCHER / "launch.sh"
SCHEDULER_SH = SCRIPTS_ILK_WATCHDOG / "scheduler.sh"
RUNNER = SCRIPTS_ILK_LOOP / "run_ilk_loop_claude.sh"

sys.path.insert(0, str(SCRIPTS_ILK_LOOP))
import ilk_paths  # noqa: E402
from plan_status import parse_frontmatter  # noqa: E402

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_master(
    plans_dir: Path,
    name: str,
    *,
    status: str = "active",
    priority: int = 0,
    created: str = "2026-09-24T00:00:00+08:00",
    subplans: list[str] | None = None,
) -> None:
    """Write a minimal MASTER-*.md."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"title: {name.replace('.md', '')}",
        f"created: {created}",
        f"status: {status}",
        f"priority: {priority}",
        "pause_after_ship: false",
        "---",
        "",
        f"# {name}",
        "",
    ]
    if subplans:
        lines += [
            "## Sub-plan registry",
            "",
            "| # | Sub-plan | Status |",
            "|---|---|---|",
        ]
        for sp in subplans:
            lines.append(f"| 1 | [{sp}](./{sp}) | pending |")
        lines.append("")
    (plans_dir / name).write_text("\n".join(lines), encoding="utf-8")


def _write_subplan(
    plans_dir: Path,
    name: str,
    *,
    status: str = "pending",
    current_step: int = 0,
    estimated_steps: int = 3,
) -> None:
    """Write a minimal sub-plan."""
    plans_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"plan: {name.replace('.md', '')}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {estimated_steps}\n"
        f"last_updated: 2026-09-24\n"
        "---\n"
        f"\n# {name}\n"
    )
    (plans_dir / name).write_text(body, encoding="utf-8")


def _run_loop_status(
    cwd: Path,
    *,
    data_home: Path,
    env_extra: dict[str, str] | None = None,
) -> dict:
    """Run loop_status.py --json with isolated HOME/ILK_DATA_HOME/ILK_SKILL_HOME."""
    env = {
        "HOME": str(cwd),
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": "/usr/bin:/bin",
    }
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, str(LOOP_STATUS), "--json"],
        capture_output=True, text=True, timeout=30,
        env=env, encoding="utf-8", errors="replace",
        cwd=str(cwd),
    )
    # --json exits 1 when there is pending work (and 2 on error) — both
    # still print valid JSON to stdout.
    assert result.returncode in (0, 1), (
        f"exit {result.returncode}: {result.stderr}"
    )
    return json.loads(result.stdout)


# ── AC-1: ILK_MASTER pins the run's master ───────────────────────────────────


@pytest.fixture
def two_active_masters(tmp_path: Path) -> dict:
    """Two active masters: A (priority 10, older) and B (priority 5, newer).

    Without ILK_MASTER, A wins (higher priority).
    Returns dict with 'project' and 'data_home' keys.
    """
    project = tmp_path / "project"
    project.mkdir()
    # Init a git repo so ilk_paths.find_project_root() finds it.
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "init", "-b", "main", str(project)],
        check=True, capture_output=True, text=True,
    )
    data_home = tmp_path / ".ilk-data"
    with patch.dict(
        os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False
    ):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    _write_master(
        plans, "MASTER-A.md",
        status="active", priority=10,
        created="2026-09-24T10:00:00+08:00",
        subplans=["2026-09-24-alpha.md"],
    )
    _write_subplan(plans, "2026-09-24-alpha.md", status="pending")
    _write_master(
        plans, "MASTER-B.md",
        status="active", priority=5,
        created="2026-09-24T11:00:00+08:00",
        subplans=["2026-09-24-beta.md"],
    )
    _write_subplan(plans, "2026-09-24-beta.md", status="pending")
    return {"project": project, "data_home": data_home}


def test_unset_picks_highest_priority(two_active_masters: dict) -> None:
    """Without ILK_MASTER, pick_active_master selects A (higher priority)."""
    data = _run_loop_status(
        two_active_masters["project"],
        data_home=two_active_masters["data_home"],
    )
    assert data["master"] == "MASTER-A.md", (
        f"without ILK_MASTER expected A, got {data['master']}"
    )


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ilk_master_pins_to_lower_priority(
    two_active_masters: dict,
) -> None:
    """AC-1: ILK_MASTER=B ⇒ loop_status reports B, not A."""
    data = _run_loop_status(
        two_active_masters["project"],
        data_home=two_active_masters["data_home"],
        env_extra={"ILK_MASTER": "MASTER-B.md"},
    )
    assert data["master"] == "MASTER-B.md", (
        f"ILK_MASTER=B expected B, got {data['master']}"
    )


# ── AC-2: pinned master all-shipped ⇒ no rollover ────────────────────────────


@pytest.fixture
def pinned_shipped_other_active(tmp_path: Path) -> dict:
    """B (pinned) is all-shipped; A is active.

    Without the pin, today's behaviour picks A. With the pin, B is reported
    as all-shipped (no rollover to A).
    Returns dict with 'project' and 'data_home' keys.
    """
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "init", "-b", "main", str(project)],
        check=True, capture_output=True, text=True,
    )
    data_home = tmp_path / ".ilk-data"
    with patch.dict(
        os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False
    ):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    _write_master(
        plans, "MASTER-A.md",
        status="active", priority=10,
        subplans=["2026-09-24-alpha.md"],
    )
    _write_subplan(plans, "2026-09-24-alpha.md", status="pending")
    _write_master(
        plans, "MASTER-B.md",
        status="active", priority=5,
        subplans=["2026-09-24-beta.md"],
    )
    _write_subplan(plans, "2026-09-24-beta.md", status="shipped",
                   current_step=3, estimated_steps=3)
    return {"project": project, "data_home": data_home}


@pytest.mark.xfail(strict=True, reason="red-first")
def test_pinned_all_shipped_no_rollover(
    pinned_shipped_other_active: dict,
) -> None:
    """AC-2: ILK_MASTER=B, B all-shipped ⇒ reports B as all-shipped, not A."""
    data = _run_loop_status(
        pinned_shipped_other_active["project"],
        data_home=pinned_shipped_other_active["data_home"],
        env_extra={"ILK_MASTER": "MASTER-B.md"},
    )
    # The master field should still be B, and the exit should be 0 (all shipped).
    assert data["master"] == "MASTER-B.md", (
        f"expected B, got {data['master']}"
    )
    assert data["queue_exit"] == 0, (
        f"expected all-shipped (exit 0), got {data['queue_exit']}"
    )


# ── AC-3: missing pinned master ⇒ today's pick + notice ──────────────────────


@pytest.fixture
def one_active_master(tmp_path: Path) -> dict:
    """One active master A. Returns dict with 'project' and 'data_home' keys."""
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "init", "-b", "main", str(project)],
        check=True, capture_output=True, text=True,
    )
    data_home = tmp_path / ".ilk-data"
    with patch.dict(
        os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False
    ):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    _write_master(
        plans, "MASTER-A.md",
        status="active", priority=10,
        subplans=["2026-09-24-alpha.md"],
    )
    _write_subplan(plans, "2026-09-24-alpha.md", status="pending")
    return {"project": project, "data_home": data_home}


@pytest.mark.xfail(strict=True, reason="red-first")
def test_missing_pinned_master_falls_back_with_notice(
    one_active_master: dict,
) -> None:
    """AC-3: ILK_MASTER=MASTER-missing.md ⇒ today's pick (A) + notice."""
    data = _run_loop_status(
        one_active_master["project"],
        data_home=one_active_master["data_home"],
        env_extra={"ILK_MASTER": "MASTER-missing.md"},
    )
    assert data["master"] == "MASTER-A.md", (
        f"expected fallback to A, got {data['master']}"
    )
    notices = data.get("notices", [])
    assert any("MASTER-missing.md" in n for n in notices), (
        f"expected a notice naming the missing file; notices={notices}"
    )


# ── AC-4: launch.sh --dry-run --master X exports ILK_MASTER=X ────────────────


@pytest.fixture
def project_with_master(tmp_path: Path) -> Path:
    """A project with one active master for launch.sh testing."""
    project = tmp_path / "project"
    project.mkdir()
    plans = tmp_path / ".ilk-data" / "projects" / "test-proj" / "plans"
    _write_master(
        plans, "MASTER-A.md",
        status="active", priority=10,
        subplans=["2026-09-24-alpha.md"],
    )
    _write_subplan(plans, "2026-09-24-alpha.md", status="pending")
    return project


@pytest.mark.xfail(strict=True, reason="red-first")
def test_launch_dry_run_master_exports_ilk_master(
    project_with_master: Path,
    tmp_path: Path,
) -> None:
    """AC-4: launch.sh --dry-run --master X output shows ILK_MASTER=X."""
    env = {
        "HOME": str(tmp_path),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "ILK_DATA_HOME": str(tmp_path / ".ilk-data"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    result = subprocess.run(
        ["bash", str(LAUNCH_SH),
         "--project-path", str(project_with_master),
         "--master", "MASTER-A.md",
         "--dry-run"],
        capture_output=True, text=True, timeout=30,
        env=env, encoding="utf-8", errors="replace",
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, (
        f"exit {result.returncode}: {result.stderr}"
    )
    output = result.stdout + result.stderr
    assert "ILK_MASTER=MASTER-A.md" in output, (
        f"expected ILK_MASTER=MASTER-A.md in output:\n{output}"
    )


# ── AC-5: scheduler --dry-run --once dispatch JSON includes master ────────────


@pytest.fixture
def scheduler_project(tmp_path: Path) -> dict:
    """A project data dir with one active master for scheduler testing."""
    data_home = tmp_path / ".ilk-data"
    key = "test-proj"
    plans = data_home / "projects" / key / "plans"
    _write_master(
        plans, "MASTER-A.md",
        status="active", priority=10,
        subplans=["2026-09-24-alpha.md"],
    )
    _write_subplan(plans, "2026-09-24-alpha.md", status="pending")
    project = tmp_path / "project"
    project.mkdir()
    return {"project": project, "plans": plans, "data_home": data_home, "key": key}


@pytest.mark.xfail(strict=True, reason="red-first")
def test_scheduler_dry_run_once_dispatch_includes_master(
    scheduler_project: dict,
    tmp_path: Path,
) -> None:
    """AC-5: scheduler --dry-run --once JSON dispatch includes "master" and
    the command includes --master."""
    env = {
        "HOME": str(tmp_path),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "ILK_DATA_HOME": str(scheduler_project["data_home"]),
        "PATH": f"{SCRIPTS_ILK_LAUNCHER}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        ["bash", str(SCHEDULER_SH),
         "--project-path", str(scheduler_project["project"]),
         "--dry-run", "--once"],
        capture_output=True, text=True, timeout=60,
        env=env, encoding="utf-8", errors="replace",
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, (
        f"exit {result.returncode}: {result.stderr}"
    )
    # Parse the last JSON line (the dispatch decision).
    lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    dispatch = None
    for line in lines:
        try:
            obj = json.loads(line)
            if obj.get("decision") == "dispatch":
                dispatch = obj
                break
        except json.JSONDecodeError:
            continue
    assert dispatch is not None, (
        f"no dispatch JSON in output:\n{result.stdout}"
    )
    assert "master" in dispatch, (
        f"dispatch JSON missing 'master' key: {dispatch}"
    )
    assert "MASTER-A.md" in dispatch["master"], (
        f"expected MASTER-A.md in master field, got {dispatch['master']}"
    )
    assert "--master" in dispatch.get("command", ""), (
        f"dispatch command missing --master: {dispatch.get('command')}"
    )


# ── AC-6: per-slug park reason ───────────────────────────────────────────────


@pytest.fixture
def two_masters_two_slugs(tmp_path: Path) -> dict:
    """Two masters: M1 owns slug s1, M2 owns slug s2.

    Both sub-plans are shipped with a gate that will fail (exit 1).
    """
    project = tmp_path / "project"
    project.mkdir()
    import ilk_paths
    with patch.dict(
        os.environ, {"ILK_DATA_HOME": str(tmp_path / ".ilk-data")}, clear=False
    ):
        key = ilk_paths.project_key(project)
    plans = tmp_path / ".ilk-data" / "projects" / key / "plans"
    plans.mkdir(parents=True)

    slug1 = "slug-one"
    slug2 = "slug-two"
    stem1 = f"2026-09-24-{slug1}"
    stem2 = f"2026-09-24-{slug2}"

    # M1 — owns s1
    (plans / "MASTER-M1.md").write_text(
        "---\ntitle: M1\nstatus: active\nsupervised_only: false\n---\n\n"
        "# M1\n\n## Sub-plan registry\n\n"
        f"| # | file |\n|---|---|\n| 1 | [{stem1}](./{stem1}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{stem1}.md").write_text(
        f"---\nplan: {slug1}\nstatus: shipped\ncurrent_step: 1\n"
        "estimated_steps: 1\n---\n\n# s1\n",
        encoding="utf-8",
    )

    # M2 — owns s2
    (plans / "MASTER-M2.md").write_text(
        "---\ntitle: M2\nstatus: queued\nsupervised_only: false\n---\n\n"
        "# M2\n\n## Sub-plan registry\n\n"
        f"| # | file |\n|---|---|\n| 1 | [{stem2}](./{stem2}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{stem2}.md").write_text(
        f"---\nplan: {slug2}\nstatus: shipped\ncurrent_step: 1\n"
        "estimated_steps: 1\n---\n\n# s2\n",
        encoding="utf-8",
    )

    return {"project": project, "plans": plans, "data_home": tmp_path / ".ilk-data",
            "key": key, "slug1": slug1, "slug2": slug2, "stem1": stem1, "stem2": stem2}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


@_NEEDS_GTIMEOUT
@pytest.mark.xfail(strict=True, reason="red-first")
def test_per_slug_park_reason(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-6: violations on s1 (M1) and s2 (M2) in one iteration ⇒ M1's
    parked_reason names s1 and not s2; M2's names s2 and not s1."""
    root = tmp_path_factory.mktemp("per-slug-reason")
    project = root / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    (project / "README.md").write_text("init\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    import ilk_paths
    with patch.dict(
        os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False
    ):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    slug1 = "slug-one"
    slug2 = "slug-two"
    stem1 = f"2026-09-24-{slug1}"
    stem2 = f"2026-09-24-{slug2}"

    # M1 — active, owns s1, gate will fail.
    (plans / "MASTER-M1.md").write_text(
        "---\ntitle: M1\nstatus: active\nsupervised_only: false\n---\n\n"
        "# M1\n\n## Sub-plan registry\n\n"
        f"| # | file |\n|---|---|\n| 1 | [{stem1}](./{stem1}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{stem1}.md").write_text(
        f"---\nplan: {slug1}\nstatus: in-progress\ncurrent_step: 0\n"
        "estimated_steps: 1\n---\n\n"
        f"# {slug1}\n\n### Step 0 — work\n\n"
        "```yaml\nlocal_checks:\n"
        "  - command: \"python3 -c 'raise SystemExit(1)'\"\n"
        "    timeout: 60\n```\n",
        encoding="utf-8",
    )

    # M2 — queued, owns s2, gate will fail.
    (plans / "MASTER-M2.md").write_text(
        "---\ntitle: M2\nstatus: queued\nsupervised_only: false\n---\n\n"
        "# M2\n\n## Sub-plan registry\n\n"
        f"| # | file |\n|---|---|\n| 1 | [{stem2}](./{stem2}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{stem2}.md").write_text(
        f"---\nplan: {slug2}\nstatus: shipped\ncurrent_step: 1\n"
        "estimated_steps: 1\n---\n\n# {slug2}\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"SP={str(plans / f'{stem1}.md')!r}\n"
        "python3 - \"$SP\" <<'EOP'\n"
        "import re, sys\nfrom pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
        "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
        "p.write_text(b)\nEOP\n"
        "git -c user.email=t@example.com -c user.name=t commit -q "
        f"--allow-empty -m 'feat: work [plan:{slug1}#step-0]'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(root),
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CLAUDE_CONFIG_DIR": str(root / ".claude"),
    }
    env.pop("ILK_DATA_DIR", None)
    (root / ".claude").mkdir(exist_ok=True)

    proc = subprocess.run(
        ["bash", str(RUNNER),
         "--project-path", str(project),
         "--max-iterations", "1",
         "--iteration-timeout-min", "2",
         "--run-local-checks"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env=env, cwd=str(root),
    )
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    # M1's reason names s1, not s2.
    fm_m1 = parse_frontmatter(
        (plans / "MASTER-M1.md").read_text(encoding="utf-8-sig"))
    assert fm_m1["status"] == "blocked", (
        f"M1 is not blocked: {fm_m1.get('status')!r}\n{tail}"
    )
    reason_m1 = fm_m1.get("parked_reason", "")
    assert slug1 in reason_m1, (
        f"M1's reason does not name {slug1!r}: {reason_m1!r}\n{tail}"
    )
    assert slug2 not in reason_m1, (
        f"M1's reason names foreign slug {slug2!r}: {reason_m1!r}\n{tail}"
    )

    # M2's reason names s2, not s1.
    fm_m2 = parse_frontmatter(
        (plans / "MASTER-M2.md").read_text(encoding="utf-8-sig"))
    assert fm_m2["status"] == "blocked", (
        f"M2 is not blocked: {fm_m2.get('status')!r}\n{tail}"
    )
    reason_m2 = fm_m2.get("parked_reason", "")
    assert slug2 in reason_m2, (
        f"M2's reason does not name {slug2!r}: {reason_m2!r}\n{tail}"
    )
    assert slug1 not in reason_m2, (
        f"M2's reason names foreign slug {slug1!r}: {reason_m2!r}\n{tail}"
    )
