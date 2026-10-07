"""Pin that the pre-dispatch ship runs the last step's gate first.

Regression for 07l #0: the pre-dispatch block (f67ad8cf) shipped without
running any gate, so a red last-step gate would be silently shipped.

  AC-1: Red last gate with current_step == total → no #ship marker,
        current_step == total-1.
  AC-2: Green last gate → shipped with #ship marker.
  AC-3: Last step declares no checks → shipped (same as gates-off).
  AC-4: Gate history records the pre-dispatch gate run.
  AC-5: current_step is rolled back to last step index on red gate.
"""
from __future__ import annotations

import json
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


# Sub-plan with a last step that has local_checks (gate commands).
SUBPLAN_WITH_GATE = """\
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

```yaml
local_checks:
  - command: "exit 1"
    timeout: 30
```
"""

# Sub-plan with a last step that has a green gate (passes).
SUBPLAN_WITH_GREEN_GATE = """\
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

```yaml
local_checks:
  - command: "exit 0"
    timeout: 30
```
"""

# Sub-plan with a last step that has NO local_checks.
SUBPLAN_NO_GATE = """\
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
    template: str = SUBPLAN_WITH_GATE,
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
        template.format(slug=slug, status=status, step=step, est=est),
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
        f"function does not exist or crashed "
        f"(rc={proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-1: red last gate → no ship, current_step rolled back ─────────────────


def test_red_last_gate_no_ship_rollback_step(tmp_path: Path) -> None:
    """Red last gate with current_step == total → no #ship marker,
    current_step == total-1."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="red-gate", status="in-progress", step=2, est=2,
    )
    before_sha = _git(repo, "rev-parse", "HEAD")
    proc = _run_fn_in_subprocess(
        tmp_path,
        'pre_dispatch_ship_if_complete "red-gate"',
        plans,
        repo,
        data_home,
    )
    # The function returns 1 when the gate fails (red gate).
    # This is expected behavior - the function should not crash.
    assert proc.returncode in (0, 1), (
        f"function crashed (rc={proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    # No #ship marker commit should have been created.
    after_sha = _git(repo, "rev-parse", "HEAD")
    assert before_sha == after_sha, (
        f"expected no commit after red gate, but HEAD changed.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    # current_step should be rolled back to total-1 (last step index).
    step = _read_frontmatter_field(plans, "red-gate", "current_step")
    assert step == "1", (
        f"expected current_step=1 (rolled back), got {step}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    # Status should remain in-progress.
    status = _read_frontmatter_field(plans, "red-gate", "status")
    assert status == "in-progress", (
        f"expected in-progress, got {status}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-2: green last gate → shipped ─────────────────────────────────────────


def test_green_last_gate_ships(tmp_path: Path) -> None:
    """Green last gate → shipped with #ship marker."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="green-gate", status="in-progress", step=2, est=2,
        template=SUBPLAN_WITH_GREEN_GATE,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'pre_dispatch_ship_if_complete "green-gate"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert "shipped" in proc.stdout.lower(), (
        f"expected shipped log line.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert _read_frontmatter_field(plans, "green-gate", "status") == "shipped"
    # Should have a #ship marker commit.
    log = _git(repo, "log", "--oneline", "-1")
    assert "green-gate" in log and "#ship" in log, (
        f"expected #ship marker commit.\nlog: {log}"
    )


# ── AC-3: no checks → shipped (gates-off path) ──────────────────────────────


def test_no_checks_ships(tmp_path: Path) -> None:
    """Last step declares no checks → shipped (same as gates-off)."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="no-checks", status="in-progress", step=2, est=2,
        template=SUBPLAN_NO_GATE,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'pre_dispatch_ship_if_complete "no-checks"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert "shipped" in proc.stdout.lower(), (
        f"expected shipped log line.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert _read_frontmatter_field(plans, "no-checks", "status") == "shipped"


# ── AC-4: gate history records the pre-dispatch gate run ─────────────────────


def test_gate_history_records_pre_dispatch_run(tmp_path: Path) -> None:
    """Gate history records the pre-dispatch gate run (same row as any gate)."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="hist-gate", status="in-progress", step=2, est=2,
        template=SUBPLAN_WITH_GREEN_GATE,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'pre_dispatch_ship_if_complete "hist-gate"',
        plans,
        repo,
        data_home,
        extra_env={"RUN_ID": "test-run"},
    )
    _assert_fn_ran(proc)
    runtime = data_home / "projects" / ilk_paths.project_key(repo) / "runtime"
    gate_history = runtime / "launcher" / "gate-history.jsonl"
    assert gate_history.is_file(), (
        f"no gate-history.jsonl at {gate_history}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    rows = [json.loads(l) for l in gate_history.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(
        r.get("slug") == "hist-gate" and r.get("step") == 1 and r.get("outcome") == "pass"
        for r in rows
    ), f"no pass row for hist-gate step 1: {rows}"


# ── AC-5: current_step rolled back on red gate ──────────────────────────────


def test_current_step_rollback_on_red_gate(tmp_path: Path) -> None:
    """current_step is rolled back to last step index on red gate."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="rollback", status="in-progress", step=2, est=2,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'pre_dispatch_ship_if_complete "rollback"',
        plans,
        repo,
        data_home,
    )
    # The function returns 1 when the gate fails (red gate).
    assert proc.returncode in (0, 1), (
        f"function crashed (rc={proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    step = _read_frontmatter_field(plans, "rollback", "current_step")
    assert step == "1", (
        f"expected current_step=1 (rolled back to last step), got {step}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── control: existing driver_ship_if_complete still works ────────────────────


def test_driver_ship_if_complete_green_gate(tmp_path: Path) -> None:
    """Control: driver_ship_if_complete still ships on green gate."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="ctrl-green", status="in-progress", step=2, est=2,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "ctrl-green" "a green gate"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert _read_frontmatter_field(plans, "ctrl-green", "status") == "shipped"


def test_driver_ship_if_complete_ships_pending_when_steps_done(tmp_path: Path) -> None:
    """Control: a pending sub-plan with every step done ships (f67ad8cf; golden
    ships golden-red from pending)."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="ctrl-pend", status="pending", step=2, est=2,
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'driver_ship_if_complete "ctrl-pend" "a green gate"',
        plans,
        repo,
        data_home,
    )
    _assert_fn_ran(proc)
    assert _read_frontmatter_field(plans, "ctrl-pend", "status") == "shipped"


# ── fail-closed pins (owner review of e950ab3e, 2026-10-08) ──────────────────

SUBPLAN_RED_THEN_GREEN = SUBPLAN_WITH_GATE.replace(
    '''  - command: "exit 1"
    timeout: 30
''',
    '''  - command: "false"
    timeout: 30
  - command: "true"
    timeout: 30
''',
)

SUBPLAN_UNPARSEABLE_GATE = SUBPLAN_WITH_GATE.replace(
    '''  - command: "exit 1"
    timeout: 30
''',
    '''  - timeout: 30
''',
)


def test_a_red_check_before_a_green_one_is_red(tmp_path: Path) -> None:
    """Every declared check counts, not the last line's exit code."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="red-green", status="in-progress", step=2, est=2,
        template=SUBPLAN_RED_THEN_GREEN,
    )
    before_sha = _git(repo, "rev-parse", "HEAD")
    proc = _run_fn_in_subprocess(
        tmp_path, 'pre_dispatch_ship_if_complete "red-green"', plans, repo, data_home,
    )
    assert proc.returncode == 1, f"rc={proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert _git(repo, "rev-parse", "HEAD") == before_sha
    assert _read_frontmatter_field(plans, "red-green", "status") == "in-progress"
    assert _read_frontmatter_field(plans, "red-green", "current_step") == "1"


def test_a_declared_but_unextractable_gate_is_not_no_checks(tmp_path: Path) -> None:
    """local_checks declared but no command extracted → inconclusive, never shipped."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="bad-gate", status="in-progress", step=2, est=2,
        template=SUBPLAN_UNPARSEABLE_GATE,
    )
    before_sha = _git(repo, "rev-parse", "HEAD")
    proc = _run_fn_in_subprocess(
        tmp_path, 'pre_dispatch_ship_if_complete "bad-gate"', plans, repo, data_home,
    )
    assert proc.returncode == 1, f"rc={proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert _git(repo, "rev-parse", "HEAD") == before_sha
    assert _read_frontmatter_field(plans, "bad-gate", "status") == "in-progress"


def test_a_declined_ship_is_not_green(tmp_path: Path) -> None:
    """A green gate on a sub-plan the driver declines (an unshipped
    batch_verification sub-plan) returns 2, not 0, so the main loop dispatches
    instead of marking the iteration green."""
    repo = _make_repo(tmp_path)
    data_home, plans = _make_plans_dir(
        tmp_path, repo, slug="declined", status="in-progress", step=2, est=2,
        template=SUBPLAN_WITH_GREEN_GATE.replace(
            "local_checks: []\n---", "local_checks: []\nbatch_verification: true\n---", 1,
        ),
    )
    proc = _run_fn_in_subprocess(
        tmp_path,
        'rc=0; pre_dispatch_ship_if_complete "declined" || rc=$?; echo "RC=$rc"',
        plans, repo, data_home,
    )
    _assert_fn_ran(proc)
    assert "RC=2" in proc.stdout, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert _read_frontmatter_field(plans, "declined", "status") == "in-progress"


def test_the_main_loop_calls_the_tested_function() -> None:
    """The live pre-dispatch block is the tested function, not an inline copy."""
    text = _SCRIPT.read_text(encoding="utf-8")
    start = text.index("# -- Pre-dispatch: ship a complete sub-plan only on a green last gate")
    end = text.index("# -- One-ship marker: clear stale, export fresh", start)
    block = text[start:end]
    assert 'pre_dispatch_ship_if_complete "$_iter_slug"' in block
    assert "--print-step-gate" not in block
    assert "driver_ship_if_complete" not in block
