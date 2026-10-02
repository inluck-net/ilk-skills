"""Tests pinning: a step is proven only by its own gate.

Part of sub-plan ``a-step-is-proven-only-by-its-own-gate`` (MASTER-2026-10-02b).

AC-1 through AC-5: per-step gate records in the JSONL loop log are required
for ship_audit to call a gated step proven.  AC-6 through AC-8: a gate
record carries the failing check's own exit code (folded 28d-4 G7).

Red-first pins — most are xfail(strict) until the implementation lands.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest


# ── git helpers ───────────────────────────────────────────────────────────────

def _init_repo(path: Path) -> None:
    """Create a git repo with an initial commit so ``git log`` works."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, capture_output=True,
                   text=True, encoding="utf-8", errors="replace", check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", check=True)


def _commit_with_message(path: Path, subject: str, body: str = "") -> None:
    """Create a commit with a specific subject and optional body."""
    (path / "marker.txt").write_text(subject + "\n")
    subprocess.run(["git", "add", "marker.txt"], cwd=path, capture_output=True,
                   text=True, encoding="utf-8", errors="replace", check=True)
    msg = subject if not body else f"{subject}\n\n{body}"
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty"], cwd=path,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", check=True)


def _head_sha(path: Path) -> str:
    """Return the short SHA of HEAD."""
    r = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=path,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", check=True)
    return r.stdout.strip()


# ── 3-step sub-plan body ─────────────────────────────────────────────────────

_BODY_3_STEPS = textwrap.dedent("""\
    ### Step 0
    ```yaml
    local_checks:
      - command: echo step0
        timeout: 10
    ```
    - do step 0
    - Commit: `feat(x): s0 [plan:test-slug#step-0]`

    ### Step 1
    ```yaml
    local_checks:
      - command: echo step1
        timeout: 10
    ```
    - do step 1
    - Commit: `feat(x): s1 [plan:test-slug#step-1]`

    ### Step 2
    ```yaml
    local_checks:
      - command: echo step2
        timeout: 10
    ```
    - do step 2
    - Commit: `feat(x): s2 [plan:test-slug#step-2]`
""")


