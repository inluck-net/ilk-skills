"""RED-first pins — an ungated step is not proven.

Retro 2026-10-10 root cause 4 (rows a96455e3 + 2207b94c): a relaunch dropped
the run's gates, 7 of 7 iterations had ``local_checks=null``, yet the driver
wrote ship-proof rows for all 6 step transitions and 2 sub-plans shipped.

Design (binding — sub-plan ``an-ungated-step-is-not-proven``):

1. ``_subplan_declares_local_checks <file>`` — frontmatter ``local_checks``
   non-empty OR any per-step ``local_checks:`` block.
2. When ``RUN_LOCAL_CHECKS != true`` and the active sub-plan declares
   local_checks: ``driver_ship_if_complete`` is NOT called, neither is
   ``write_ship_proof_records`` / ``ledger_record_point``; the run prints
   ``[driver-ship] <slug>: refused, gates off (sub-plan declares
   local_checks)``, appends one Findings line, and ends with exit state
   ``gates-off``.
3. The watchdog treats ``gates-off`` as not relaunchable (same treatment as a
   stop/hold: no relaunch, no blacklist banner needed beyond the log line).
4. A sub-plan that declares NO local_checks keeps today's gates-off ship.

The seam is ``post_iteration_ship <slug> <gate_outcome> <total_new> [iteration]``
— the runner's post-iteration ship path, extracted from the main loop's
inline block (``run_ilk_loop_claude.sh:7183-7198``).  Named here so every
assertion below fails loudly (rather than vacuously passing on an absent
function) until it exists: an undefined shell function also writes no ledger
and ships nothing, which is exactly what AC-1's negatives would assert.

AC-1  a gated sub-plan under ``RUN_LOCAL_CHECKS=false`` is refused: no
      ship-proof rows, not shipped, ``refused, gates off`` in the output.
AC-2  a sub-plan that declares no local_checks still ships under gates off
      (today's behaviour) — and its ship-proof row is the denominator for
      AC-1's zero.  Without it, "0 rows" is what an absent writer also says.
AC-3  the run's exit state is ``gates-off``; the watchdog never relaunches it.
AC-4  ``gates-off`` is in the declared exit-state vocabulary.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SKILLS = _HERE.parent.parent  # skills/
_SCRIPTS = _SKILLS / "ilk-loop" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"
_GUARD = _SKILLS / "ilk-watchdog" / "scripts" / "relaunch_guard.py"
_LOOP_SCRIPTS = _SCRIPTS

import ilk_paths  # noqa: E402

#: The seam step 1 extracts from the main loop's post-iteration ship block.
#: Named here so every assertion below fails loudly (rather than vacuously
#: passing on an absent function) until it exists — an undefined shell
#: function also writes no ledger and ships nothing, which is exactly what
#: AC-1's negatives assert.
SEAM_FUNC = "post_iteration_ship"

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout",
)


# ── fixture helpers ──────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    # A shared remote — the condition under which commit messages carry no
    # [plan:<slug>#step-N] trailer.  Keeps the writer's commit filter out of
    # this file's subject matter.
    (repo / ".ilk-remote-type").write_text("shared\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "mark shared remote")
    return repo


def _two_iteration_commits(repo: Path) -> tuple[str, str, list[str]]:
    """Two commits, so the writer would have rows to write if it ran."""
    before = _git(repo, "rev-parse", "HEAD")
    for n in (1, 2):
        (repo / f"change{n}.txt").write_text(f"change {n}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"work: change {n}")
    after = _git(repo, "rev-parse", "HEAD")
    shas = _git(repo, "rev-list", f"{before}..{after}").split()
    assert len(shas) == 2, shas
    return before, after, shas


# Sub-plan shapes.  The gated per-step form is the trap: frontmatter says
# ``local_checks: []`` (the canonical template style) while the step fences
# declare the gate, so a frontmatter-only detector reports gates OFF and the
# ship sails through.
_SUBPLAN_GATED_PER_STEP = """\
---
plan: {slug}
status: {status}
current_step: {step}
tickets: []
priority: P1
estimated_steps: {est}
last_updated: 2026-10-10
verification_tier: loop-verified
local_checks: []
---

