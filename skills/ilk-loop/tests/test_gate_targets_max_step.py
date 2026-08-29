"""Red-first: on a shared remote the last step's gate is structurally unreachable.

Gate targets have two sources in ``run_ilk_loop_claude.sh``:

===========================================  ===========================  =========
source                                       emits                        when
===========================================  ===========================  =========
``get_local_check_targets`` (``:798``)       **max** step per slug, from  personal
                                             commit trailers              remote
``get_active_subplan_targets`` (``:766``)    the sub-plan's               shared
                                             ``current_step`` **as        remote
                                             captured before the
                                             iteration** (``PRE_ITER_TARGET``,
                                             ``:1929``)
===========================================  ===========================  =========

Pre-iteration step versus max-step-committed.  When an agent advances several
steps in one iteration, the shared-remote path gates the step the iteration
*started* on — so the final step's gate never runs, and that is exactly where a
planner puts the broadest command.

Measured on ``kira-cloudflare`` run ``20260829-001901``: two sub-plans, both
shipped, both declaring their directory-wide gate on their **last** step
(step 2 of 3 and step 3 of 4).  Neither directory gate was ever a target; the
only command that ran, all four times, was the single-file frontmatter gate.

  AC-7  shared remote, a 3-step sub-plan advanced 0→3 in one iteration:
        the step-2 gate is a target
  AC-8  personal remote unchanged — the trailer path keeps its targets

Sub-plan: ``a-shared-remote-ship-can-be-proven`` (AC-7, AC-8).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

RUNNER = (Path(__file__).resolve().parent.parent / "scripts"
          / "run_ilk_loop_claude.sh")
SCRIPTS = RUNNER.parent

_PATH = os.environ.get("PATH", "/usr/bin:/bin")

#: The seam step 3 adds: resolve gate targets from this iteration's ledger
#: records rather than from the pre-iteration capture.
LEDGER_FUNC = "get_ledger_check_targets"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _runner_lines() -> list[str]:
    return RUNNER.read_text(encoding="utf-8").splitlines()


def _line_of(pattern: str) -> int:
    """1-indexed line number of the first non-comment line matching *pattern*."""
    rx = re.compile(pattern)
    for n, line in enumerate(_runner_lines(), start=1):
        if rx.search(line) and not line.lstrip().startswith("#"):
            return n
    raise AssertionError(
        f"no executable line in {RUNNER.name} matches {pattern!r} — the runner "
        "was restructured and this test's anchors need re-deriving"
    )


def _sandbox_env(root: Path) -> dict[str, str]:
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _launcher_dir(project: Path, env: dict[str, str]) -> Path:
    proc = subprocess.run(
        ["python3", str(SCRIPTS / "ilk_paths.py"), "--start", str(project)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return Path(json.loads(proc.stdout)["external_launcher_dir"])


def _make_project(root: Path, remote_type: str, *, slug: str = "gate-work") -> Path:
    """A git repo whose sub-plan declares its broadest gate on its LAST step."""
    project = root / "proj"
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-08-29-gate.md").write_text(
        "---\n"
        "master_plan: 2026-08-29-gate\n"
        "batch_date: 2026-08-29\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: gate\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        f"| 1 | [2026-08-29-{slug}.md](./2026-08-29-{slug}.md) | shipped |\n",
        encoding="utf-8",
    )
    (plans / f"2026-08-29-{slug}.md").write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: shipped\n"
        "current_step: 3\n"
        "estimated_steps: 3\n"
        "---\n\n"
        f"# Sub-plan: {slug}\n\n"
        "### Step 0 — narrow\n\n"
        "```yaml\nlocal_checks:\n  - command: echo one-file\n    timeout: 30\n```\n\n"
        "### Step 1 — narrow\n\n"
        "```yaml\nlocal_checks:\n  - command: echo one-file\n    timeout: 30\n```\n\n"
        "### Step 2 — the broad one\n\n"
        "```yaml\nlocal_checks:\n  - command: echo directory-wide\n    timeout: 60\n```\n",
        encoding="utf-8",
    )
    (project / ".ilk-remote-type").write_text(f"{remote_type}\n", encoding="utf-8")

    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")
    return project


def _write_ledger(project: Path, env: dict[str, str], records: list[dict]) -> None:
    d = _launcher_dir(project, env)
    d.mkdir(parents=True, exist_ok=True)
    (d / "ship-proof.jsonl").write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records),
        encoding="utf-8",
    )


def _dotsource(project: Path, env: dict[str, str], script: str):
    prelude = f"""
