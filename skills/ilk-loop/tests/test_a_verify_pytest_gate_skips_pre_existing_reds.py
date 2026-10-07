"""Red-first tests: a batch-verification pytest gate deselects pre-existing reds.

Part of `a-verify-pytest-gate-skips-pre-existing-reds` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 1 removes.

The contract (from the sub-plan):

When the sub-plan being checked has ``batch_verification: true``, then for each
check whose command starts with ``python3 -m pytest``:

1. Find the batch name: the ``--batch <name>`` argument of that same sub-plan's
   ``verification_record.py`` command (step 0).  None found ⇒ no deselects.
2. Load that batch's record and verify its digest exactly as
   ``verify_attribution`` does (import and reuse its functions; do not copy
   them).  Missing, digest mismatch, or unparseable ⇒ no deselects, and print
   ``[gates] no signed record for <batch>; pytest gate runs as authored``.
3. Collect node ids whose ``at base`` cell is ``failed`` or
   ``declared-at-base``.  Append ``--deselect <id>`` for each (skip ids
   already deselected in the command) before running, and print
   ``[gates] deselected <n> pre-existing red(s) measured at base in <record
   path>: <ids, first 5>``.
4. Never deselect any other verdict (``passed``, ``absent-at-base``,
   ``unmeasured-at-base``, ``failed-differently``, ``born-red-at:*``,
   ``owned-by:*``).
5. Non-verify sub-plans and non-pytest commands are unchanged.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import verify_attribution as va  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _setup_project(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Create a tmp git project and pin ILK_DATA_HOME / HOME.

    Returns (project_dir, data_home).
    """
    data_home = tmp_path / "ilk-data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    _git(proj, "commit", "-q", "--allow-empty", "-m", "init")
    return proj, data_home


def _write_pytest_file(proj: Path) -> Path:
    """Write a pytest file with one always-failing test and one passing test."""
    test_file = proj / "test_gate_target.py"
    test_file.write_text(textwrap.dedent("""\
        def test_t_red():
            assert False, "always fails"

        def test_t_ok():
            pass
    """), encoding="utf-8")
    return test_file


def _write_batch_subplan(proj: Path, batch: str, pytest_cmd: str) -> Path:
    """Create a batch-verification sub-plan with step-0 record + step-1 gate."""
    plans = proj / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    sp = plans / f"2026-10-07-{batch}.md"
    sp.write_text(textwrap.dedent(f"""\
        ---
        plan: {batch}
        status: pending
        current_step: 1
        estimated_steps: 2
        batch_verification: true
        local_checks: []
        ---

        # {batch}

        ### Step 0 — record

        ```yaml
        local_checks:
          - command: "python3 verification_record.py --batch {batch} --run-suite"
            timeout: 600
        ```

        ### Step 1 — verify

        ```yaml
        local_checks:
          - command: "{pytest_cmd}"
            timeout: 60
        ```
    """), encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", f"add {batch} sub-plan")
    return sp


def _make_record_text(batch: str, base_sha: str, rows: list[tuple[str, str]],
                      *, digest: str | None = None) -> str:
    """Build a record with the given at-base rows.

    If *digest* is provided, it is written as-is (for tamper tests).
    Otherwise computes the real digest from the surface above ``## Findings``.
    """
    row_lines = "\n".join(
        f"| {node} | {verdict} | no | — |" for node, verdict in rows
    )
    surface = textwrap.dedent(f"""\
        # Batch verification record — {batch}

        record_writer: verification_record.py
        batch: {batch}
        verified_head: {"a" * 40}
        verified_tree: {"b" * 40}
        base_sha: {base_sha}
        suite_invocation: python3 -m pytest -q
        suite_scope: targeted
        suite_scope_reason: test
        selection_size: 2
        suite_total: 2
        suite_passed: 1
        suite_failed: 1
        suite_errors: 0
        suite_skipped: 0
        suite_duration_sec: 1
        suite_budget: 600 (measured)
        suite_source: tool
        ledger_mode: require
        head_source: ledger {"b" * 40} {"c" * 16}
        base_source: rerun
        record_elapsed_sec: 1

        ## At-base rerun

        | node id | at base | in baseline_red | head reruns | batch touched file |
        |---|---|---|---|---|
        {row_lines}
    """)
    if digest is None:
        digest = hashlib.sha256(surface.encode("utf-8")).hexdigest()
    return surface + f"\nrecord_digest: {digest}\n"


def _write_record(data_home: Path, key: str, batch: str, text: str) -> Path:
    """Write a record file to the external logs dir."""
    vdir = data_home / "projects" / key / "logs" / "verification"
    vdir.mkdir(parents=True, exist_ok=True)
    rec = vdir / f"{batch}-batch.md"
    rec.write_text(text, encoding="utf-8")
    return rec


def _resolve_project_key(proj: Path) -> str:
    """Resolve the ilk project key for a path."""
    from ilk_paths import resolve_project_key
    key = resolve_project_key(proj)
    assert key is not None, f"no project key for {proj}"
    return key


# ── AC-1: record row t_red | failed ⇒ gate passes, t_red deselected ─────────

class TestAC1DeselectFailedAtBase:
    """A pytest gate deselects node ids that the record measured as failed at base."""

    @pytest.mark.xfail(strict=True, reason="deselection not yet implemented in run_local_checks.py")
    def test_failed_at_base_is_deselected(self, tmp_path: Path, monkeypatch) -> None:
        """AC-1: record row t_red | failed ⇒ gate passes, stdout names t_red."""
        proj, data_home = _setup_project(tmp_path, monkeypatch)
        _write_pytest_file(proj)
        key = _resolve_project_key(proj)
        batch = "batch-test-ac1"
        base_sha = _git(proj, "rev-parse", "HEAD")
        node_id = "test_gate_target.py::test_t_red"
        rec_text = _make_record_text(batch, base_sha, [(node_id, "failed")])
        _write_record(data_home, key, batch, rec_text)

        pytest_cmd = "python3 -m pytest test_gate_target.py -q -p no:cacheprovider"
        _write_batch_subplan(proj, batch, pytest_cmd)

        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "run_local_checks.py"),
             "--project", str(proj), "--slug", batch, "--step", "1"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, f"gate should pass; stderr:\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert "deselected" in combined.lower(), (
            "stdout should name the deselected id"
        )


