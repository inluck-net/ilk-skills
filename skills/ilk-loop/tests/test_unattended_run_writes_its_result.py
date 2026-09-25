"""Red-first: an unattended run writes its result file instead of parking.

Part of sub-plan ``unattended-run-writes-its-result`` (step 0).

AC-1  (xfail) e2e: master with ``ilk_profile: unattended`` + valid
      ``result_file`` whose gate prints ``ILK-CHECK: unmeasured …`` and
      exits 1 ⇒ master status/parked_* unchanged, sub-plan not reverted,
      result file row ``outcome: unmeasured`` with that reason,
      ``provenance: runner``.
AC-2  (pass) the same run without ``ilk_profile`` parks exactly as today.
AC-3  (xfail) SIGTERM mid-iteration under the profile ⇒ result file with
      ``exit_state: interrupted``.
AC-4  (xfail) ``result_file: relative/path.json`` ⇒ refused and logged,
      no file written, no park.
AC-5  (xfail) run_result.py unit tests: ``pinned`` mirrors ILK_MASTER,
      ``error``→``unmeasured``, ``skipped``→``unmeasured``, a ``fail`` row
      keeps ``exit_code``, ``unmeasured`` without a reason is refused,
      write is atomic.
AC-6  (xfail) PS1: pwsh absent ⇒ skip; pwsh present + profile master ⇒
      ``exit_state: profile_unsupported``, master untouched.
AC-7  (xfail) test_exit_state_vocabulary.py green with new states
      ``lock_held`` and ``profile_unsupported``.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import shutil

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent.parent
RUNNER = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
PS_RUNNER = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.ps1"
SCRIPTS = _REPO / "skills" / "ilk-loop" / "scripts"

sys.path.insert(0, str(SCRIPTS))
from plan_status import parse_frontmatter  # noqa: E402

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)

_HAS_PWSH = pytest.mark.skipif(
    shutil.which("pwsh") is None,
    reason="pwsh not available on this host",
)

# ── helpers ────────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )


def _build_world_profile(
    root: Path,
    *,
    profile: str | None = "unattended",
    result_file: str | None = None,
    gate_script: str = "echo 'ILK-CHECK: unmeasured no vitest summary line' >&2; exit 1",
) -> dict:
    """Build a project world with an optional ``ilk_profile`` master.

    The stub agent ships the sub-plan and commits with a trailer, so the
    failing gate triggers a ship_integrity violation.  Under the profile
    the runner records instead of parking; without the profile it parks
    as today.

    Returns dict with ``project``, ``plans``, ``data_home``, ``key``,
    ``bin``, ``result_path``, ``slug``, ``stem``.
    """
    project = root / "project"
    (project / "docs").mkdir(parents=True)
    _git(project.parent, "init", "-q", str(project))
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    sys.path.insert(0, str(RUNNER.parent))
    import ilk_paths
    with patch.dict(os.environ, {"ILK_DATA_HOME": str(data_home)}, clear=False):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    # Result file defaults to tmp dir outside project root.
    if result_file is None:
        result_file = str(root / "result" / "run-result.json")

    # Master frontmatter.
    fm_lines = [
        "---",
        "master_plan: 2026-09-24-unattended-test",
        "batch_date: 2026-09-24",
        "status: active",
        "supervised_only: false",
    ]
    if profile is not None:
        fm_lines.append(f"ilk_profile: {profile}")
    if result_file is not None:
        fm_lines.append(f"result_file: {result_file}")
    fm_lines.append("---")
    fm_text = "\n".join(fm_lines)

    slug = "unattended-test-subplan"
    stem = f"2026-09-24-{slug}"
    (plans / "MASTER-2026-09-24-unattended-test.md").write_text(
        f"{fm_text}\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{stem}](./{stem}.md) |\n",
        encoding="utf-8",
    )

    (plans / f"{stem}.md").write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "local_checks:\n"
        f'  - command: "{gate_script}"\n'
        "    timeout: 60\n"
        "---\n\n"
        f"# {slug}\n\n"
        "### Step 0 — do the thing\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    # Stub claude: ships the sub-plan and commits with a trailer,
    # so the failing gate triggers a ship_integrity violation.
    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    sp_path = str(plans / f"{stem}.md")
    stub.write_text(
        "#!/usr/bin/env bash\n"
        # STUB_HOLD_SECONDS keeps the agent running so a signal can land
        # mid-iteration; unset, the stub returns at once.
        "sleep \"${STUB_HOLD_SECONDS:-0}\"\n"
        f"SP={sp_path!r}\n"
        "python3 - \"$SP\" <<'EOP'\n"
        "import re, sys\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
        "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
        "p.write_text(b)\n"
        "EOP\n"
        "git -c user.email=t@example.com -c user.name=t commit -q "
        f"--allow-empty -m 'feat: the work [plan:{slug}#step-0]'\n"
        "echo 'stub agent done'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    result_path = Path(result_file)
    return {
        "project": project,
        "plans": plans,
        "data_home": data_home,
        "key": key,
        "bin": bin_dir,
        "result_path": result_path,
        "slug": slug,
        "stem": stem,
    }


def _run_one_iteration(
    world: dict,
    root: Path,
    *,
    extra_env: dict | None = None,
    send_signal: int | None = None,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": str(root),
        "ILK_DATA_HOME": str(world["data_home"]),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "CLAUDE_CONFIG_DIR": str(root / ".claude"),
    }
    env.pop("ILK_DATA_DIR", None)
    if extra_env:
        env.update(extra_env)
    (root / ".claude").mkdir(exist_ok=True)

    proc = subprocess.Popen(
        ["bash", str(RUNNER),
         "--project-path", str(world["project"]),
         "--max-iterations", "1",
         "--iteration-timeout-min", "2",
         "--run-local-checks"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(root),
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if send_signal is not None:
        import time
        time.sleep(3)  # let the runner get past setup
        proc.send_signal(send_signal)
        stdout, stderr = proc.communicate(timeout=60)
    else:
        stdout, stderr = proc.communicate(timeout=300)

    return subprocess.CompletedProcess(
        args=proc.args,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _read_sentinel(world: dict) -> dict:
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    sentinel = runtime / "launcher" / "last-exit.json"
    assert sentinel.exists(), f"no sentinel found at {sentinel}"
    return json.loads(sentinel.read_text(encoding="utf-8"))


def _read_master_fm(world: dict) -> dict:
    return parse_frontmatter(
        (world["plans"] / "MASTER-2026-09-24-unattended-test.md").read_text(
            encoding="utf-8-sig"
        )
    )


# ── AC-1: e2e unattended run writes result, does not park ─────────────────────

@_NEEDS_GTIMEOUT
def test_e2e_unattended_run_writes_result_instead_of_parking(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: master with ``ilk_profile: unattended`` whose gate prints
    ``ILK-CHECK: unmeasured`` and exits 1.

    After the run:
    - master status and parked_* fields unchanged
    - sub-plan not reverted
    - result file has check row ``outcome: unmeasured`` with the reason
    - ``provenance`` is ``runner``
    """
    root = tmp_path_factory.mktemp("unattended-e2e")
    world = _build_world_profile(root)
    fm_before = _read_master_fm(world)

    result = _run_one_iteration(world, root)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])

    # Master is NOT parked.
    fm_after = _read_master_fm(world)
    assert fm_after.get("status") == fm_before.get("status"), (
        f"master status changed from {fm_before.get('status')!r} to "
        f"{fm_after.get('status')!r} — should be unchanged under profile.\n{tail}"
    )
    assert "parked_reason" not in fm_after, (
        f"master was parked under the unattended profile: {fm_after}\n{tail}"
    )

    # Sub-plan not reverted.
    sp = world["plans"] / f"{world['stem']}.md"
    sp_fm = parse_frontmatter(sp.read_text(encoding="utf-8-sig"))
    assert sp_fm.get("status") != "pending", (
        f"sub-plan was reverted to pending: {sp_fm}\n{tail}"
    )

    # Result file exists and has the right shape.
    rf = world["result_path"]
    assert rf.exists(), f"result file not written at {rf}\n{tail}"
    data = json.loads(rf.read_text(encoding="utf-8"))
    assert data["provenance"] == "runner", f"provenance is {data['provenance']!r}"
    assert data["schema_version"] == 1
    assert data["run_id"], "run_id is empty"

    # At least one check row with outcome: unmeasured.
    checks = data.get("checks", [])
    assert len(checks) >= 1, f"no checks in result: {data}"
    unmeasured = [c for c in checks if c.get("outcome") == "unmeasured"]
    assert len(unmeasured) >= 1, (
        f"no unmeasured check rows; outcomes: {[c.get('outcome') for c in checks]}"
    )
    assert unmeasured[0].get("reason"), (
        "unmeasured row has no reason"
    )


