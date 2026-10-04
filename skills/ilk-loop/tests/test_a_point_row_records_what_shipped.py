"""Red-first pins: the point row's shipped list and master field.

Part of sub-plan ``a-point-row-records-what-shipped`` (MASTER-2026-10-04m).

``run_ilk_loop_claude.sh:6909-6929`` builds the ``shipped`` list by reading
each ``PRE_ITER_ALL_STEPS`` slug's status from ``${_PLANS_DIR}/${slug}.md``.
``_PLANS_DIR`` is never assigned (0 assignments, 2 occurrences), so under
``set -Eeuo pipefail`` the subshell aborts with ``unbound variable`` and
``shipped`` is always ``[]``.  33 of 37 real ``points.jsonl`` rows have
``shipped: []``; 37 of 37 have ``master: ""``.

These tests pin the expected behaviour:

  AC-1  ``point_row_shipped_slugs`` echoes exactly the slugs whose
        frontmatter turned ``shipped`` during the iteration.
  AC-2  ``ledger_record_point`` with the real ``suite_ledger.py`` appends
        a row with ``shipped == ["alpha"]`` and the MASTER's basename.
  AC-3  stderr contains 0 occurrences of ``unbound variable``.
  AC-4  (static) the runner text contains 0 occurrences of ``_PLANS_DIR``
        and 0 of ``_ACTIVE_MASTER_BASENAME``.
  AC-5  (control) nothing shipped, one commit: row IS written with
        ``shipped == []``.
  AC-6  (control) nothing shipped, no commit: no row appended.
  AC-7  (control) every test in the Step 1 gate's existing files passes.

AC-1..AC-4 are ``xfail(strict=True)`` — the function does not exist yet.
AC-5 is ``xfail`` because it calls the new helper.  AC-6 passes today
(``ledger_record_point`` already exists and the no-commit guard works).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent / "scripts"
_RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_PATH = os.environ.get("PATH", "/usr/bin:/bin")

#: The seam step 1 adds to the runner.  Named here so every assertion below
#: fails loudly (rather than vacuously passing on an absent function) until it
#: exists — an undefined shell function echoes nothing, which is exactly
#: what AC-1 asserts does NOT happen.
NEW_FUNC = "point_row_shipped_slugs"


# ── fixture helpers ──────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _subplan(plans: Path, slug: str, current_step: int, steps: int,
             status: str = "in-progress") -> Path:
    sp = plans / f"2026-08-29-{slug}.md"
    sp.write_text(
        "---\n"
        f"plan: {slug}\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {steps}\n"
        "local_checks: []\n"
        "---\n\n"
        f"# Sub-plan: {slug}\n\n"
        + "".join(f"### Step {n} — work\n\nBody.\n\n" for n in range(steps)),
        encoding="utf-8",
    )
    return sp


def _make_project(root: Path, subplans: dict[str, tuple[int, int]]) -> Path:
    """A git repo with ``docs/plans`` holding a MASTER + the named sub-plans.

    *subplans* maps slug -> (current_step, estimated_steps).
    """
    plans = root / "docs" / "plans"
    plans.mkdir(parents=True)
    registry = "\n".join(
        f"| {n} | [2026-08-29-{slug}.md](./2026-08-29-{slug}.md) | pending |"
        for n, slug in enumerate(subplans, start=1)
    )
    (plans / "MASTER-2026-08-29-ledger.md").write_text(
        "---\n"
        "master_plan: 2026-08-29-ledger\n"
        "batch_date: 2026-08-29\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: ledger\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        f"{registry}\n",
        encoding="utf-8",
    )
    for slug, (cur, steps) in subplans.items():
        _subplan(plans, slug, cur, steps)

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside *root* so nothing reads ~/.ilk-data."""
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _capture_pre_iter(project: Path, env: dict[str, str]) -> str:
    """Capture ``PRE_ITER_ALL_STEPS`` the same way the runner does at :5494."""
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
PROJECT_PATH='{project}'
LOOP_STATUS_SCRIPT='{_SCRIPTS / "loop_status.py"}'
_SKILL_ROOT='{_SCRIPTS.parent}'
get_all_subplan_steps
"""
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env, cwd=str(project),
    )
    assert proc.returncode == 0, f"get_all_subplan_steps failed: {proc.stderr}"
    return proc.stdout.strip()


def _run_point_row_shipped(
    project: Path, env: dict[str, str], pre_iter_all_steps: str,
) -> subprocess.CompletedProcess:
    """Dot-source the runner and call ``point_row_shipped_slugs``."""
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
PROJECT_PATH='{project}'
REPOS=('{project}')
RUN_ID='test-run-001'
LOOP_STATUS_SCRIPT='{_SCRIPTS / "loop_status.py"}'
_SKILL_ROOT='{_SCRIPTS.parent}'
PRE_ITER_ALL_STEPS=$'{pre_iter_all_steps}'
set +e
declare -F {NEW_FUNC} >/dev/null || {{ echo "FUNC_MISSING"; exit 90; }}
{NEW_FUNC}
echo "RC=$?"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(project),
    )


def _run_ledger_record(
    project: Path, env: dict[str, str],
    before: str, shipped: str,
) -> subprocess.CompletedProcess:
    """Dot-source the runner and call ``ledger_record_point`` with real suite_ledger."""
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
PROJECT_PATH='{project}'
REPOS=('{project}')
RUN_ID='test-run-001'
LOOP_STATUS_SCRIPT='{_SCRIPTS / "loop_status.py"}'
_SKILL_ROOT='{_SCRIPTS.parent}'
_iter_slug='test-slug'
ACTIVE_MASTER_BASENAME=''
set +e
ledger_record_point '{project}' '{before}' '{shipped}'
echo "RC=$?"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(project),
    )


def _read_points(project: Path, env: dict[str, str]) -> list[dict]:
    """Read the points.jsonl ledger written by suite_ledger.py."""
    resolver = _SCRIPTS / "ilk_paths.py"
    proc = subprocess.run(
        ["python3", str(resolver), "--start", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    ledger = Path(data["external_logs_dir"]) / "verification" / "ledger" / "points.jsonl"
    if not ledger.exists():
        return []
    out = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ── AC-1 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="point_row_shipped_slugs does not exist yet")
def test_shipped_slugs_echoes_exactly_the_shipped_ones(tmp_path: Path) -> None:
    """AC-1 — ``point_row_shipped_slugs`` echoes exactly the slugs whose
    frontmatter turned ``shipped`` during the iteration.

    Fixture: alpha and beta, both in-progress.  Capture PRE_ITER_ALL_STEPS,
    flip alpha to shipped, call the helper.
    """
    project = _make_project(tmp_path / "proj", {"alpha": (0, 2), "beta": (0, 2)})
    env = _sandbox_env(tmp_path)

    pre_iter = _capture_pre_iter(project, env)
    assert "alpha" in pre_iter
    assert "beta" in pre_iter

    # Flip alpha to shipped.
    plans = project / "docs" / "plans"
    alpha_file = plans / "2026-08-29-alpha.md"
    text = alpha_file.read_text(encoding="utf-8")
    alpha_file.write_text(text.replace("status: in-progress", "status: shipped"),
                          encoding="utf-8")

    proc = _run_point_row_shipped(project, env, pre_iter)
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr}"
    # The helper should echo exactly "alpha" (no newline issues).
    output = proc.stdout.strip()
    assert output == "alpha", f"expected 'alpha', got {output!r}"


# ── AC-2 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="point_row_shipped_slugs does not exist yet")
def test_ledger_record_point_writes_shipped_and_master(tmp_path: Path) -> None:
    """AC-2 — ``ledger_record_point`` with the real ``suite_ledger.py`` appends
    a row with ``shipped == ["alpha"]`` and ``master`` equal to the MASTER's
    basename.

    Fixture: alpha shipped, beta in-progress.  One commit exists.
    """
    project = _make_project(tmp_path / "proj", {"alpha": (0, 2), "beta": (0, 2)})
    env = _sandbox_env(tmp_path)

    pre_iter = _capture_pre_iter(project, env)

    # Flip alpha to shipped.
    plans = project / "docs" / "plans"
    alpha_file = plans / "2026-08-29-alpha.md"
    text = alpha_file.read_text(encoding="utf-8")
    alpha_file.write_text(text.replace("status: in-progress", "status: shipped"),
                          encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "ship alpha")

    before = _git(project, "rev-parse", "HEAD~1")
    shipped = _run_point_row_shipped(project, env, pre_iter).stdout.strip()

    proc = _run_ledger_record(project, env, before, shipped)
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr}"

    rows = _read_points(project, env)
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
    row = rows[0]
    assert row["shipped"] == ["alpha"], f"shipped={row['shipped']}"
    assert "MASTER" in row["master"], f"master={row['master']!r}"


# ── AC-3 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="_PLANS_DIR is unassigned; inline loop aborts under set -u")
def test_no_unbound_variable_in_stderr(tmp_path: Path) -> None:
    """AC-3 — the shipped-list extraction contains 0 occurrences of
    ``unbound variable`` in stderr.

    This test exercises the REAL inline loop at ``:6911-6929`` (the code path
    that references ``_PLANS_DIR``), not the new helper.  It sources the
    runner, sets ``PRE_ITER_ALL_STEPS``, and runs the inline loop directly.
    """
    project = _make_project(tmp_path / "proj", {"alpha": (0, 2), "beta": (0, 2)})
    env = _sandbox_env(tmp_path)

    pre_iter = _capture_pre_iter(project, env)

    # Flip alpha to shipped.
    plans = project / "docs" / "plans"
    alpha_file = plans / "2026-08-29-alpha.md"
    text = alpha_file.read_text(encoding="utf-8")
    alpha_file.write_text(text.replace("status: in-progress", "status: shipped"),
                          encoding="utf-8")

    # Run the inline loop (the code at :6911-6929) directly.
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{_RUNNER}'
PROJECT_PATH='{project}'
REPOS=('{project}')
RUN_ID='test-run-001'
LOOP_STATUS_SCRIPT='{_SCRIPTS / "loop_status.py"}'
_SKILL_ROOT='{_SCRIPTS.parent}'
PRE_ITER_ALL_STEPS=$'{pre_iter}'
set +e
# Inline loop from :6911-6929 (the code that uses _PLANS_DIR).
_point_shipped=""
if [[ -n "${{PRE_ITER_ALL_STEPS:-}}" ]]; then
  while read -r _pre_line; do
    _pre_slug="${{_pre_line%% *}}"
    [[ -z "$_pre_slug" ]] && continue
    _pre_fm=$(python3 -c "
import sys
sys.path.insert(0, sys.argv[1])
from loop_status import parse_frontmatter
from pathlib import Path
p = Path(sys.argv[2])
fm = parse_frontmatter(p.read_text(encoding='utf-8'))
print(fm.get('status', ''))
" "${{_SKILL_ROOT}}/ilk-loop/scripts" "${{_PLANS_DIR}}/${{_pre_slug}}.md" 2>/dev/null) || _pre_fm=""
    if [[ "$_pre_fm" == "shipped" ]]; then
      _point_shipped="${{_point_shipped:+$_point_shipped,}}$_pre_slug"
    fi
  done <<< "$PRE_ITER_ALL_STEPS"
fi
echo "shipped=$_point_shipped"
"""
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(project),
    )
    assert "unbound variable" not in proc.stderr, (
        f"found 'unbound variable' in stderr:\n{proc.stderr}"
    )


