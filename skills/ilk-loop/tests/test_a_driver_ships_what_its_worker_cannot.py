"""Pin that the driver ships a completed sub-plan after a green gate.

Regression for 07l #0: the runner refused worker sessions but never added
the driver-side ship, so every regular sub-plan stayed in-progress forever.

  AC-1: After a green post-iteration gate and current_step == estimated_steps,
        the driver calls ship_transition.py --ship <slug> without
        ILK_WORKER_SESSION.  The sub-plan becomes shipped with a #ship marker.
  AC-2: The function only ships when status is in-progress.
  AC-3: The function only ships when current_step == estimated_steps.
  AC-4: The function unsets ILK_ITERATION_SUBPLAN and ILK_WORKER_SESSION for
        the ship call.
  AC-5: The function appends the slug to _GATE_FIRST_SHIPPED_SLUGS.
  AC-6: On ship_transition failure, the function logs the output and leaves
        the sub-plan as-is (no crash, no status change).
  AC-7: The post-iteration call site invokes driver_ship_if_complete only when
        the gate is green (blocking_checks --any is false).
  AC-8: When local checks are OFF (no gate ran), driver_ship_if_complete is
        called after the iteration.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_SCRIPT = _SCRIPTS / "run_ilk_loop_claude.sh"

import ilk_paths  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return cp.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


SUBPLAN_TEMPLATE = """\
---
plan: {slug}
status: {status}
current_step: {step}
tickets: []
priority: P1
estimated_steps: {est}
last_updated: 2026-10-08
verification_tier: loop-verified
local_checks: []
---

# Sub-plan: {slug}

## Steps

### Step 0 — first step

Body.

### Step 1 — last step

Body.
"""


class _scoped_data_home:
    """Pin ILK_DATA_HOME for a block."""

    def __init__(self, data_home: Path) -> None:
        self._data_home = data_home
        self._prev: str | None = None

    def __enter__(self) -> Path:
        self._prev = os.environ.get("ILK_DATA_HOME")
        os.environ["ILK_DATA_HOME"] = str(self._data_home)
        return self._data_home

    def __exit__(self, *exc: object) -> None:
        if self._prev is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = self._prev


def _make_plans_dir(
    tmp_path: Path,
    repo: Path,
    slug: str = "my-slug",
    status: str = "in-progress",
    step: int = 2,
    est: int = 2,
) -> tuple[Path, Path]:
    """Create plans dir under the proper ILK_DATA_HOME structure.

    Returns (data_home, plans_dir).
    """
    data_home = tmp_path / ".ilk-data"
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(repo)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)
    # MASTER file is required for ilk_paths.find_plans_dir to resolve.
    (plans / "MASTER-2026-10-07n-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-10-07n-execution\n"
        "batch_date: 2026-10-07\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [2026-10-07n-{slug}](./2026-10-07n-{slug}.md) |\n",
        encoding="utf-8",
    )
    fname = f"2026-10-07n-{slug}.md"
    (plans / fname).write_text(
        SUBPLAN_TEMPLATE.format(slug=slug, status=status, step=step, est=est),
        encoding="utf-8",
    )
    return data_home, plans


def _read_frontmatter_field(plans_dir: Path, slug: str, field: str) -> str:
    """Read a YAML frontmatter field from a sub-plan file."""
    for p in plans_dir.glob("*.md"):
        if p.name.startswith("MASTER"):
            continue
        text = p.read_text(encoding="utf-8")
        fm: dict[str, str] = {}
        in_fm = False
        for line in text.splitlines():
            if line.strip() == "---":
                if in_fm:
                    break
                in_fm = True
                continue
            if in_fm:
                m = re.match(r"^([\w_-]+):\s*(.*)", line)
                if m:
                    fm[m.group(1)] = m.group(2).strip()
        if fm.get("plan") == slug:
            return fm.get(field, "")
    return ""


def _run_fn_in_subprocess(
    tmp_path: Path,
    fn_body: str,
    plans_dir: Path,
    repo: Path,
    data_home: Path,
    extra_env: dict[str, str] | None = None,
    extra_code: str = "",
) -> subprocess.CompletedProcess:
    """Source the runner and execute arbitrary code in its context."""
    env_vars = ""
    if extra_env:
        for k, v in extra_env.items():
            env_vars += f"export {k}={v!r}\n"
    script = f"""
