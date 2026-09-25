"""Red-first pins: a stale verify goes back to step 0.

Part of `a-stale-verify-goes-back-to-step-0` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 1 removes.

AC-1: a verify at ``current_step: 1`` with an old-form step 1 gate and a
      record whose tree ≠ HEAD ⇒ one iteration of the fast path runs step 0's
      gate (the fake suite runs once), then step 1's gate passes, with no
      worker invoked.
AC-2: a fresh record ⇒ step 0 is not re-run (the fake suite does not run).
AC-3: ``--is-stale`` exit codes 0/1 on stale/fresh fixtures; it never spawns
      the suite.
AC-4: plan_lint emits the WARN on an old-form fixture and not on a 25a-form
      one.
AC-5: a stale record with ``suite_scope: full`` and a gate without ``--scope``
      ⇒ the re-measure runs in full scope (the new record says
      ``suite_scope: full``).

Harness for AC-1/AC-2: same as ``test_gate_first_step.py`` — source the driver
under ``ILK_DOTSOURCE_ONLY=1`` and run its real ``main`` for one iteration.
The agent is stubbed via PATH.  For AC-1 the sentinel proves step 0 ran
before step 1.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent / "scripts"
_DRIVER = _SCRIPTS / "run_ilk_loop_claude.sh"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ilk_paths  # noqa: E402

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


class _scoped_data_home:
    """Pin ILK_DATA_HOME for a block (ilk_paths.project_key reads it)."""

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


# ── AC-1 world builder ──────────────────────────────────────────────────────


def _build_world_ac1(root: Path) -> dict:
    """Project + isolated data home + stub agent + stale record at step 1.

    Two gate-first steps.  Step 0's gate touches a sentinel to prove it ran.
    Step 1's gate is ``true`` (pass-through).  The record is stale (verified at
    HEAD~1, HEAD is ahead).  The gate-first path should detect the staleness,
    run step 0's gate first, then run step 1's gate.
    """
    project = root / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "base")
    base_sha = _git(project, "rev-parse", "HEAD")
    (project / "fix.py").write_text("x = 1\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "head")
    head_sha = _git(project, "rev-parse", "HEAD")

    data_home = root / ".ilk-data"
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    sentinel = root / "step0-ran"

    slug = "stale-verify"
    stem = f"2026-09-25-{slug}"

    (plans / f"MASTER-{slug}-execution-plan.md").write_text(
        "---\n"
        f"master_plan: {slug}-execution\n"
        "batch_date: 2026-09-25\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{slug}](./{stem}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{stem}.md").write_text(
        "---\n"
        f"plan: {slug}\n"
        "batch_verification: true\n"
        "status: in-progress\n"
        "current_step: 1\n"
        "estimated_steps: 2\n"
        "verification_tier: loop-verified\n"
        "local_checks: []\n"
        "---\n\n"
        f"# {slug}\n\n"
        "## Steps\n\n"
        "### Step 0 — run the suite\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        f"  - command: \"touch {sentinel}\"\n"
        "    timeout: 30\n"
        "```\n\n"
        "### Step 1 — verify attribution\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        f"  - command: \"true --batch batch-{slug}\"\n"
        "    timeout: 30\n"
        "```\n",
        encoding="utf-8",
    )

    # Write a stale record: verified at base_sha, HEAD is head_sha.
    vdir = (
        data_home / "projects" / key / "logs" / "verification"
    )
    vdir.mkdir(parents=True, exist_ok=True)
    record = vdir / f"batch-{slug}-batch.md"
    record.write_text(
        f"# Batch verification record — batch-{slug}\n\n"
        f"record_writer: verification_record.py\n"
        f"verified_head: {base_sha}\n"
        f"verified_tree: {_git(project, 'rev-parse', base_sha + '^{tree}')}\n"
        f"base_sha: {base_sha}\n"
        f"suite_invocation: echo ok\n"
        f"suite_scope: auto\n"
        f"suite_failed: 0\n\n"
        f"## At-base rerun\n\n_(no failures)_\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    counter = root / "agent-invocations.txt"
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo invoked >> {shlex.quote(str(counter))}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {
        "project": project,
        "plans": plans,
        "data_home": data_home,
        "key": key,
        "bin": bin_dir,
        "counter": counter,
        "root": root,
        "sentinel": sentinel,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "slug": slug,
        "stem": stem,
        "record": record,
    }


def _run_one_iteration(world: dict) -> subprocess.CompletedProcess:
    """Source the driver under ILK_DOTSOURCE_ONLY=1 and run its real main()."""
    script = f"""