export ILK_DOTSOURCE_ONLY=1
source '{RUNNER}'
PROJECT_PATH='{project}'
REPOS=('{project}')
LOOP_STATUS_SCRIPT='{SCRIPTS / "loop_status.py"}'
set +e
"""
    return subprocess.run(
        ["bash", "-c", prelude + script],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(project),
    )


# ── AC-7 ─────────────────────────────────────────────────────────────────────

def test_shared_remote_gate_targets_the_step_the_iteration_reached(
    tmp_path: Path,
) -> None:
    """AC-7 — a 3-step sub-plan advanced 0→3 in one iteration gates step 2.

    ``PRE_ITER_TARGET`` would say ``gate-work 0`` here, which is the whole
    defect: the broadest command in the sub-plan is declared on step 2 and
    would never be a target.
    """
    env = _sandbox_env(tmp_path)
    project = _make_project(tmp_path, "shared")
    _write_ledger(project, env, [{
        "run_id": "20260829-120000", "iteration": 5, "slug": "gate-work",
        "repo": str(project), "step_from": 0, "step_to": 3,
        "commits": ["aaaa111", "bbbb222", "cccc333"],
    }])

    proc = _dotsource(project, env, f"""
declare -F {LEDGER_FUNC} >/dev/null || {{ echo "LEDGER_FUNC_MISSING"; exit 90; }}
{LEDGER_FUNC} '20260829-120000' 5
""")
    assert "LEDGER_FUNC_MISSING" not in proc.stdout, (
        f"{LEDGER_FUNC} is not defined in the runner — the ledger-based gate "
        f"targeting (step 3) has not landed.\n{proc.stdout}\n{proc.stderr}"
    )

    targets = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    assert "gate-work 2" in targets, (
        "the step the iteration REACHED (step_to - 1 = 2) must be a gate "
        f"target; got {targets!r}. With PRE_ITER_TARGET as the source this is "
        "'gate-work 0' and the step-2 gate never runs.\nstderr: {}"
        .format(proc.stderr)
    )
    assert all(t.startswith("gate-work ") for t in targets), (
        f"only the worked slug may be emitted; got {targets!r}"
    )

    # Wiring: a function that exists but is never called leaves the defect in
    # place.  The call must sit inside the trailerless fallback branch, i.e.
    # after the `! -s "$all_targets_file"` test and before the slug merge.
    branch = _line_of(r'if \[\[ ! -s "\$all_targets_file" \]\]')
    merge = _line_of(r'sort.*all_targets_file.*merged_targets_file')
    call = _line_of(rf'{LEDGER_FUNC}\b.*>>')
    assert branch < call < merge, (
        f"{LEDGER_FUNC} is called at line {call}, outside the trailerless "
        f"fallback branch ({branch}..{merge}). The ledger must supply the "
        "fallback's targets, not run unconditionally."
    )


# ── AC-8 ─────────────────────────────────────────────────────────────────────

def test_personal_remote_trailer_path_is_unchanged(tmp_path: Path) -> None:
    """AC-8 — regression guard: where trailers exist, nothing moves.

    The anti-vacuity check comes first on purpose. Before the ledger exists,
    "the trailer path is unchanged by the ledger" is a claim about nothing,
    and would pass against a runner that never grew the feature.
    """
    env = _sandbox_env(tmp_path)
    project = _make_project(tmp_path, "personal")

    probe = _dotsource(project, env, f"declare -F {LEDGER_FUNC} >/dev/null "
                                     f"&& echo DEFINED || echo MISSING")
    assert "DEFINED" in probe.stdout, (
        f"{LEDGER_FUNC} is not defined — this regression guard is vacuous "
        f"until the feature it guards against exists.\n{probe.stdout}\n{probe.stderr}"
    )

    before = _git(project, "rev-parse", "HEAD")
    for n in (0, 1):
        (project / f"file{n}.txt").write_text(f"step {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m",
             f"fix(app): step {n} [plan:gate-work#step-{n}]")
    after = _git(project, "rev-parse", "HEAD")

    # A ledger that disagrees with the trailers.  Trailers are the stronger
    # evidence — they are in the commit itself — so they must win here.
    _write_ledger(project, env, [{
        "run_id": "20260829-120000", "iteration": 5, "slug": "gate-work",
        "repo": str(project), "step_from": 0, "step_to": 3,
        "commits": ["aaaa111"],
    }])

    proc = _dotsource(project, env,
                      f"get_local_check_targets '{project}' '{before}' '{after}'")
    targets = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    assert targets == ["gate-work 1"], (
        "trailer scanning must still emit the max committed step per slug, "
        f"unchanged by the ledger; got {targets!r}\nstderr: {proc.stderr}"
    )