set -e
export ILK_DOTSOURCE_ONLY=1
source "{_SCRIPT}" || exit 90
unset ILK_DOTSOURCE_ONLY
export PROJECT_PATH="{repo}"
{env_vars}
{extra_code}
{fn_body}
"""
    env = {
        **os.environ,
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(_SCRIPTS.parent.parent),
    }
    env.pop("ILK_ITERATION_SUBPLAN", None)
    env.pop("ILK_WORKER_SESSION", None)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
        cwd=str(tmp_path),
    )


def _assert_fn_ran(proc: subprocess.CompletedProcess) -> None:
    """Assert the subprocess exited 0 — the function exists and ran."""
    assert proc.returncode == 0, (
        f"driver_ship_if_complete does not exist or crashed "
        f"(rc={proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-1: green gate + last step done → driver ships ────────────────────────


def test_driver_ships_after_green_gate_last_step(tmp_path: Path) -> None:
    """After a green gate and current_step == estimated_steps, the driver
    calls ship_transition.py --ship without ILK_WORKER_SESSION."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="green-slug", status="in-progress", step=2,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "green-slug" "a green gate"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert "shipped" in proc.stdout.lower(), (
        f"expected shipped log line.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert _read_frontmatter_field(plans, "green-slug", "status") == "shipped"


def test_driver_ship_creates_marker_commit(tmp_path: Path) -> None:
    """A successful driver ship creates a #ship marker commit."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="marker-slug", status="in-progress", step=2,
    )
    before_sha = _git(repo, "rev-parse", "HEAD")
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "marker-slug" "a green gate"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    log = _git(repo, "log", "--oneline", "-1")
    assert "marker-slug" in log and "#ship" in log, (
        f"expected #ship marker commit.\nlog: {log}"
    )
    after_sha = _git(repo, "rev-parse", "HEAD")
    assert before_sha != after_sha, "HEAD did not advance after ship"


# ── AC-2: only ships when status is in-progress ─────────────────────────────


def test_driver_does_not_ship_pending_subplan(tmp_path: Path) -> None:
    """A pending sub-plan must not be shipped. The function must run and skip."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="pend-slug", status="pending", step=2,
    )
    before_sha = _git(repo, "rev-parse", "HEAD")
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "pend-slug" "a green gate"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert _read_frontmatter_field(plans, "pend-slug", "status") != "shipped"
    assert _git(repo, "rev-parse", "HEAD") == before_sha


def test_driver_does_not_ship_already_shipped_subplan(tmp_path: Path) -> None:
    """An already-shipped sub-plan must not be shipped again."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="done-slug", status="shipped", step=2,
    )
    before_sha = _git(repo, "rev-parse", "HEAD")
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "done-slug" "a green gate"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert _git(repo, "rev-parse", "HEAD") == before_sha, (
        "a commit was created for an already-shipped sub-plan"
    )


# ── AC-3: only ships when current_step == estimated_steps ───────────────────


def test_driver_does_not_ship_when_step_not_complete(tmp_path: Path) -> None:
    """current_step < estimated_steps must not trigger a ship."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="half-slug", status="in-progress", step=1, est=2,
    )
    before_sha = _git(repo, "rev-parse", "HEAD")
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "half-slug" "a green gate"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert _read_frontmatter_field(plans, "half-slug", "status") != "shipped"
    assert _git(repo, "rev-parse", "HEAD") == before_sha


# ── AC-4: unsets ILK_ITERATION_SUBPLAN and ILK_WORKER_SESSION ────────────────