# ── AC-2: record row t_red | passed (regression) ⇒ gate FAILS ───────────────

class TestAC2RegressionFails:
    """A pytest gate does NOT deselect a node that passed at base (regression)."""

    def test_passed_at_base_is_not_deselected(self, tmp_path: Path, monkeypatch) -> None:
        """AC-2: record row t_red | passed ⇒ gate FAILS; nothing deselected."""
        proj, data_home = _setup_project(tmp_path, monkeypatch)
        _write_pytest_file(proj)
        key = _resolve_project_key(proj)
        batch = "batch-test-ac2"
        base_sha = _git(proj, "rev-parse", "HEAD")
        node_id = "test_gate_target.py::test_t_red"
        rec_text = _make_record_text(batch, base_sha, [(node_id, "passed")])
        _write_record(data_home, key, batch, rec_text)

        pytest_cmd = "python3 -m pytest test_gate_target.py -q -p no:cacheprovider"
        _write_batch_subplan(proj, batch, pytest_cmd)

        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "run_local_checks.py"),
             "--project", str(proj), "--slug", batch, "--step", "1"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode != 0, "gate should fail (regression not deselected)"
        combined = result.stdout + result.stderr
        assert "deselected" not in combined.lower(), (
            "nothing should be deselected for a passed-at-base id"
        )


# ── AC-3: record row t_red | unmeasured-at-base ⇒ gate FAILS ────────────────

class TestAC3UnmeasuredFails:
    """A pytest gate does NOT deselect an unmeasured-at-base node."""

    def test_unmeasured_at_base_is_not_deselected(self, tmp_path: Path, monkeypatch) -> None:
        """AC-3: record row t_red | unmeasured-at-base ⇒ gate FAILS."""
        proj, data_home = _setup_project(tmp_path, monkeypatch)
        _write_pytest_file(proj)
        key = _resolve_project_key(proj)
        batch = "batch-test-ac3"
        base_sha = _git(proj, "rev-parse", "HEAD")
        node_id = "test_gate_target.py::test_t_red"
        rec_text = _make_record_text(batch, base_sha, [(node_id, "unmeasured-at-base")])
        _write_record(data_home, key, batch, rec_text)

        pytest_cmd = "python3 -m pytest test_gate_target.py -q -p no:cacheprovider"
        _write_batch_subplan(proj, batch, pytest_cmd)

        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "run_local_checks.py"),
             "--project", str(proj), "--slug", batch, "--step", "1"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode != 0, "gate should fail (unmeasured not deselected)"


# ── AC-4: digest mismatch ⇒ gate FAILS, prints "no signed record" ───────────