export ILK_DOTSOURCE_ONLY=1
source {shlex.quote(str(_DRIVER))} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
main --project-path {shlex.quote(str(world["project"]))} \\
     --max-iterations 1 \\
     --iteration-timeout-min 1 \\
     --model test-model \\
     --run-local-checks
echo "MAIN_RC=$?"
"""
    env = {
        **os.environ,
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(world["root"]),
        "ILK_DATA_HOME": str(world["data_home"]),
    }
    env.pop("ILK_DATA_DIR", None)
    env.pop("ILK_DOTSOURCE_ONLY", None)
    (world["root"] / ".claude").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=env, cwd=str(world["root"]),
    )


def _read_subplan_status(world: dict) -> tuple[int, str]:
    """Return (current_step, status) from the sub-plan file on disk."""
    sub_file = world["plans"] / f"{world['stem']}.md"
    body = sub_file.read_text(encoding="utf-8")
    step_m = re.search(r"^current_step:[ \t]*(\d+)[ \t]*$", body, re.MULTILINE)
    status_m = re.search(r"^status:[ \t]*(\S+)[ \t]*$", body, re.MULTILINE)
    step = int(step_m.group(1)) if step_m else -1
    status = status_m.group(1) if status_m else "unknown"
    return step, status


# ── AC-1 (xfail): stale record ⇒ step 0 re-runs before step 1 ───────────────


@_NEEDS_GTIMEOUT
def test_stale_record_reruns_step0_before_step1(tmp_path: Path) -> None:
    """A verify at current_step: 1 with an old-form step 1 gate and a record
    whose tree ≠ HEAD ⇒ the gate-first fast path runs step 0's gate first
    (the fake suite runs once — sentinel exists), then step 1's gate passes,
    with no worker invoked.
    """
    world = _build_world_ac1(tmp_path)
    proc = _run_one_iteration(world)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    counter = world["counter"]
    sentinel = world["sentinel"]

    # No worker should have been invoked.
    assert not counter.exists(), (
        "the stub agent ran (counter file is present) for a stale verify "
        "whose step 0 should have been re-run by the driver.\n"
        f"invocations: {counter.read_text(encoding='utf-8') if counter.exists() else ''}\n"
        f"last 40 lines:\n{tail}"
    )

    # The gate-first fast path must have engaged for step 1.
    combined = proc.stdout + proc.stderr
    assert "declares gate_first: true" in combined, (
        "the gate-first fast path never engaged for step 1.\n"
        f"last 40 lines:\n{tail}"
    )

    # Step 0's gate must have run (sentinel exists).
    assert sentinel.exists(), (
        "step 0's gate did not run — the sentinel file is missing. "
        "The stale record should trigger a step-0 re-run before step 1.\n"
        f"last 40 lines:\n{tail}"
    )

    # The driver should have logged the stale-record re-run.
    assert "record stale" in combined.lower() or "re-running step 0" in combined.lower(), (
        "the driver did not log a stale-record re-run.\n"
        f"last 40 lines:\n{tail}"
    )

    assert re.search(r"MAIN_RC=", proc.stdout), (
        f"main() did not run to a recorded exit — harness failure.\n"
        f"last 40 lines:\n{tail}"
    )


# ── AC-2 (xfail): fresh record ⇒ step 0 is NOT re-run ───────────────────────


def _build_world_ac2(root: Path) -> dict:
    """Project + isolated data home + stub agent + fresh record at step 1.

    Same as AC-1 but the record's verified_head == HEAD (fresh).
    Step 0's gate touches a sentinel; step 1's gate is ``true``.
    If step 0 re-runs, the sentinel appears — that's the red state.
    """
    project = root / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-q")
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "base")
    base_sha = _git(project, "rev-parse", "HEAD")
    # No second commit — record will point at HEAD (fresh).
    head_sha = base_sha

    data_home = root / ".ilk-data"
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    sentinel = root / "step0-ran"

    slug = "fresh-verify"
    stem = f"2026-09-25-{slug}"

    (plans / f"MASTER-{slug}-execution-plan.md").write_text(
        "---\n"
        f"master_plan: {slug}-execution\n"
        "batch_date: 2026-09-25\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{slug}](./{stem}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{stem}.md").write_text(
        "---\n"
        f"plan: {slug}\n"
        "batch_verification: true\n"
        "status: in-progress\n"
        "current_step: 1\n"
        "estimated_steps: 2\n"
        "verification_tier: loop-verified\n"
        "local_checks: []\n"
        "---\n\n"
        f"# {slug}\n\n"
        "## Steps\n\n"
        "### Step 0 — run the suite\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        f"  - command: \"touch {sentinel}\"\n"
        "    timeout: 30\n"
        "```\n\n"
        "### Step 1 — verify attribution\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        f"  - command: \"true --batch batch-{slug}\"\n"
        "    timeout: 30\n"
        "```\n",
        encoding="utf-8",
    )

    # Write a FRESH record: verified at HEAD.
    vdir = (
        data_home / "projects" / key / "logs" / "verification"
    )
    vdir.mkdir(parents=True, exist_ok=True)
    record = vdir / f"batch-{slug}-batch.md"
    record.write_text(
        f"# Batch verification record — batch-{slug}\n\n"
        f"record_writer: verification_record.py\n"
        f"verified_head: {head_sha}\n"
        f"verified_tree: {_git(project, 'rev-parse', head_sha + '^{tree}')}\n"
        f"base_sha: {base_sha}\n"
        f"suite_invocation: echo ok\n"
        f"suite_scope: auto\n"
        f"suite_failed: 0\n\n"
        f"## At-base rerun\n\n_(no failures)_\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    counter = root / "agent-invocations.txt"
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo invoked >> {shlex.quote(str(counter))}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {
        "project": project,
        "plans": plans,
        "data_home": data_home,
        "key": key,
        "bin": bin_dir,
        "counter": counter,
        "root": root,
        "sentinel": sentinel,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "slug": slug,
        "stem": stem,
        "record": record,
    }


@_NEEDS_GTIMEOUT
def test_fresh_record_does_not_rerun_step0(tmp_path: Path) -> None:
    """A fresh record (verified_head == HEAD) ⇒ step 0's gate does NOT re-run.
    The sentinel file (created by step 0's gate) must be absent.

    RED STATE: the sentinel exists, meaning step 0 was re-run unnecessarily
    for a fresh record.  The gate-first path must have engaged (positive
    control).
    """
    world = _build_world_ac2(tmp_path)
    proc = _run_one_iteration(world)

    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    sentinel = world["sentinel"]

    # Positive control: the gate-first fast path must have engaged for step 1.
    combined = proc.stdout + proc.stderr
    assert "declares gate_first: true" in combined, (
        "the gate-first fast path never engaged for step 1 — the test is "
        "vacuous (an absent sentinel means the path never ran, not that it "
        "skipped step 0).\n"
        f"last 40 lines:\n{tail}"
    )

    # Step 0's sentinel must NOT exist — the record is fresh and step 0
    # should not re-run.
    assert not sentinel.exists(), (
        "step 0's sentinel exists — the record is fresh but step 0 was "
        "re-run unnecessarily.  The stale-record fallback must skip "
        "step 0 when the record is fresh.\n"
        f"last 40 lines:\n{tail}"
    )


# ── AC-3 (xfail): --is-stale exit codes ─────────────────────────────────────


def test_is_stale_exits_zero_on_stale_record(tmp_path: Path) -> None:
    """``verify_attribution.py --is-stale --batch X`` exits 0 when the record
    is stale (verified_head != HEAD).  It never spawns the suite.
    """
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts))
    import verify_attribution as vat

    # Set up a project with two commits.
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    _git(project, "commit", "-q", "--allow-empty", "-m", "base")
    base_sha = _git(project, "rev-parse", "HEAD")
    # HEAD must change the TREE: a head-only move (an empty marker commit)
    # is not staleness -- see test_marker_commit_is_not_stale.
    (project / "fix.py").write_text("x = 1\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "head")
    head_sha = _git(project, "rev-parse", "HEAD")

    # Pin ILK_DATA_HOME and HOME.
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    os.environ["ILK_DATA_HOME"] = str(data_home)
    os.environ["HOME"] = str(home)

    # Write a stale record.
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(project)
    vdir = data_home / "projects" / key / "logs" / "verification"
    vdir.mkdir(parents=True, exist_ok=True)
    record = vdir / "batch-stale-batch.md"
    record.write_text(
        "# Batch verification record — batch-stale\n\n"
        "record_writer: verification_record.py\n"
        f"verified_head: {base_sha}\n"
        f"verified_tree: {_git(project, 'rev-parse', base_sha + '^{tree}')}\n"
        f"base_sha: {base_sha}\n"
        "suite_invocation: echo ok\n"
        "suite_scope: auto\n"
        "suite_failed: 0\n\n"
        "## At-base rerun\n\n_(no failures)_\n",
        encoding="utf-8",
    )

    sentinel = tmp_path / "sentinel"
    ret = vat.main([
        "--is-stale",
        "--project", str(project),
        "--batch", "batch-stale",
    ])
    assert ret == 0, (
        f"--is-stale should exit 0 on a stale record, got {ret}"
    )
    assert not sentinel.exists(), (
        "--is-stale must not spawn the suite (sentinel exists)"
    )


def test_is_stale_exits_one_on_fresh_record(tmp_path: Path) -> None:
    """``verify_attribution.py --is-stale --batch X`` exits 1 when the record
    is fresh (verified_head == HEAD).  It never spawns the suite.
    """
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts))
    import verify_attribution as vat

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    _git(project, "commit", "-q", "--allow-empty", "-m", "base")
    base_sha = _git(project, "rev-parse", "HEAD")

    data_home = tmp_path / "data"
    home = tmp_path / "home"
    os.environ["ILK_DATA_HOME"] = str(data_home)
    os.environ["HOME"] = str(home)

    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(project)
    vdir = data_home / "projects" / key / "logs" / "verification"
    vdir.mkdir(parents=True, exist_ok=True)
    record = vdir / "batch-fresh-batch.md"
    record.write_text(
        "# Batch verification record — batch-fresh\n\n"
        "record_writer: verification_record.py\n"
        f"verified_head: {base_sha}\n"
        f"verified_tree: {_git(project, 'rev-parse', base_sha + '^{tree}')}\n"
        f"base_sha: {base_sha}\n"
        "suite_invocation: echo ok\n"
        "suite_scope: auto\n"
        "suite_failed: 0\n\n"
        "## At-base rerun\n\n_(no failures)_\n",
        encoding="utf-8",
    )

    sentinel = tmp_path / "sentinel"
    ret = vat.main([
        "--is-stale",
        "--project", str(project),
        "--batch", "batch-fresh",
    ])
    assert ret == 1, (
        f"--is-stale should exit 1 on a fresh record, got {ret}"
    )
    assert not sentinel.exists(), (
        "--is-stale must not spawn the suite (sentinel exists)"
    )


def test_marker_commit_is_not_stale(tmp_path: Path) -> None:
    """An empty marker commit after the record moves HEAD but not the tree, so
    the record is FRESH: ``--is-stale`` exits 1 and ``--remeasure-if-stale``
    would not re-run the suite.

    Regression (2026-09-25): a head comparison re-ran the suite on an unchanged
    tree in 3 of 8 measurements of batches 25a/25b, because gate-first commits
    an empty marker after every step."""
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts))
    import verify_attribution as vat

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    (project / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "code")
    measured = _git(project, "rev-parse", "HEAD")
    _git(project, "commit", "-q", "--allow-empty", "-m",
         "chore(loop): gate-first marker [plan:x#step-0]")
    assert _git(project, "rev-parse", "HEAD") != measured

    data_home = tmp_path / "data"
    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(project)
        vdir = data_home / "projects" / key / "logs" / "verification"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "batch-marker-batch.md").write_text(
            "# Batch verification record — batch-marker\n\n"
            "record_writer: verification_record.py\n"
            f"verified_head: {measured}\n"
            f"verified_tree: {_git(project, 'rev-parse', measured + '^{tree}')}\n"
            f"base_sha: {measured}\n"
            "suite_invocation: echo ok\n"
            "suite_scope: auto\n"
            "suite_failed: 0\n\n"
            "## At-base rerun\n\n_(no failures)_\n",
            encoding="utf-8",
        )
        ret = vat.main(["--is-stale", "--project", str(project),
                        "--batch", "batch-marker"])
    assert ret == 1, f"a marker-only HEAD move must read fresh, got {ret}"


# ── AC-4 (xfail): plan_lint WARN on old-form step 1 ─────────────────────────


def test_plan_lint_warns_on_old_form_step1() -> None:
    """A ``batch_verification: true`` sub-plan whose step 1 gate calls
    ``verify_attribution.py`` without ``--remeasure-if-stale`` gets a WARN.
    """
    sys.path.insert(0, str(_SCRIPTS))
    import plan_lint

    body = (
        "---\n"
        "plan: old-form-verify\n"
        "batch_verification: true\n"
        "status: in-progress\n"
        "current_step: 1\n"
        "estimated_steps: 2\n"
        "verification_tier: loop-verified\n"
        "local_checks: []\n"
        "---\n\n"
        "# old-form-verify\n\n"
        "## Steps\n\n"
        "### Step 0 — run the suite\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        "  - command: \"python3 skills/ilk-loop/scripts/verification_record.py "
        "--project . --batch batch-old --base-sha abc --run-suite\"\n"
        "    timeout: 1800\n"
        "```\n\n"
        "### Step 1 — verify attribution\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        "  - command: \"python3 skills/ilk-loop/scripts/verify_attribution.py "
        "--project . --batch batch-old\"\n"
        "    timeout: 1800\n"
        "```\n"
    )

    findings = plan_lint.lint_subplan(body, "old-form-verify")
    warn_texts = [f for f in findings if "WARN" in f.upper()]
    assert any("remeasure-if-stale" in w.lower() for w in warn_texts), (
        f"expected a WARN about --remeasure-if-stale on old-form step 1, "
        f"got: {findings}"
    )


def test_plan_lint_no_warn_on_25a_form_step1() -> None:
    """A ``batch_verification: true`` sub-plan whose step 1 gate calls
    ``verify_attribution.py --remeasure-if-stale`` does NOT get the WARN.
    """
    sys.path.insert(0, str(_SCRIPTS))
    import plan_lint

    body = (
        "---\n"
        "plan: new-form-verify\n"
        "batch_verification: true\n"
        "status: in-progress\n"
        "current_step: 1\n"
        "estimated_steps: 2\n"
        "verification_tier: loop-verified\n"
        "local_checks: []\n"
        "---\n\n"
        "# new-form-verify\n\n"
        "## Steps\n\n"
        "### Step 0 — run the suite\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        "  - command: \"python3 skills/ilk-loop/scripts/verification_record.py "
        "--project . --batch batch-new --base-sha abc --run-suite\"\n"
        "    timeout: 1800\n"
        "```\n\n"
        "### Step 1 — verify attribution\n\n"
        "```yaml\n"
        "gate_first: true\n"
        "local_checks:\n"
        "  - command: \"python3 skills/ilk-loop/scripts/verify_attribution.py "
        "--project . --batch batch-new --remeasure-if-stale\"\n"
        "    timeout: 1800\n"
        "```\n"
    )

    findings = plan_lint.lint_subplan(body, "new-form-verify")
    warn_texts = [f for f in findings if "WARN" in f.upper()]
    assert not any("remeasure-if-stale" in w.lower() for w in warn_texts), (
        f"expected NO WARN about --remeasure-if-stale on a 25a-form step 1, "
        f"got: {findings}"
    )


# ── AC-5 (xfail): stale record + suite_scope: full + no --scope ⇒ full ──────


def test_stale_record_with_suite_scope_full_respects_scope(tmp_path: Path) -> None:
    """A stale record with ``suite_scope: full`` and a gate without ``--scope``
    ⇒ the re-measure runs in full scope (the new record says
    ``suite_scope: full``).
    """
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts))
    import verification_record as vr
    import verify_attribution as vat

    # Project with two commits.
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    _git(project, "commit", "-q", "--allow-empty", "-m", "base")
    base_sha = _git(project, "rev-parse", "HEAD")
    # HEAD must change the TREE: a head-only move (an empty marker commit)
    # is not staleness -- see test_marker_commit_is_not_stale.
    (project / "fix.py").write_text("x = 1\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "head")
    head_sha = _git(project, "rev-parse", "HEAD")

    # Fake suite script that prints pytest-like output.
    sentinel = tmp_path / "sentinel"
    script = tmp_path / "fake-suite.sh"
    script.write_text(
        f"#!/bin/sh\ntouch {sentinel}\n"
        f"echo '====== 1 passed in 0.01s ======'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    # .ilk-launch.json pointing at the fake suite.
    (project / ".ilk-launch.json").write_text(
        json.dumps({"ship": {"suite": {"command": str(script)}}}),
        encoding="utf-8",
    )

    data_home = tmp_path / "data"
    home = tmp_path / "home"
    os.environ["ILK_DATA_HOME"] = str(data_home)
    os.environ["HOME"] = str(home)

    with _scoped_data_home(data_home):
        key = ilk_paths.project_key(project)
    vdir = data_home / "projects" / key / "logs" / "verification"
    vdir.mkdir(parents=True, exist_ok=True)
    record = vdir / "batch-scope-batch.md"

    # Write a stale record with suite_scope: full.
    record.write_text(
        "# Batch verification record — batch-scope\n\n"
        "record_writer: verification_record.py\n"
        f"verified_head: {base_sha}\n"
        f"verified_tree: {_git(project, 'rev-parse', base_sha + '^{tree}')}\n"
        f"base_sha: {base_sha}\n"
        "suite_invocation: echo ok\n"
        "suite_scope: full\n"
        "suite_failed: 0\n\n"
        "## At-base rerun\n\n_(no failures)_\n",
        encoding="utf-8",
    )

    # Re-measure without --scope (defaults to "auto").
    # The record's suite_scope is "full", so the re-measure should use full.
    # Unset ILK_WORKER_SESSION so the re-measurement is not refused
    # (this test may run inside a worker session).
    saved_worker = os.environ.pop("ILK_WORKER_SESSION", None)
    try:
        ret = vat.main([
            str(record),
            "--project", str(project),
            "--remeasure-if-stale",
        ])
    finally:
        if saved_worker is not None:
            os.environ["ILK_WORKER_SESSION"] = saved_worker

    # The suite should have run.
    assert sentinel.exists(), (
        "the suite should have been re-run (sentinel missing)"
    )

    # The updated record should say suite_scope: full.
    text = record.read_text(encoding="utf-8")
    m = re.search(r"^suite_scope:\s*(\S+)", text, re.MULTILINE)
    assert m and m.group(1) == "full", (
        f"expected suite_scope: full in the re-measured record, "
        f"got {m.group(1) if m else 'not found'}"
    )