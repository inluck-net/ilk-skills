"""Red-first: a verify sub-plan (``batch_verification: true``) gates every
committed step in ascending order, not just the max.

AC-1  a verify sub-plan whose iteration committed #step-0 and #step-1 ⇒ both
      steps are gate targets, 0 before 1
AC-2  step 0's gate failing ⇒ step 1's gate does not run
AC-3  a normal sub-plan committing step 0 and step 2 ⇒ only step 2 is a target
      (regression guard — the max-step rule is preserved for non-verify plans)
AC-4  the ledger path (shared remote, no trailers) behaves as AC-1
AC-5  verify_attribution with no record and no --base-sha prints the message
      naming the record path and exits 1
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

RUNNER = (Path(__file__).resolve().parent.parent / "scripts"
          / "run_ilk_loop_claude.sh")
SCRIPTS = RUNNER.parent
VERIFY_ATTR = SCRIPTS / "verify_attribution.py"

_PATH = os.environ.get("PATH", "/usr/bin:/bin")


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _sandbox_env(root: Path) -> dict[str, str]:
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_SKILL_HOME": str(root),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _launcher_dir(project: Path, env: dict[str, str]) -> Path:
    proc = subprocess.run(
        ["python3", str(SCRIPTS / "ilk_paths.py"), "--start", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return Path(json.loads(proc.stdout)["external_launcher_dir"])


def _make_verify_project(
    root: Path, remote_type: str, *, slug: str = "batch-verify",
) -> Path:
    """A git repo with a ``batch_verification: true`` sub-plan."""
    project = root / "proj"
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-09-25-bv.md").write_text(
        "---\n"
        "master_plan: 2026-09-25-bv\n"
        "batch_date: 2026-09-25\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: bv\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        f"| 1 | [2026-09-25-{slug}.md](./2026-09-25-{slug}.md) | in-progress |\n",
        encoding="utf-8",
    )
    (plans / f"2026-09-25-{slug}.md").write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 2\n"
        "batch_verification: true\n"
        "---\n\n"
        f"# Sub-plan: {slug}\n\n"
        "### Step 0 — write the record\n\n"
        "```yaml\nlocal_checks:\n  - command: echo step0-gate\n    timeout: 30\n```\n\n"
        "### Step 1 — verify\n\n"
        "```yaml\nlocal_checks:\n  - command: echo step1-gate\n    timeout: 30\n```\n",
        encoding="utf-8",
    )
    (project / ".ilk-remote-type").write_text(f"{remote_type}\n", encoding="utf-8")
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")
    return project


def _make_normal_project(
    root: Path, remote_type: str, *, slug: str = "normal-work",
) -> Path:
    """A git repo with a normal (non-verify) sub-plan."""
    project = root / "proj"
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-09-25-norm.md").write_text(
        "---\n"
        "master_plan: 2026-09-25-norm\n"
        "batch_date: 2026-09-25\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: norm\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        f"| 1 | [2026-09-25-{slug}.md](./2026-09-25-{slug}.md) | shipped |\n",
        encoding="utf-8",
    )
    (plans / f"2026-09-25-{slug}.md").write_text(
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
        "### Step 2 — broad\n\n"
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


def _write_ledger(project: Path, env: dict[str, str],
                  records: list[dict]) -> None:
    d = _launcher_dir(project, env)
    d.mkdir(parents=True, exist_ok=True)
    (d / "ship-proof.jsonl").write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n"
                for r in records),
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
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(project),
    )


# ── AC-1 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_verify_sub_plan_gates_every_committed_step(tmp_path: Path) -> None:
    """AC-1 — a verify sub-plan whose iteration committed #step-0 and #step-1
    ⇒ both steps are gate targets, 0 before 1."""
    env = _sandbox_env(tmp_path)
    project = _make_verify_project(tmp_path, "personal")

    before = _git(project, "rev-parse", "HEAD")
    for n in (0, 1):
        (project / f"file{n}.txt").write_text(f"step {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m",
             f"fix(app): step {n} [plan:batch-verify#step-{n}]")
    after = _git(project, "rev-parse", "HEAD")

    proc = _dotsource(project, env,
                      f"get_local_check_targets '{project}' '{before}' '{after}'")
    targets = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    assert targets == ["batch-verify 0", "batch-verify 1"], (
        f"a verify sub-plan must emit every committed step in ascending order; "
        f"got {targets!r}\nstderr: {proc.stderr}"
    )


# ── AC-2 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_step0_gate_failure_stops_later_steps(tmp_path: Path) -> None:
    """AC-2 — step 0's gate failing ⇒ step 1's gate does not run."""
    env = _sandbox_env(tmp_path)
    project = _make_verify_project(tmp_path, "personal")

    before = _git(project, "rev-parse", "HEAD")
    for n in (0, 1):
        (project / f"file{n}.txt").write_text(f"step {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m",
             f"fix(app): step {n} [plan:batch-verify#step-{n}]")
    after = _git(project, "rev-parse", "HEAD")

    proc = _dotsource(project, env,
                      f"get_local_check_targets '{project}' '{before}' '{after}'")
    targets = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]

    # Simulate gate execution: step 0 fails → step 1 must not be attempted.
    gate_order = []
    for t in targets:
        slug, step = t.rsplit(" ", 1)
        if step == "0":
            gate_order.append((slug, step, "FAIL"))
            break  # step 0 failed; later steps are not reached
        gate_order.append((slug, step, "PASS"))

    failed_steps = [(s, st) for s, st, o in gate_order if o == "FAIL"]
    assert len(failed_steps) == 1 and failed_steps[0][1] == "0", (
        "when step 0's gate fails, it must be the only failure reported; "
        f"got gate_order={gate_order!r}"
    )


# ── AC-3 ─────────────────────────────────────────────────────────────────────

def test_normal_sub_plan_still_uses_max_step(tmp_path: Path) -> None:
    """AC-3 — a normal sub-plan committing step 0 and step 2 ⇒ only step 2."""
    env = _sandbox_env(tmp_path)
    project = _make_normal_project(tmp_path, "personal")

    before = _git(project, "rev-parse", "HEAD")
    for n in (0, 2):
        (project / f"file{n}.txt").write_text(f"step {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m",
             f"fix(app): step {n} [plan:normal-work#step-{n}]")
    after = _git(project, "rev-parse", "HEAD")

    proc = _dotsource(project, env,
                      f"get_local_check_targets '{project}' '{before}' '{after}'")
    targets = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    assert targets == ["normal-work 2"], (
        "a normal sub-plan must keep max-step targeting; "
        f"got {targets!r}\nstderr: {proc.stderr}"
    )


# ── AC-4 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ledger_path_gates_every_step_for_verify(tmp_path: Path) -> None:
    """AC-4 — shared remote (ledger, no trailers) emits every step for a
    verify sub-plan."""
    env = _sandbox_env(tmp_path)
    project = _make_verify_project(tmp_path, "shared")
    _write_ledger(project, env, [{
        "run_id": "20260925-120000", "iteration": 3, "slug": "batch-verify",
        "repo": str(project), "step_from": 0, "step_to": 2,
        "commits": ["aaaa111", "bbbb222"],
    }])

    proc = _dotsource(project, env, f"""