class TestAC4DigestMismatch:
    """A pytest gate refuses to deselect when the record digest is wrong."""

    @pytest.mark.xfail(strict=True, reason="deselection not yet implemented in run_local_checks.py")
    def test_tampered_record_refused(self, tmp_path: Path, monkeypatch) -> None:
        """AC-4: digest mismatch ⇒ gate FAILS, prints 'no signed record'."""
        proj, data_home = _setup_project(tmp_path, monkeypatch)
        _write_pytest_file(proj)
        key = _resolve_project_key(proj)
        batch = "batch-test-ac4"
        base_sha = _git(proj, "rev-parse", "HEAD")
        node_id = "test_gate_target.py::test_t_red"
        # Write with a wrong digest (tampered record).
        rec_text = _make_record_text(batch, base_sha, [(node_id, "failed")],
                                     digest="0" * 64)
        _write_record(data_home, key, batch, rec_text)

        pytest_cmd = "python3 -m pytest test_gate_target.py -q -p no:cacheprovider"
        _write_batch_subplan(proj, batch, pytest_cmd)

        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "run_local_checks.py"),
             "--project", str(proj), "--slug", batch, "--step", "1"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        # Gate should fail (t_red still fails, nothing deselected).
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "no signed record" in combined.lower(), (
            "should print the 'no signed record' line"
        )


# ── AC-5: non-batch-verification sub-plan ⇒ unchanged behaviour ─────────────

class TestAC5NonBatchUnchanged:
    """A sub-plan without batch_verification: true is unaffected."""

    def test_non_batch_gate_behaviour_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        """AC-5: without batch_verification: true ⇒ gate FAILS (unchanged)."""
        proj, _data_home = _setup_project(tmp_path, monkeypatch)
        _write_pytest_file(proj)

        # Create a non-batch-verification sub-plan.
        plans = proj / "docs" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        sp = plans / "2026-10-07-test-normal.md"
        sp.write_text(textwrap.dedent("""\
            ---
            plan: test-normal
            status: pending
            current_step: 0
            estimated_steps: 1
            local_checks: []
            ---

            # Normal sub-plan

            ### Step 0 — run

            ```yaml
            local_checks:
              - command: "python3 -m pytest test_gate_target.py -q -p no:cacheprovider"
                timeout: 60
            ```
        """), encoding="utf-8")
        _git(proj, "add", "-A")
        _git(proj, "commit", "-q", "-m", "add sub-plan")

        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "run_local_checks.py"),
             "--project", str(proj), "--slug", "test-normal", "--step", "0"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        # t_red always fails, so the gate should fail with no deselection.
        assert result.returncode != 0, "non-batch gate should fail on t_red"
        combined = result.stdout + result.stderr
        assert "deselected" not in combined.lower(), (
            "non-batch sub-plan should not trigger deselection"
        )


# ── declared-at-base also gets deselected ────────────────────────────────────

class TestDeclaredAtBase:
    """declared-at-base verdicts are also pre-existing and should be deselected."""

    @pytest.mark.xfail(strict=True, reason="deselection not yet implemented in run_local_checks.py")
    def test_declared_at_base_is_deselected(self, tmp_path: Path, monkeypatch) -> None:
        """A declared-at-base row is treated like failed for deselection."""
        proj, data_home = _setup_project(tmp_path, monkeypatch)
        _write_pytest_file(proj)
        key = _resolve_project_key(proj)
        batch = "batch-test-declared"
        base_sha = _git(proj, "rev-parse", "HEAD")
        node_id = "test_gate_target.py::test_t_red"
        rec_text = _make_record_text(batch, base_sha, [(node_id, "declared-at-base")])
        _write_record(data_home, key, batch, rec_text)

        pytest_cmd = "python3 -m pytest test_gate_target.py -q -p no:cacheprovider"
        _write_batch_subplan(proj, batch, pytest_cmd)

        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "run_local_checks.py"),
             "--project", str(proj), "--slug", batch, "--step", "1"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, f"gate should pass; stderr:\n{result.stderr}"


# ── missing record ⇒ no deselects, gate runs as authored ─────────────────────

class TestMissingRecord:
    """When no signed record exists, the gate runs without deselection."""

    @pytest.mark.xfail(strict=True, reason="deselection not yet implemented in run_local_checks.py")
    def test_missing_record_prints_no_signed_record(self, tmp_path: Path, monkeypatch) -> None:
        """No record on disk ⇒ no deselects, prints the diagnostic line."""
        proj, _data_home = _setup_project(tmp_path, monkeypatch)
        _write_pytest_file(proj)

        batch = "batch-missing"
        pytest_cmd = "python3 -m pytest test_gate_target.py -q -p no:cacheprovider"
        _write_batch_subplan(proj, batch, pytest_cmd)

        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "run_local_checks.py"),
             "--project", str(proj), "--slug", batch, "--step", "1"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        combined = result.stdout + result.stderr
        assert "no signed record" in combined.lower(), (
            "missing record should print diagnostic"
        )
        # Gate still fails because t_red is not deselected.
        assert result.returncode != 0