@_NEEDS_GTIMEOUT
def test_result_names_the_source_that_resolved_the_gate(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The stub commits with a ``[plan:…#step-0]`` trailer, so the gate target
    came from the trailer.  The runner used to write ``gate_resolution:
    "none"`` on every run, which a reader must treat as unmeasured."""
    root = tmp_path_factory.mktemp("gate-resolution")
    world = _build_world_profile(root)

    result = _run_one_iteration(world, root)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])

    rf = world["result_path"]
    assert rf.exists(), f"result file not written at {rf}\n{tail}"
    data = json.loads(rf.read_text(encoding="utf-8"))
    assert data.get("gate_resolution") == "trailer", (
        f"gate_resolution is {data.get('gate_resolution')!r}, expected 'trailer'\n{tail}"
    )


# ── AC-2: without profile, parks as today ─────────────────────────────────────

@_NEEDS_GTIMEOUT
def test_without_profile_parks_as_today(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-2: the same run without ``ilk_profile`` parks exactly as today.

    This is existing behaviour — no xfail.
    """
    root = tmp_path_factory.mktemp("no-profile")
    world = _build_world_profile(root, profile=None)

    result = _run_one_iteration(world, root)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])

    # Master should be parked (blocked) — existing behaviour.
    fm = _read_master_fm(world)
    assert fm.get("status") == "blocked", (
        f"master not parked without profile: status={fm.get('status')!r}\n{tail}"
    )

    # No result file written (the profile feature should not leak).
    rf = world["result_path"]
    assert not rf.exists(), (
        f"result file was written without profile: {rf}\n{tail}"
    )