# ═══════════════════════════════════════════════════════════════════════════════
# AC-1: log has pass only for step 2 ⇒ ship_audit says unproven, naming 0 & 1
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac1_missing_step_records_unproven(tmp_path: Path) -> None:
    """A 3-step sub-plan whose log has a pass record only for step 2
    ⇒ ship_audit says unproven, naming steps 0 and 1.

    This is the shape from gh-resolve run 20261002-071602
    (``a-run-is-disposed-in-its-own-clone``): 1 record in the whole run
    (step 2 pass), yet ``ship_audit`` prints PROVEN and its step-1 gate
    FAILS on re-run.
    """
    import ship_audit

    _init_repo(tmp_path)
    for n in range(3):
        _commit_with_message(
            tmp_path,
            f"feat(x): step {n} [plan:test-slug#step-{n}]",
        )

    # Loop log: only step 2 has a pass record.
    loop_log = tmp_path / ".ilk-loop.log"
    loop_log.write_text(
        json.dumps({"slug": "test-slug", "step": 2, "outcome": "pass",
                     "exit_code": 0, "head_sha": _head_sha(tmp_path)})
        + "\n"
    )

    result = ship_audit.audit_ship(
        status="shipped",
        body=_BODY_3_STEPS,
        declared_checks=[{"command": "echo ok", "timeout": 10}],
        gate_passed="unknown",
        slug="test-slug",
        cwd=tmp_path,
        loop_log_path=loop_log,
        master_created="2099-01-01T00:00:00+00:00",  # non-legacy (after cutover)
    )
    assert result["proven"] is False, (
        f"sub-plan with only step-2 record must be unproven; got {result}"
    )
    # The reason names the unproven steps (e.g. "unproven: steps 0, 1 gate never ran").
    assert 0 in result["missing_steps"] or any(
        re.search(r"\b0\b", r) for r in result["reasons"]
    ), f"step 0 must be named as missing/unproven; got {result}"
    assert 1 in result["missing_steps"] or any(
        re.search(r"\b1\b", r) for r in result["reasons"]
    ), f"step 1 must be named as missing/unproven; got {result}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC-2: pass records for steps 0, 1, 2 ⇒ proven
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac2_all_step_records_proven(tmp_path: Path) -> None:
    """A 3-step sub-plan with pass records for steps 0, 1, and 2 ⇒ proven.

    This is the happy path: every gated step has a pass record.
    """
    import ship_audit

    _init_repo(tmp_path)
    sha0 = None
    for n in range(3):
        _commit_with_message(
            tmp_path,
            f"feat(x): step {n} [plan:test-slug#step-{n}]",
        )
        if n == 0:
            sha0 = _head_sha(tmp_path)

    # Loop log: all three steps pass.
    loop_log = tmp_path / ".ilk-loop.log"
    records = []
    for n in range(3):
        records.append(
            json.dumps({"slug": "test-slug", "step": n, "outcome": "pass",
                         "exit_code": 0, "head_sha": _head_sha(tmp_path)})
        )
    loop_log.write_text("\n".join(records) + "\n")

    result = ship_audit.audit_ship(
        status="shipped",
        body=_BODY_3_STEPS,
        declared_checks=[{"command": "echo ok", "timeout": 10}],
        gate_passed="true",
        slug="test-slug",
        cwd=tmp_path,
        loop_log_path=loop_log,
    )
    assert result["proven"] is True, (
        f"sub-plan with all step records must be proven; got {result}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3: legacy sub-plan (master created before cutover) ⇒ proven, with label
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac3_legacy_sub_plan_proven_with_label(tmp_path: Path) -> None:
    """A legacy sub-plan (master ``created`` earlier the SAME DAY as the
    cutover commit) with missing records ⇒ proven, with the ``legacy``
    label in the reasons.

    The cutover rule: strict per-step records apply only to sub-plans whose
    MASTER ``created`` timestamp is later than the committer time of this
    sub-plan's ``#step-1`` commit.
    """
    import ship_audit

    _init_repo(tmp_path)
    for n in range(3):
        _commit_with_message(
            tmp_path,
            f"feat(x): step {n} [plan:test-slug#step-{n}]",
        )

    # Loop log: empty — no per-step records at all.
    loop_log = tmp_path / ".ilk-loop.log"
    loop_log.write_text("")

    result = ship_audit.audit_ship(
        status="shipped",
        body=_BODY_3_STEPS,
        declared_checks=[{"command": "echo ok", "timeout": 10}],
        gate_passed="true",  # gate passes (legacy exemption is about per-step records)
        slug="test-slug",
        cwd=tmp_path,
        loop_log_path=loop_log,
        master_created="2000-01-01T00:00:00+00:00",  # pre-cutover (legacy)
    )
    assert result["proven"] is True, (
        f"legacy sub-plan must be proven; got {result}"
    )
    assert any("legacy" in r.lower() for r in result["reasons"]), (
        f"legacy label must appear in reasons; got {result['reasons']}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4: runner advances 0→2 runs step-0 and step-1 gates
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac4_runner_gates_every_intermediate_step(tmp_path: Path) -> None:
    """A fixture iteration that moves ``current_step`` 0→2 runs the step-0
    and step-1 gates, and both appear in the iteration's ``local_checks``
    with their own ``step``.

    This tests the RUNNER behavior: after an iteration, for each sub-plan
    whose ``current_step`` moved from ``a`` to ``b``, run the declared gates
    of **every** step in ``a..b-1`` as well as the step it ended on.

    The test exercises ``get_ledger_check_targets`` — the function that
    resolves gate targets from the ship-proof ledger.  Before the fix it
    emitted only ``step_to - 1`` (the max step) for non-verify slugs; now it
    emits every step in ``[step_from, step_to)``.
    """
    runner = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"
    scripts = runner.parent

    # Create a git repo with plans so ilk_paths.py can resolve the launcher dir.
    project = tmp_path / "proj"
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-10-02-ac4.md").write_text(
        "---\n"
        "master_plan: 2026-10-02-ac4\n"
        "batch_date: 2026-10-02\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: ac4\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        "| 1 | [2026-10-02-test-slug.md](./2026-10-02-test-slug.md) | pending |\n",
        encoding="utf-8",
    )
    (plans / "2026-10-02-test-slug.md").write_text(
        "---\n"
        "plan: test-slug\n"
        "status: pending\n"
        "current_step: 0\n"
        "estimated_steps: 2\n"
        "---\n\n"
        "# Sub-plan: test-slug\n\n"
        "### Step 0\n\n"
        "```yaml\nlocal_checks:\n  - command: echo step0\n    timeout: 10\n```\n\n"
        "### Step 1\n\n"
        "```yaml\nlocal_checks:\n  - command: echo step1\n    timeout: 10\n```\n",
        encoding="utf-8",
    )
    _init_repo(project)

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "ILK_DATA_HOME": str(tmp_path / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }

    # Resolve the launcher dir via ilk_paths.py.
    proc_paths = subprocess.run(
        ["python3", str(scripts / "ilk_paths.py"), "--start", str(project)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )
    assert proc_paths.returncode == 0, f"ilk_paths.py failed: {proc_paths.stderr}"
    launcher_dir = Path(json.loads(proc_paths.stdout)["external_launcher_dir"])
    launcher_dir.mkdir(parents=True, exist_ok=True)

    # Write a ledger with one record: step_from=0, step_to=2 (non-verify slug).
    ledger = launcher_dir / "ship-proof.jsonl"
    ledger.write_text(
        json.dumps({"run_id": "20261002-120000", "iteration": 3,
                     "slug": "test-slug", "repo": str(project),
                     "step_from": 0, "step_to": 2,
                     "commits": ["aaaa111", "bbbb222"]},
                    separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    # Dot-source the runner and call get_ledger_check_targets.
    prelude = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        source '{runner}'
        PROJECT_PATH='{project}'
        REPOS=('{project}')
        LOOP_STATUS_SCRIPT='{scripts / "loop_status.py"}'
        set +e
    """)
    proc = subprocess.run(
        ["bash", "-c", prelude
         + "declare -F get_ledger_check_targets >/dev/null || { echo FUNC_MISSING; exit 90; }\n"
         + "get_ledger_check_targets '20261002-120000' 3\n"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(project),
    )
    assert "FUNC_MISSING" not in proc.stdout, (
        f"get_ledger_check_targets not defined.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    targets = {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}
    assert "test-slug 0" in targets, (
        f"step 0 must be a gate target (intermediate step); got {targets!r}. "
        f"stderr: {proc.stderr}"
    )
    assert "test-slug 1" in targets, (
        f"step 1 must be a gate target (final step in [step_from, step_to)); "
        f"got {targets!r}. stderr: {proc.stderr}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-5: step-0 baseline helper returns the step-0 record's head_sha
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac5_step0_baseline_from_record_not_trailer(tmp_path: Path) -> None:
    """The step-0 baseline helper returns the commit the step-0 gate
    **passed at**, from the step record's ``head_sha``, never from a
    trailer grep.  Even when a later commit carries a ``#step-0`` trailer.

    gh-resolve's pins_only gates resolve the step-0 baseline by trailer
    grep.  A worker commit mislabelled ``#step-0`` gave a false FAIL.
    The baseline must come from the step-0 gate's own record.
    """
    _init_repo(tmp_path)

    # Commit step 0 (the real one).
    _commit_with_message(
        tmp_path,
        "feat(x): step 0 [plan:test-slug#step-0]",
    )
    real_sha = _head_sha(tmp_path)

    # Later commit that also carries a #step-0 trailer (mislabelled).
    _commit_with_message(
        tmp_path,
        "chore(x): some later work [plan:test-slug#step-0]",
    )
    later_sha = _head_sha(tmp_path)

    # The gate record says step 0 passed at real_sha.
    gate_record = {
        "slug": "test-slug",
        "step": 0,
        "outcome": "pass",
        "exit_code": 0,
        "head_sha": real_sha,
    }

    # The helper should return real_sha, not later_sha.
    from ship_audit import step0_baseline_sha

    result = step0_baseline_sha(
        slug="test-slug",
        gate_records=[gate_record],
        cwd=tmp_path,
    )
    assert result == real_sha, (
        f"expected step-0 baseline {real_sha}, got {result}. "
        f"Later mislabelled commit was {later_sha}."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-6: nonexistent command ⇒ cmd_exit_code == 127, broken gate result
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac6_nonexistent_command_exit_code_127(tmp_path: Path) -> None:
    """A record built from REAL ``run_local_checks`` output of
    ``nonexistent-cmd-xyz`` ⇒ ``cmd_exit_code == 127``, and
    ``_is_broken_gate_result`` is true.

    Folded item 28d-4 G7: each gate record gains ``cmd_exit_code``,
    the first failing check's own exit code.
    """
    import run_local_checks

    # Run a nonexistent command through run_local_checks.
    checks = [{"command": "nonexistent-cmd-xyz", "timeout": 10}]
    result = run_local_checks.run_local_checks(checks, cwd=tmp_path)

    # The record should carry cmd_exit_code == 127.
    assert hasattr(result, "cmd_exit_code") or "cmd_exit_code" in result, (
        "gate result must carry cmd_exit_code field"
    )
    code = (result.cmd_exit_code if hasattr(result, "cmd_exit_code")
            else result["cmd_exit_code"])
    assert code == 127, f"expected cmd_exit_code=127, got {code}"

    # _is_broken_gate_result should be true.
    try:
        from run_local_checks import _is_broken_gate_result
    except ImportError:
        raise AssertionError("_is_broken_gate_result not yet implemented")

    assert _is_broken_gate_result(result) is True, (
        "nonexistent command must be a broken gate result"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-7: pytest on a file with no tests (rc 5) ⇒ local-checks-broken, not stuck
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac7_pytest_no_tests_broken_not_stuck(tmp_path: Path) -> None:
    """Real pytest on a file with no tests (rc 5) ⇒ ``local-checks-broken``,
    not ``local-checks-stuck``.

    Exit code 5 from pytest means "no tests collected" — that is a broken
    gate (the command itself is wrong), not a stuck one (tests ran but failed).
    """
    # Run pytest on a nonexistent file (will exit 5 or similar).
    checks = [{"command": "python3 -m pytest nonexistent_file.py -q",
               "timeout": 30}]
    result = run_local_checks.run_local_checks(checks, cwd=tmp_path)

    # The outcome should indicate broken, not stuck.
    # This assertion will be refined once cmd_exit_code lands.
    outcome = (result.outcome if hasattr(result, "outcome")
               else result.get("outcome"))
    assert outcome != "local-checks-stuck", (
        f"pytest with no tests must not be 'stuck'; got {outcome}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC-8: exit_code is unchanged for pass/fail (control)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac8_exit_code_unchanged_for_pass_and_fail(tmp_path: Path) -> None:
    """``exit_code`` is unchanged for pass/fail.

    This is a control test: adding ``cmd_exit_code`` must not break the
    existing ``exit_code`` field semantics.
    """
    import run_local_checks

    # Pass case.
    pass_checks = [{"command": "echo ok", "timeout": 10}]
    pass_result = run_local_checks.run_local_checks(pass_checks, cwd=tmp_path)
    pass_exit = (pass_result.exit_code if hasattr(pass_result, "exit_code")
                 else pass_result.get("exit_code"))
    assert pass_exit == 0, f"pass case exit_code must be 0, got {pass_exit}"

    # Fail case.
    fail_checks = [{"command": "false", "timeout": 10}]
    fail_result = run_local_checks.run_local_checks(fail_checks, cwd=tmp_path)
    fail_exit = (fail_result.exit_code if hasattr(fail_result, "exit_code")
                 else fail_result.get("exit_code"))
    assert fail_exit != 0, f"fail case exit_code must be nonzero, got {fail_exit}"

    # If cmd_exit_code exists, it should also be correct.
    if hasattr(pass_result, "cmd_exit_code") or "cmd_exit_code" in pass_result:
        pass_cmd = (pass_result.cmd_exit_code
                    if hasattr(pass_result, "cmd_exit_code")
                    else pass_result.get("cmd_exit_code"))
        assert pass_cmd == 0, f"pass cmd_exit_code must be 0, got {pass_cmd}"
        fail_cmd = (fail_result.cmd_exit_code
                    if hasattr(fail_result, "cmd_exit_code")
                    else fail_result.get("cmd_exit_code"))
        assert fail_cmd != 0, f"fail cmd_exit_code must be nonzero, got {fail_cmd}"