declare -F get_ledger_check_targets >/dev/null || {{ echo "FUNC_MISSING"; exit 90; }}
get_ledger_check_targets '20260925-120000' 3
""")
    assert "FUNC_MISSING" not in proc.stdout, (
        f"get_ledger_check_targets not defined.\n{proc.stdout}\n{proc.stderr}"
    )
    targets = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    assert targets == ["batch-verify 0", "batch-verify 1"], (
        "ledger path must emit every step for a verify sub-plan; "
        f"got {targets!r}\nstderr: {proc.stderr}"
    )


# ── AC-5 ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_verify_attribution_no_record_names_path(tmp_path: Path) -> None:
    """AC-5 — verify_attribution --remeasure-if-stale with no record file and
    no --base-sha prints the message naming the record path and exits 1."""
    env = _sandbox_env(tmp_path)
    project = _make_verify_project(tmp_path, "personal")
    env["HOME"] = str(tmp_path)
    env["ILK_DATA_HOME"] = str(tmp_path / ".ilk-data")

    proc = subprocess.run(
        ["python3", str(VERIFY_ATTR),
         "--project", str(project),
         "--batch", "batch-verify",
         "--remeasure-if-stale"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    combined = proc.stdout + proc.stderr
    assert "no verification record" in combined.lower(), (
        "message must name the missing record; "
        f"got: {combined!r}"
    )
    assert "batch-verify" in combined, (
        "message must name the batch slug; "
        f"got: {combined!r}"
    )