# Sub-plan: {slug}

## Steps

### Step 0 — first step

Body.

```yaml
local_checks:
  - command: "true"
    timeout: 30
```

### Step 1 — last step

Body.

```yaml
local_checks:
  - command: "true"
    timeout: 30
```

## Findings

_(filled by the loop during execution)_
"""

_SUBPLAN_GATED_FRONTMATTER = """\
---
plan: {slug}
status: {status}
current_step: {step}
tickets: []
priority: P1
estimated_steps: {est}
last_updated: 2026-10-10
verification_tier: loop-verified
local_checks:
  - command: "true"
    timeout: 30
---

# Sub-plan: {slug}

## Steps

### Step 0 — first step

Body.

### Step 1 — last step

Body.

## Findings

_(filled by the loop during execution)_
"""

_SUBPLAN_UNGATED = """\
---
plan: {slug}
status: {status}
current_step: {step}
tickets: []
priority: P1
estimated_steps: {est}
last_updated: 2026-10-10
verification_tier: loop-verified
local_checks: []
---

# Sub-plan: {slug}

## Steps

### Step 0 — first step

Body.

### Step 1 — last step

Body.

## Findings

_(filled by the loop during execution)_
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
    template: str = _SUBPLAN_UNGATED,
) -> tuple[Path, Path, Path]:
    """External plans under ILK_DATA_HOME.  Returns (data_home, plans, subplan)."""
    data_home = tmp_path / ".ilk-data"
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(repo)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-10-10c-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-10-10c-execution\n"
        "batch_date: 2026-10-10\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [2026-10-10c-{slug}](./2026-10-10c-{slug}.md) |\n",
        encoding="utf-8",
    )
    subplan = plans / f"2026-10-10c-{slug}.md"
    subplan.write_text(
        template.format(slug=slug, status=status, step=step, est=est),
        encoding="utf-8",
    )
    return data_home, plans, subplan


def _read_frontmatter_field(plans_dir: Path, slug: str, field: str) -> str:
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


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME + the three data-home env vars pinned inside *root*."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DATA_DIR": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
        "ILK_SKILL_HOME": str(_SKILLS),
    }