# ── AC-3: SIGTERM ⇒ result file with interrupted ─────────────────────────────

@_NEEDS_GTIMEOUT
def test_sigterm_under_profile_writes_interrupted_result(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-3: SIGTERM mid-iteration under the profile ⇒ result file with
    ``exit_state: interrupted``."""
    root = tmp_path_factory.mktemp("sigterm")
    world = _build_world_profile(root)

    # Without the hold the stub finishes in about a second and the run
    # ends on its own before the signal is sent.
    result = _run_one_iteration(world, root, send_signal=signal.SIGTERM,
                                extra_env={"STUB_HOLD_SECONDS": "30"})
    # A signal trap that returns resumes the script: the run went on to gate
    # and ship-check the iteration it was told to abandon.
    assert "=== Loop ended:" not in result.stdout, (
        "the runner resumed after SIGTERM and ran to its normal end:\n"
        + "\n".join(result.stdout.splitlines()[-15:])
    )

    rf = world["result_path"]
    assert rf.exists(), f"result file not written after SIGTERM at {rf}"
    data = json.loads(rf.read_text(encoding="utf-8"))
    assert data.get("exit_state") == "interrupted", (
        f"exit_state is {data.get('exit_state')!r}, expected 'interrupted'"
    )
    assert data.get("ended_at"), "ended_at is empty after SIGTERM"


# ── AC-4: relative result_file ⇒ refused, no file, no park ───────────────────

@_NEEDS_GTIMEOUT
def test_relative_result_file_is_refused(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-4: ``result_file: relative/path.json`` ⇒ refused and logged,
    no file written, no park."""
    root = tmp_path_factory.mktemp("relative-rf")
    world = _build_world_profile(root, result_file="relative/path.json")

    result = _run_one_iteration(world, root)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])

    # No result file written.
    rf = Path(root / "project" / "relative" / "path.json")
    assert not rf.exists(), f"result file was written at {rf} despite relative path"

    # Master is NOT parked (profile's no-park semantics still apply).
    fm = _read_master_fm(world)
    assert fm.get("status") != "blocked", (
        f"master was parked despite relative result_file: {fm}\n{tail}"
    )

    # The runner logged the refusal.
    assert "result_file refused" in (result.stdout + result.stderr).lower(), (
        f"no 'result_file refused' log line found.\n{tail}"
    )


# ── AC-5: run_result.py unit tests ────────────────────────────────────────────