def test_driver_ship_refuses_in_worker_session(tmp_path: Path) -> None:
    """The driver must NOT inherit ILK_WORKER_SESSION from the iteration."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="ws-slug", status="in-progress", step=2,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "ws-slug" "a green gate"',
        plans,
        repo,
        data_home,
        extra_env={"ILK_WORKER_SESSION": "1", "ILK_ITERATION_SUBPLAN": "ws-slug"},
    )
    _assert_fn_ran(proc)
    status = _read_frontmatter_field(plans, "ws-slug", "status")
    assert status == "shipped", (
        f"expected shipped (env was cleared), got {status}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_driver_ship_clears_iteration_env_for_ship_call(tmp_path: Path) -> None:
    """Verify ILK_ITERATION_SUBPLAN is unset in the ship call environment."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="env-slug", status="in-progress", step=2,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "env-slug" "a green gate"',
        plans,
        repo,
        data_home,
        extra_env={"ILK_WORKER_SESSION": "1", "ILK_ITERATION_SUBPLAN": "env-slug"},
    )
    _assert_fn_ran(proc)
    status = _read_frontmatter_field(plans, "env-slug", "status")
    assert status == "shipped", (
        f"expected shipped (env was cleared), got {status}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-5: appends slug to _GATE_FIRST_SHIPPED_SLUGS ─────────────────────────


def test_driver_ship_tracks_slug_in_gate_first_shipped(tmp_path: Path) -> None:
    """After shipping, the slug is appended to _GATE_FIRST_SHIPPED_SLUGS."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="track-slug", status="in-progress", step=2,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "track-slug" "a green gate"\n'
        'echo "TRACKED=$_GATE_FIRST_SHIPPED_SLUGS"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert "TRACKED=track-slug" in proc.stdout, (
        f"expected TRACKED=track-slug in output.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-6: ship_transition failure is handled gracefully ──────────────────────


def test_driver_ship_handles_transition_failure(tmp_path: Path) -> None:
    """On ship_transition failure, the sub-plan stays as-is and no crash."""
    repo = _make_repo(tmp_path)
    data_home = tmp_path / ".ilk-data"
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(repo)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)
    # MASTER file required for ilk_paths.find_plans_dir.
    (plans / "MASTER-2026-10-07n-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-10-07n-execution\n"
        "batch_date: 2026-10-07\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        "| # | Slug |\n|---|---|\n| 1 | [mismatch](./2026-10-07n-mismatch.md) |\n",
        encoding="utf-8",
    )
    # Mismatched plan: field vs the slug we pass — ship_transition will fail.
    (plans / "2026-10-07n-mismatch.md").write_text(
        SUBPLAN_TEMPLATE.format(slug="real-slug", status="in-progress", step=2, est=2),
        encoding="utf-8",
    )
    before_sha = _git(repo, "rev-parse", "HEAD")
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "nonexistent-slug" "a green gate"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert _git(repo, "rev-parse", "HEAD") == before_sha, (
        "a commit was created despite ship_transition failure"
    )


# ── AC-7: post-iteration call site: only on green gate ───────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="post-iteration driver ship call site does not exist yet",
)
def test_post_iteration_ships_on_green_gate(tmp_path: Path) -> None:
    """Integration: a full iteration with a green gate ships the sub-plan."""
    pytest.skip("integration — deferred to step 1 verification")


# ── AC-8: gates-off path ships after iteration ──────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="gates-off driver ship call site does not exist yet",
)
def test_gates_off_path_ships_after_iteration(tmp_path: Path) -> None:
    """Integration: when local checks are OFF, the driver still ships."""
    pytest.skip("integration — deferred to step 1 verification")


# ── control: existing behavior still works ───────────────────────────────────


def test_ship_refuses_in_worker_session_for_regular_subplan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: ship() still refuses when ILK_WORKER_SESSION=1 (07l #0)."""
    import ship_transition  # noqa: E402

    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="ctrl-slug", status="in-progress", step=2,
    )
    monkeypatch.setenv("ILK_WORKER_SESSION", "1")
    monkeypatch.setenv("ILK_ITERATION_SUBPLAN", "ctrl-slug")
    with pytest.raises(ship_transition.ShipTransitionError, match="refused.*driver"):
        ship_transition.ship(plans, repo, "ctrl-slug")