# ── AC-4 (static) ───────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="_PLANS_DIR and _ACTIVE_MASTER_BASENAME still in runner")
def test_no_plans_dir_variable_in_runner() -> None:
    """AC-4 (static) — the runner text contains 0 occurrences of ``_PLANS_DIR``
    and 0 of ``_ACTIVE_MASTER_BASENAME``."""
    text = _RUNNER.read_text(encoding="utf-8")
    plans_dir_count = text.count("_PLANS_DIR")
    active_master_count = text.count("_ACTIVE_MASTER_BASENAME")
    assert plans_dir_count == 0, f"_PLANS_DIR appears {plans_dir_count} times"
    assert active_master_count == 0, f"_ACTIVE_MASTER_BASENAME appears {active_master_count} times"


# ── AC-5 (control) ──────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="point_row_shipped_slugs does not exist yet")
def test_nothing_shipped_one_commit_writes_row_with_empty_shipped(tmp_path: Path) -> None:
    """AC-5 (control) — nothing shipped, one commit (a sub-plan advanced
    ``current_step`` but stayed ``in-progress``): ``point_row_shipped_slugs``
    echoes nothing and the row IS written with ``shipped == []``."""
    project = _make_project(tmp_path / "proj", {"alpha": (0, 2), "beta": (0, 2)})
    env = _sandbox_env(tmp_path)

    pre_iter = _capture_pre_iter(project, env)

    # Advance alpha's current_step but keep it in-progress.
    plans = project / "docs" / "plans"
    alpha_file = plans / "2026-08-29-alpha.md"
    text = alpha_file.read_text(encoding="utf-8")
    alpha_file.write_text(text.replace("current_step: 0", "current_step: 1"),
                          encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "advance alpha")

    before = _git(project, "rev-parse", "HEAD~1")
    shipped = _run_point_row_shipped(project, env, pre_iter).stdout.strip()
    assert shipped == "", f"expected empty shipped, got {shipped!r}"

    proc = _run_ledger_record(project, env, before, shipped)
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr}"

    rows = _read_points(project, env)
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
    assert rows[0]["shipped"] == [], f"shipped={rows[0]['shipped']}"


# ── AC-6 (control) ──────────────────────────────────────────────────────────

def test_nothing_shipped_no_commit_writes_no_row(tmp_path: Path) -> None:
    """AC-6 (control) — nothing shipped and no commit (``before == after``):
    ``ledger_record_point`` appends no row (``suite_ledger.py:336``).

    This test passes today — ``ledger_record_point`` already exists and the
    no-commit guard works.
    """
    project = _make_project(tmp_path / "proj", {"alpha": (0, 2)})
    env = _sandbox_env(tmp_path)

    head = _git(project, "rev-parse", "HEAD")
    proc = _run_ledger_record(project, env, head, "")
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr}"

    rows = _read_points(project, env)
    assert len(rows) == 0, f"expected 0 rows, got {len(rows)}"