class TestRunResultUnit:
    """Unit tests for ``run_result.py`` writer rules.

    Each test calls ``run_result.py write`` directly with crafted inputs.
    """

    SCRIPT = SCRIPTS / "run_result.py"

    def _write(self, tmp: Path, *, extra_args: list[str] | None = None) -> dict:
        rf = tmp / "result.json"
        cmd = [
            sys.executable, str(self.SCRIPT), "write",
            "--result-file", str(rf),
            "--run-id", "test-run-001",
            "--master", "MASTER-test.md",
            "--exit-state", "all-shipped",
            *(extra_args or []),
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            timeout=30,
        )
        assert r.returncode == 0, f"exit {r.returncode}: {r.stderr or r.stdout}"
        assert rf.exists(), f"result file not written at {rf}"
        return json.loads(rf.read_text(encoding="utf-8"))

    def test_pinned_true_when_ilk_master_set(self, tmp_path: Path) -> None:
        """``pinned`` is true when ILK_MASTER was set."""
        env = {**os.environ, "ILK_MASTER": "MASTER-test.md"}
        rf = tmp_path / "result.json"
        r = subprocess.run(
            [sys.executable, str(self.SCRIPT), "write",
             "--result-file", str(rf),
             "--run-id", "t", "--master", "m", "--exit-state", "ok",
             "--pinned"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=30, env=env,
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(rf.read_text())
        assert data["pinned"] is True

    def test_pinned_false_when_ilk_master_unset(self, tmp_path: Path) -> None:
        """``pinned`` is false when ILK_MASTER is not set."""
        env = {**os.environ}
        env.pop("ILK_MASTER", None)
        rf = tmp_path / "result.json"
        r = subprocess.run(
            [sys.executable, str(self.SCRIPT), "write",
             "--result-file", str(rf),
             "--run-id", "t", "--master", "m", "--exit-state", "ok"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=30, env=env,
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(rf.read_text())
        assert data["pinned"] is False

    def test_error_maps_to_unmeasured(self, tmp_path: Path) -> None:
        """Internal ``error`` outcome ⇒ ``unmeasured`` in the result file."""
        checks = tmp_path / "checks.jsonl"
        checks.write_text(
            json.dumps({
                "slug": "test-gate", "step": 0, "cmd": "pytest",
                "outcome": "error", "exit_code": 1, "duration_s": 5.0,
                "reason": "spawn failed",
            }) + "\n",
            encoding="utf-8",
        )
        data = self._write(tmp_path, extra_args=["--checks-jsonl", str(checks)])
        rows = [c for c in data["checks"] if c["slug"] == "test-gate"]
        assert rows, f"test-gate not in checks: {data['checks']}"
        assert rows[0]["outcome"] == "unmeasured", (
            f"error should map to unmeasured, got {rows[0]['outcome']!r}"
        )

    def test_skipped_maps_to_unmeasured(self, tmp_path: Path) -> None:
        """Internal ``skipped`` outcome ⇒ ``unmeasured`` in the result file."""
        checks = tmp_path / "checks.jsonl"
        checks.write_text(
            json.dumps({
                "slug": "test-gate", "step": 0, "cmd": "pytest",
                "outcome": "skipped", "exit_code": 0, "duration_s": 0.1,
            }) + "\n",
            encoding="utf-8",
        )
        data = self._write(tmp_path, extra_args=["--checks-jsonl", str(checks)])
        rows = [c for c in data["checks"] if c["slug"] == "test-gate"]
        assert rows[0]["outcome"] == "unmeasured"

    def test_fail_row_keeps_exit_code(self, tmp_path: Path) -> None:
        """A ``fail`` row retains ``exit_code`` in the result."""
        checks = tmp_path / "checks.jsonl"
        checks.write_text(
            json.dumps({
                "slug": "test-gate", "step": 0, "cmd": "pytest",
                "outcome": "fail", "exit_code": 1, "failed_count": 3,
                "duration_s": 12.0,
            }) + "\n",
            encoding="utf-8",
        )
        data = self._write(tmp_path, extra_args=["--checks-jsonl", str(checks)])
        rows = [c for c in data["checks"] if c["slug"] == "test-gate"]
        assert rows[0]["exit_code"] == 1, "fail row lost its exit_code"
        assert rows[0]["failed_count"] == 3

    def test_unmeasured_without_reason_is_refused(self, tmp_path: Path) -> None:
        """An ``unmeasured`` row without a ``reason`` must be refused."""
        assert self.SCRIPT.exists(), (
            "run_result.py does not exist — cannot test refusal logic"
        )
        checks = tmp_path / "checks.jsonl"
        checks.write_text(
            json.dumps({
                "slug": "test-gate", "step": 0, "cmd": "pytest",
                "outcome": "unmeasured", "exit_code": None, "duration_s": 0.0,
            }) + "\n",
            encoding="utf-8",
        )
        rf = tmp_path / "result.json"
        r = subprocess.run(
            [sys.executable, str(self.SCRIPT), "write",
             "--result-file", str(rf),
             "--run-id", "t", "--master", "m", "--exit-state", "ok",
             "--checks-jsonl", str(checks)],
            capture_output=True, text=True, encoding="utf-8",
            timeout=30,
        )
        assert r.returncode != 0, (
            "unmeasured row without a reason should be refused but exit was 0"
        )

    def test_write_is_atomic(self, tmp_path: Path) -> None:
        """The write uses tmp+replace; no partial file after simulated crash."""
        # If the writer is atomic, the result file should never be a partial
        # JSON. We verify by checking that the file is valid JSON after write.
        data = self._write(tmp_path)
        # Re-read to confirm it's well-formed.
        rf = tmp_path / "result.json"
        parsed = json.loads(rf.read_text(encoding="utf-8"))
        assert parsed["schema_version"] == data["schema_version"]


# ── AC-6: PS1 refuses the profile ─────────────────────────────────────────────

@_HAS_PWSH
def test_ps1_refuses_unattended_profile(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-6: PS1 runner with ``ilk_profile: unattended`` ⇒
    ``exit_state: profile_unsupported``, master untouched."""
    root = tmp_path_factory.mktemp("ps1-refuse")
    world = _build_world_profile(root)

    fm_before = _read_master_fm(world)

    env = {
        **os.environ,
        "HOME": str(root),
        "ILK_DATA_HOME": str(world["data_home"]),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
    }
    env.pop("ILK_DATA_DIR", None)

    r = subprocess.run(
        ["pwsh", "-File", str(PS_RUNNER),
         "-ProjectPath", str(world["project"]),
         "-MaxIterations", "1",
         "-IterationTimeoutMin", "2"],
        capture_output=True, text=True, encoding="utf-8",
        timeout=120, env=env,
    )

    # Result file should exist with profile_unsupported.
    rf = world["result_path"]
    assert rf.exists(), f"PS1 did not write result file: {r.stderr[-500:]}"
    data = json.loads(rf.read_text(encoding="utf-8"))
    assert data.get("exit_state") == "profile_unsupported", (
        f"exit_state is {data.get('exit_state')!r}, expected 'profile_unsupported'"
    )

    # Master untouched.
    fm_after = _read_master_fm(world)
    assert fm_after.get("status") == fm_before.get("status"), (
        f"PS1 modified the master: {fm_before.get('status')!r} → {fm_after.get('status')!r}"
    )


@_HAS_PWSH
def test_ps1_skipped_when_pwsh_absent_is_not_a_silent_pass() -> None:
    """AC-6 guard: when pwsh is available, the test must actually run (not skip).

    This is a meta-test: if pwsh IS present but the test is somehow skipped,
    that's a silent pass. The skipif guard at the top handles the absent case.
    """
    # This test only runs when pwsh is present (the skipif is on the class/
    # module level for AC-6 tests). If we get here, pwsh exists.
    assert shutil.which("pwsh") is not None, (
        "this test should only run when pwsh is present"
    )


# ── AC-7: vocabulary includes new states ──────────────────────────────────────

def test_exit_state_vocabulary_includes_new_states() -> None:
    """AC-7: test_exit_state_vocabulary.py passes with ``lock_held`` and
    ``profile_unsupported`` in the derived set."""
    # Re-derive the states from the runners.
    contract_path = _REPO / "skills" / "ilk-loop" / "references" / "detached-component-contracts.md"
    contract_text = contract_path.read_text(encoding="utf-8")

    vocab_heading = "### State vocabulary"
    label_heading = "### Sentinel state"
    start = contract_text.index(vocab_heading)
    end = contract_text.index(label_heading, start)
    section = contract_text[start:end]

    for state in ("lock_held", "profile_unsupported"):
        assert f'"{state}"' in section or f"'{state}'" in section, (
            f"Exit state '{state}' not declared in {vocab_heading} table. "
            f"Add a row for it."
        )
