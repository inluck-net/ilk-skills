"""Pin the golden batch fixture, its stub, and the harness refusals.

AC-1..AC-3, ``xfail(strict=True)`` until step 1 adds the fixture and
harness.  Each test imports inside the test body so the module loads
even when the fixture does not yet exist.

AC-1: the fixture parses — ``plan_status.extract_subplan_files`` returns
      the four sub-plans in order; ``expected.json`` names all four.
AC-2: the stub, run by hand on a tmp copy, makes one commit carrying
      ``[plan:golden-real#step-0]`` and prints a stream-json ``init`` line.
AC-3: ``golden_batch.run`` refuses (exit 2) when ``PATH`` has no
      ``gtimeout``, and when the resolved ``ILK_SKILL_HOME`` would be
      inside this repo.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "golden"
_SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"

pytestmark = pytest.mark.timeout(120)


# ── AC-1: fixture parses ─────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="fixture does not exist yet (step 1)")
def test_fixture_parses_four_subplans_in_order() -> None:
    """AC-1: extract_subplan_files returns golden-real, golden-inert,
    golden-red, golden-verify in order; expected.json names all four."""
    sys.path.insert(0, str(_SCRIPTS))
    from plan_status import extract_subplan_files

    master = (_FIXTURES / "plans" / "MASTER-golden-execution-plan.md"
              .read_text(encoding="utf-8"))
    slugs = extract_subplan_files(master)
    assert slugs == [
        "golden-real",
        "golden-inert",
        "golden-red",
        "golden-verify",
    ], f"extract_subplan_files returned {slugs!r}"

    expected = json.loads(
        (_FIXTURES / "expected.json").read_text(encoding="utf-8")
    )
    for slug in ["golden-real", "golden-inert", "golden-red", "golden-verify"]:
        assert slug in expected, f"{slug!r} missing from expected.json"


# ── AC-2: stub makes a trailered commit ──────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="stub does not exist yet (step 1)")
def test_stub_makes_trailered_commit_and_prints_init(tmp_path: Path) -> None:
    """AC-2: run the stub by hand with ILK_ITERATION_SUBPLAN=golden-real
    and current_step: 0; it must commit with [plan:golden-real#step-0]
    and print a stream-json init line."""
    # Copy the fixture project to tmp.
    project = tmp_path / "project"
    shutil.copytree(_FIXTURES / "project", project)

    # Copy the stub.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    shutil.copy2(_FIXTURES / "stub-claude", stub)
    stub.chmod(0o755)

    # Set up plans dir with golden-real at current_step: 0.
    data_home = tmp_path / ".ilk-data"
    sys.path.insert(0, str(_SCRIPTS))
    import ilk_paths
    from unittest.mock import patch as _patch
    with _patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)
    shutil.copytree(_FIXTURES / "plans", plans, dirs_exist_ok=True)

    # Run the stub.
    env = {
        **os.environ,
        "ILK_ITERATION_SUBPLAN": "golden-real",
        "ILK_DATA_HOME": str(data_home),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    r = subprocess.run(
        [str(stub)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env, cwd=str(project),
    )
    assert r.returncode == 0, f"stub exited {r.returncode}.\n{r.stderr[-500:]}"

    # Check the commit.
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        capture_output=True, text=True, cwd=str(project),
    )
    assert "[plan:golden-real#step-0]" in log.stdout, (
        f"commit trailer missing.\nstdout={log.stdout}\nstderr={log.stderr}"
    )

    # Check the stream-json init line.
    assert '{"type":"system","subtype":"init"}' in r.stdout, (
        f"stream-json init line missing.\nstdout={r.stdout[-500:]}"
    )


# ── AC-3: harness refusals ───────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="harness does not exist yet (step 1)")
def test_golden_batch_refuses_without_gtimeout(tmp_path: Path) -> None:
    """AC-3a: golden_batch.run exits 2 when PATH has no gtimeout."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import golden_batch

    # Remove gtimeout from PATH.
    env = {k: v for k, v in os.environ.items() if k != "PATH"}
    env["PATH"] = "/usr/bin:/bin"  # no gtimeout here

    with pytest.raises(SystemExit) as exc_info:
        golden_batch.run(out=tmp_path / "out.json")
    assert exc_info.value.code == 2, (
        f"expected exit 2 for missing gtimeout, got {exc_info.value.code}"
    )


@pytest.mark.xfail(strict=True, reason="harness does not exist yet (step 1)")
def test_golden_batch_refuses_when_skill_home_in_repo(tmp_path: Path) -> None:
    """AC-3b: golden_batch.run exits 2 when ILK_SKILL_HOME resolves
    inside this repo (would pollute the live skills/)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import golden_batch

    # Point ILK_SKILL_HOME at the repo root (a git repo).
    env = {**os.environ, "ILK_SKILL_HOME": str(_REPO)}

    with pytest.raises(SystemExit) as exc_info:
        golden_batch.run(out=tmp_path / "out.json")
    assert exc_info.value.code == 2, (
        f"expected exit 2 for skill-home-in-repo, got {exc_info.value.code}"
    )