def _launcher_dir(project: Path, env: dict[str, str]) -> Path:
    resolver = _SCRIPTS / "ilk_paths.py"
    proc = subprocess.run(
        ["python3", str(resolver), "--start", str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(
            f"ilk_paths.py printed non-JSON: {exc}\nstdout: {proc.stdout}"
        ) from exc
    path = data.get("external_launcher_dir")
    assert path, f"ilk_paths.py returned no external_launcher_dir: {data!r}"
    return Path(path)


def _read_ledger(project: Path, env: dict[str, str]) -> list[dict]:
    """Rows of ship-proof.jsonl.  Empty list = no file / no rows."""
    ledger = _launcher_dir(project, env) / "ship-proof.jsonl"
    if not ledger.exists():
        return []
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            raise AssertionError(
                f"ship-proof.jsonl row is not JSON: {exc}\nrow: {line}"
            ) from exc
    return rows


def _run_seam(
    project: Path,
    env: dict[str, str],
    *,
    slug: str,
    gate_outcome: str,
    total_new: int,
    iteration: int = 1,
    run_id: str = "20261010-120000",
    before: str = "",
    after: str = "",
) -> subprocess.CompletedProcess:
    """Dot-source the runner and drive the post-iteration ship seam.

    The seam reads ``heads_before_file`` / ``heads_after_file`` from the
    caller's scope the way the main loop's inline block does, so they are
    set here as the main loop sets them.
    """
    heads_dir = project.parent / "heads"
    heads_dir.mkdir(exist_ok=True)
    (heads_dir / "before").write_text(f"{project}={before}\n", encoding="utf-8")
    (heads_dir / "after").write_text(f"{project}={after}\n", encoding="utf-8")
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{RUNNER}' || exit 90
unset ILK_DOTSOURCE_ONLY
PROJECT_PATH='{project}'
REPOS=('{project}')
RUN_ID='{run_id}'
LOOP_STATUS_SCRIPT='{_SCRIPTS / "loop_status.py"}'
PRE_ITER_TARGET=$'{slug} 0'
heads_before_file='{heads_dir / "before"}'
heads_after_file='{heads_dir / "after"}'
RUN_LOCAL_CHECKS=false
set +e
declare -F {SEAM_FUNC} >/dev/null || {{ echo "SEAM_MISSING"; exit 90; }}
{SEAM_FUNC} '{slug}' $'{gate_outcome}' {total_new} {iteration}
echo "RC=$?"
echo "STOP_REASON=${{iter_stop_reason:-}}"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
        cwd=str(project),
    )


def _assert_seam_ran(proc: subprocess.CompletedProcess) -> None:
    assert "SEAM_MISSING" not in proc.stdout, (
        f"{SEAM_FUNC} is not defined in the runner — the gates-off refusal "
        f"(step 1) has not landed.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.returncode == 0, (
        f"the seam subprocess crashed (rc={proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-1: a gated sub-plan is refused under gates off ────────────────────────


def test_refuses_the_ship_when_gates_are_off(tmp_path: Path) -> None:
    """AC-1 — per-step ``local_checks:`` fences, frontmatter ``local_checks: []``.

    The trap shape: a frontmatter-only detector reads gates OFF here and the
    ship sails through.  The denominator for "ship-proof.jsonl has 0 rows" is
    ``test_an_ungated_subplan_still_ships``, which writes rows through the
    same seam with the same fixture shape.
    """
    repo = _make_repo(tmp_path)
    data_home, plans, subplan = _make_plans_dir(
        tmp_path, repo, slug="gated-step",
        status="in-progress", step=2, est=2,
        template=_SUBPLAN_GATED_PER_STEP,
    )
    before, after, _shas = _two_iteration_commits(repo)
    env = _sandbox_env(tmp_path)

    proc = _run_seam(
        repo, env,
        slug="gated-step", gate_outcome="", total_new=2,
        run_id="20261010-120000", before=before, after=after,
    )
    _assert_seam_ran(proc)

    out = proc.stdout + proc.stderr
    assert "refused, gates off" in out, (
        "the runner did not refuse the gates-off ship of a gated sub-plan.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert _read_frontmatter_field(plans, "gated-step", "status") != "shipped", (
        "a gated sub-plan shipped under RUN_LOCAL_CHECKS=false."
    )
    rows = _read_ledger(repo, env)
    assert rows == [], (
        "ship-proof.jsonl gained rows for an ungated run of a gated sub-plan "
        f"({len(rows)} row(s)); the whole point of the row is that the step "
        f"never ran a gate.\nrows: {rows}"
    )
    body = subplan.read_text(encoding="utf-8")
    assert re.search(
        r"^- \[[^\]]+\] ungated: run 20261010-120000 had gates off; ship refused\.$",
        body,
        re.MULTILINE,
    ), (
        "the refusal did not append its Findings line.\n"
        f"sub-plan body:\n{body}"
    )


def test_refuses_the_ship_when_the_frontmatter_declares_gates_too(tmp_path: Path) -> None:
    """AC-1 — the detector sees a non-empty frontmatter ``local_checks`` as well.

    Design (binding) 1 is a disjunction: frontmatter non-empty OR a per-step
    ``local_checks:`` block.  This covers the other half.
    """
    repo = _make_repo(tmp_path)
    data_home, plans, _subplan = _make_plans_dir(
        tmp_path, repo, slug="gated-fm",
        status="in-progress", step=2, est=2,
        template=_SUBPLAN_GATED_FRONTMATTER,
    )
    before, after, _shas = _two_iteration_commits(repo)
    env = _sandbox_env(tmp_path)

    proc = _run_seam(
        repo, env,
        slug="gated-fm", gate_outcome="", total_new=2,
        run_id="20261010-120000", before=before, after=after,
    )
    _assert_seam_ran(proc)

    out = proc.stdout + proc.stderr
    assert "refused, gates off" in out, (
        "a frontmatter ``local_checks`` list was not seen as declaring gates.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert _read_frontmatter_field(plans, "gated-fm", "status") != "shipped"
    assert _read_ledger(repo, env) == []


def test_the_live_ship_path_is_the_tested_function() -> None:
    """The main loop's post-iteration ship block IS the tested seam.

    Without this, step 1 could define ``post_iteration_ship``, make every
    test above pass against it, and leave the live loop calling the old
    inline block — exactly the trap ``test_the_main_loop_calls_the_tested_function``
    exists to prevent.
    """
    text = RUNNER.read_text(encoding="utf-8")
    start = text.index(
        "# Ship-proof ledger: write rows after the gate has run so"
    )
    end = text.index("# Build new_commits JSON", start)
    block = text[start:end]
    assert f"{SEAM_FUNC}" in block, (
        "the post-iteration ship block does not call the tested seam "
        f"{SEAM_FUNC}.\nblock:\n{block}"
    )
    assert "driver_ship_if_complete" not in block, (
        "the post-iteration ship block still inlines driver_ship_if_complete "
        "instead of calling the tested seam.\nblock:\n" + block
    )
    assert "write_ship_proof_records" not in block, (
        "the post-iteration ship block still inlines write_ship_proof_records "
        "instead of calling the tested seam.\nblock:\n" + block
    )


# ── AC-2: an ungated sub-plan still ships (the denominator) ──────────────────


def test_an_ungated_subplan_still_ships(tmp_path: Path) -> None:
    """AC-2 — nothing to prove, so today's gates-off ship stands.

    This is the denominator for AC-1's "0 rows": the same seam, the same
    fixture shape, a sub-plan that declares no local_checks writes at least
    one ship-proof row and ships.  A zero without this twin is what an absent
    writer also produces.
    """
    repo = _make_repo(tmp_path)
    data_home, plans, _subplan = _make_plans_dir(
        tmp_path, repo, slug="ungated",
        status="in-progress", step=2, est=2,
        template=_SUBPLAN_UNGATED,
    )
    before, after, _shas = _two_iteration_commits(repo)
    env = _sandbox_env(tmp_path)

    proc = _run_seam(
        repo, env,
        slug="ungated", gate_outcome="", total_new=2,
        run_id="20261010-120000", before=before, after=after,
    )
    _assert_seam_ran(proc)

    out = proc.stdout + proc.stderr
    assert "refused, gates off" not in out, (
        "an ungated sub-plan was refused; design (binding) 4 keeps today's "
        f"gates-off ship for it.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    rows = _read_ledger(repo, env)
    assert len(rows) >= 1, (
        "the ungated twin wrote 0 ship-proof rows, so AC-1's zero has no "
        f"denominator and is vacuous.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert _read_frontmatter_field(plans, "ungated", "status") == "shipped", (
        "a sub-plan that declares no local_checks did not ship under gates off.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ── AC-3: exit state gates-off, and the watchdog never relaunches it ─────────


def test_the_run_ends_with_exit_state_gates_off(tmp_path: Path) -> None:
    """AC-3 — the refusal ends the run with ``gates-off``, not a park.

    MASTER judgment call (d): a gates-off run cannot ship anything gated, so
    further iterations only burn time; the scheduler's next dispatch carries
    the gates.
    """
    repo = _make_repo(tmp_path)
    data_home, plans, _subplan = _make_plans_dir(
        tmp_path, repo, slug="exit-state",
        status="in-progress", step=2, est=2,
        template=_SUBPLAN_GATED_PER_STEP,
    )
    before, after, _shas = _two_iteration_commits(repo)
    env = _sandbox_env(tmp_path)

    proc = _run_seam(
        repo, env,
        slug="exit-state", gate_outcome="", total_new=2,
        run_id="20261010-120000", before=before, after=after,
    )
    _assert_seam_ran(proc)

    assert "STOP_REASON=gates-off" in proc.stdout, (
        "the refusal did not leave the run's exit state at gates-off.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def _world(root: Path) -> dict:
    """A tmp project + data home + plans, the way the watchdog tests build one."""
    project = root / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    (project / "README.md").write_text("x\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "init")
    data_home = root / ".ilk-data"
    prev = os.environ.get("ILK_DATA_HOME")
    os.environ["ILK_DATA_HOME"] = str(data_home)
    try:
        key = ilk_paths.project_key(project)
        plans = ilk_paths.external_plans_dir(key)
        launcher = ilk_paths.external_launcher_dir(key)
    finally:
        if prev is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = prev
    plans.mkdir(parents=True)
    launcher.mkdir(parents=True)
    (plans / "2026-10-10c-aaa.md").write_text(
        "---\nplan: aaa\nstatus: in-progress\ncurrent_step: 0\n"
        "estimated_steps: 2\n---\n\n### Step 0 — x\n\n### Step 1 — y\n",
        encoding="utf-8",
    )
    (plans / "MASTER-2026-10-10c.md").write_text(
        "---\nmaster_plan: 2026-10-10c\nstatus: active\n---\n\n"
        "| # | Slug |\n|---|---|\n| 1 | 2026-10-10c-aaa.md |\n",
        encoding="utf-8",
    )
    return {
        "project": project,
        "data_home": data_home,
        "plans": plans,
        "launcher": launcher,
        "root": root,
    }


def _sentinel(
    launcher: Path,
    *,
    stopped_by: str | None,
    ended: datetime,
    state: str = "interrupted",
) -> None:
    d = {
        "state": state,
        "pid": None,
        "run_id": "20261010-120000",
        "started_at": (ended - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S"),
        "ended_at": ended.strftime("%Y-%m-%dT%H:%M:%S"),
        "stopped_reason": (
            "operator stop (SIGTERM)" if stopped_by else "run ended without a stop signal"
        ),
    }
    if stopped_by:
        d["stopped_by"] = stopped_by
    (launcher / "last-exit.json").write_text(json.dumps(d), encoding="utf-8")


def _guard(w: dict) -> tuple[int, dict]:
    r = subprocess.run(
        [sys.executable, str(_GUARD), "--project", str(w["project"]),
         "--launcher-dir", str(w["launcher"])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env={**os.environ, "ILK_DATA_HOME": str(w["data_home"])},
    )
    try:
        out = json.loads(r.stdout)
    except ValueError as exc:
        raise AssertionError(
            f"relaunch_guard.py printed non-JSON: {exc}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        ) from exc
    return r.returncode, out


def test_the_guard_refuses_to_relaunch_a_gates_off_run(tmp_path: Path) -> None:
    """AC-3 — relaunch_guard refuses a ``gates-off`` sentinel.

    Today's guard checks run.lock, launch freshness, an operator stop
    (``state == "interrupted"`` + ``stopped_by`` starting ``signal:``) and a
    held project, then falls through to ``return 0, {"verdict": "ok"}``.  A
    ``gates-off`` sentinel is therefore relaunchable — the exact defect.
    """
    w = _world(tmp_path)
    _sentinel(w["launcher"], stopped_by=None, ended=datetime.now(), state="gates-off")
    rc, out = _guard(w)
    assert rc not in (0, 10), (
        "relaunch_guard allowed a gates-off run to be relaunched "
        f"(rc={rc}, verdict={out.get('verdict')!r})."
    )
    assert out.get("verdict") != "ok", out


@pytest.fixture(scope="module")
def skill_copy(tmp_path_factory) -> Path:
    """A copy of skills/ with launch.sh and collect.py stubbed."""
    dst = tmp_path_factory.mktemp("skills-copy") / "skills"
    shutil.copytree(
        _SKILLS,
        dst,
        ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc", ".pytest_cache"),
    )
    (dst / "ilk-launcher" / "scripts" / "launch.sh").write_text(
        '#!/usr/bin/env bash\necho "$@" >> "$WATCHDOG_TEST_LAUNCHES"\nexit 0\n',
        encoding="utf-8",
    )
    # The incident shape: the postmortem answers a relaunchable label.  Only
    # the sentinel state (gates-off) stands between that label and a relaunch.
    (dst / "ilk-feedback" / "scripts" / "collect.py").write_text(
        "import os\n"
        "p = os.path.join(os.environ['WATCHDOG_TEST_DIR'], 'postmortem.md')\n"
        "open(p, 'w').write('---\\nclassification: max-iter-bound\\n---\\n')\n"
        "print(p)\n",
        encoding="utf-8",
    )
    return dst


def _run_watchdog(
    skills: Path, w: dict
) -> tuple[subprocess.CompletedProcess, str]:
    launches = w["root"] / "launches.txt"
    env = {
        **os.environ,
        "HOME": str(w["root"]),
        "ILK_DATA_HOME": str(w["data_home"]),
        "ILK_SKILL_HOME": str(skills),
        "WATCHDOG_TEST_LAUNCHES": str(launches),
        "WATCHDOG_TEST_DIR": str(w["root"]),
        "ILK_NOTIFY": "0",
    }
    env.pop("ILK_DATA_DIR", None)
    proc = subprocess.run(
        ["gtimeout", "25", "bash", str(skills / "ilk-watchdog" / "scripts" / "watchdog.sh"),
         "--project-path", str(w["project"]), "--poll-interval-sec", "60",
         "--max-restarts", "3"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )
    return proc, launches.read_text() if launches.exists() else ""


def _activity(w: dict) -> str:
    logs = list(w["data_home"].rglob("activity.log"))
    return "\n".join(p.read_text(errors="replace") for p in logs)


@_NEEDS_GTIMEOUT
def test_the_watchdog_never_relaunches_a_gates_off_run(
    tmp_path: Path, skill_copy: Path
) -> None:
    """AC-3 — through the real watchdog loop, with collect.py answering a
    relaunchable label exactly as in the incident: a ``gates-off`` sentinel
    is NOT relaunched.  Driven the way
    ``test_the_watchdog_never_relaunches_a_stop_or_a_hold.py`` drives a stop
    or a hold — same treatment.
    """
    w = _world(tmp_path)
    _sentinel(w["launcher"], stopped_by=None, ended=datetime.now(), state="gates-off")
    proc, launches = _run_watchdog(skill_copy, w)
    log = _activity(w) + proc.stdout + proc.stderr
    assert "max-iter-bound" in log, (
        "the stubbed postmortem was not consulted; the scenario is not the "
        f"one this pins.\n{log[-3000:]}"
    )
    assert launches == "", f"relaunched a gates-off run: {launches!r}\n{log[-3000:]}"
    assert "NOT RELAUNCHING" in log, log[-3000:]


# ── AC-4: gates-off is in the declared exit-state vocabulary ─────────────────


def _vocab_module():
    """Load the doc-guard module by path, without collecting its tests."""
    spec = importlib.util.spec_from_file_location(
        "exit_state_vocabulary", _HERE / "test_exit_state_vocabulary.py"
    )
    assert spec is not None and spec.loader is not None, "vocab module not loadable"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gates_off_is_in_the_declared_exit_state_vocabulary() -> None:
    """AC-4 — the v0.9.121 doc guard derives the vocabulary from source, so
    ``gates-off`` must be written by a runner AND declared in the contract's
    ``### State vocabulary`` table.  Declaring it in only one place is the
    drift this guard exists to catch.
    """
    vocab = _vocab_module()
    derived = vocab._extract_state_literals(vocab._BASH_RUNNER, vocab._PS_RUNNER)
    assert "gates-off" in derived, (
        "no runner writes the exit state 'gates-off'; the derived vocabulary "
        f"is {sorted(derived)}"
    )
    contract_text = vocab._CONTRACT_PATH.read_text(encoding="utf-8")
    section = vocab._contract_vocabulary_section(contract_text)
    assert '"gates-off"' in section or "'gates-off'" in section, (
        "exit state 'gates-off' is written by a runner but not declared in "
        f"the {vocab._VOCAB_HEADING} table of {vocab._CONTRACT_PATH.name}."
    )
