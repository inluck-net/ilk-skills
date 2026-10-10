"""Red-first pins: an optional project records `delegated` at ship time.

Sub-plan: a-waived-verify-is-recorded-as-delegated (step 0).
One test per AC-1..AC-5 of that sub-plan's Design (binding):

AC-1  ``batch_gate --run`` on a project with
      ``ship.verification_subplan: optional`` records ``delegated`` and
      never runs the suite (the marker file must not exist).
AC-2  the same project with ``required`` keeps running the suite
      (positive control — green today and after; the waiver must not
      bleed into the required path).
AC-3  ship_audit accepts a ``delegated`` record while the live config is
      ``optional``: gate verdict ``delegated``, batch proven.
AC-4  the same record once the config flips to ``required`` refuses
      (``stale_invocation``, never proven).
AC-5  a ``delegated`` verdict is unmeasured evidence and never replaces a
      measured ``pass``/``fail`` record for the same HEAD (the
      never-overwrite-measured rule must keep holding for ``delegated``).

Hermetic: tmp git projects, tmp data homes (HOME / ILK_DATA_HOME /
ILK_DATA_DIR all point into tmp_path). Never touches the real
``~/.ilk-data``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_LOOP_SCRIPTS = _HERE.parent / "scripts"
if str(_LOOP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LOOP_SCRIPTS))

from batch_gate import _is_measured, read_record, record_path, run_batch_gate  # noqa: E402
from ship_audit import _resolve_batch_record, audit_ship  # noqa: E402


# ── fixtures (adapted from test_batch_gate.py + test_not_configured_is_not_stale.py) ──


def _git(repo: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    return proc.stdout.strip()


def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point HOME / ILK_DATA_HOME / ILK_DATA_DIR into tmp_path."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(home / "ilk-data"))
    monkeypatch.setenv("ILK_DATA_DIR", str(home / "ilk-data"))


def _stub_suite(root: Path, marker: Path) -> Path:
    """Suite command that writes *marker* and exits 0."""
    script = root / "stub_suite.sh"
    script.write_text(f'#!/bin/bash\ntouch "{marker}"\nexit 0\n', encoding="utf-8")
    script.chmod(0o755)
    return script


def _wait_helper(root: Path) -> Path:
    """Poll stub for batch_gate's backgrounded-suite wait (copy of
    the helper in test_batch_gate.py)."""
    wait = root / "wait.sh"
    wait.write_text(
        '#!/bin/bash\n'
        'OUTPUT="$1"\n'
        'for i in $(seq 1 100); do\n'
        '  if [ -f "$OUTPUT" ]; then\n'
        '    SIZE=$(wc -c < "$OUTPUT")\n'
        '    if [ "$SIZE" -gt 0 ]; then\n'
        '      sleep 0.2\n'
        '      echo 0\n'
        '      exit 0\n'
        '    fi\n'
        '  fi\n'
        '  sleep 0.1\n'
        'done\n'
        'echo 125\nexit 125\n',
        encoding="utf-8",
    )
    wait.chmod(0o755)
    return wait


def _make_project(
    root: Path,
    *,
    verification_subplan: str,
) -> tuple[Path, Path, Path]:
    """Real git project + stub suite + runtime dir.

    Returns ``(project, runtime, marker)``.  The stub suite writes
    *marker* when — and only when — the suite actually runs.
    """
    root.mkdir(parents=True, exist_ok=True)
    project = root / "proj"
    project.mkdir()
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "f.txt").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    marker = root / "suite-ran.marker"
    suite = _stub_suite(root, marker)
    (project / ".ilk-launch.json").write_text(
        json.dumps({
            "ship": {
                "suite": {"command": str(suite)},
                "verification_subplan": verification_subplan,
            },
        }),
        encoding="utf-8",
    )
    runtime = root / "runtime"
    runtime.mkdir()
    return project, runtime, marker


def _run_gate(project: Path, runtime: Path, root: Path):
    return run_batch_gate(
        project, runtime,
        _wait_helper=_wait_helper(root),
        _poll_timeout=30,
    )


def _write_record(runtime: Path, **fields) -> None:
    """Hand-write a batch-gate record at the path batch_gate resolves."""
    runtime.mkdir(parents=True, exist_ok=True)
    rp = record_path(runtime)
    rp.write_text(json.dumps(fields), encoding="utf-8")


def _delegated_invocation(config_path: Path) -> str:
    return f"delegated: ship.verification_subplan optional in {config_path}"


def _audit(repo: Path, runtime: Path) -> dict:
    """audit_ship over a one-step shipped sub-plan, gate half only under
    test — the commit half is satisfied via ledger_records."""
    return audit_ship(
        status="shipped",
        body="### Step 0 — work\n\nBody.\n",
        declared_checks=[{"command": "pytest x", "timeout": 60}],
        gate_passed="unknown",
        slug="whatever",
        cwd=repo,
        runtime_dir=runtime,
        ledger_records=[{"slug": "whatever", "step_from": 0, "step_to": 1}],
    )


# ── AC-1: optional records delegated, suite never runs ───────────────────────


class TestAC1:
    """``ship.verification_subplan: optional`` ⇒ ``delegated``, no suite."""

    def test_optional_records_delegated_and_skips_the_suite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic(tmp_path, monkeypatch)
        project, runtime, marker = _make_project(
            tmp_path / "opt", verification_subplan="optional",
        )

        rec = _run_gate(project, runtime, tmp_path / "opt")

        assert rec is not None
        assert rec.verdict == "delegated", (
            f"an optional project must record delegated, not {rec.verdict!r}"
        )
        assert rec.invocation.startswith("delegated:"), rec.invocation
        assert not marker.exists(), (
            "the suite must not run at all when the project waived the "
            "verify sub-plan — marker file exists"
        )
        on_disk = read_record(runtime)
        assert on_disk is not None
        assert on_disk.verdict == "delegated"


# ── AC-2: required keeps running the suite (positive control) ────────────────


class TestAC2:
    """``ship.verification_subplan: required`` ⇒ the suite runs.

    Green today and after: the waiver is opt-in and must not bleed into
    the required path.
    """

    def test_required_still_runs_the_suite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic(tmp_path, monkeypatch)
        project, runtime, marker = _make_project(
            tmp_path / "req", verification_subplan="required",
        )

        rec = _run_gate(project, runtime, tmp_path / "req")

        assert rec is not None
        assert rec.verdict != "delegated", (
            "required must never record delegated"
        )
        assert rec.verdict in ("pass", "fail"), rec.verdict
        assert marker.exists(), (
            "required must keep running the suite — marker file missing"
        )


# ── AC-3: ship_audit accepts delegated while the live config is optional ─────


class TestAC3:
    """A ``delegated`` record + live optional config ⇒ delegated, proven."""

    def test_ship_audit_accepts_delegated_while_optional(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic(tmp_path, monkeypatch)
        project, runtime, _marker = _make_project(
            tmp_path / "opt", verification_subplan="optional",
        )
        _write_record(
            runtime,
            verdict="delegated",
            head_sha=_head(project),
            invocation=_delegated_invocation(project / ".ilk-launch.json"),
            timestamp="2026-10-10T00:00:00+08:00",
        )

        verdict, reason = _resolve_batch_record(runtime, cwd=project)
        assert verdict == "delegated", (
            f"live optional config must accept the delegated record; "
            f"got {verdict!r} (reason: {reason!r})"
        )

        result = _audit(project, runtime)
        assert result["proven"] is True, (
            f"a delegated batch with live optional config must be proven; "
            f"reasons: {result['reasons']}"
        )
        assert result["final_gate"] == "delegated", result["final_gate"]


# ── AC-4: the same record refuses once the config flips to required ──────────


class TestAC4:
    """The delegated short-circuit is gated on the LIVE config."""

    def test_delegated_record_refuses_once_required(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic(tmp_path, monkeypatch)
        project, runtime, _marker = _make_project(
            tmp_path / "flip", verification_subplan="optional",
        )
        # The same record: whatever the optional project's gate wrote.
        rec = _run_gate(project, runtime, tmp_path / "flip")
        assert rec is not None

        # …the live config flips back to required, keeping the same suite.
        suite_cmd = str(tmp_path / "flip" / "stub_suite.sh")
        (project / ".ilk-launch.json").write_text(
            json.dumps({
                "ship": {
                    "suite": {"command": suite_cmd},
                    "verification_subplan": "required",
                },
            }),
            encoding="utf-8",
        )

        got, _reason = _resolve_batch_record(runtime, cwd=project)
        assert got == "stale_invocation", (
            "a delegated record must grade stale_invocation once the live "
            f"config is required — never as pass/delegated; got {got!r}"
        )

        result = _audit(project, runtime)
        assert result["proven"] is False, (
            f"a delegated record must refuse to prove a required project; "
            f"reasons: {result['reasons']}"
        )


# ── AC-5: delegated never replaces measured evidence for the same HEAD ───────


class TestAC5:
    """The never-overwrite-measured rule must keep holding for ``delegated``."""

    def test_delegated_is_unmeasured_and_never_replaces_a_measured_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic(tmp_path, monkeypatch)

        # (a) measured evidence for the same HEAD survives a gate attempt.
        project, runtime, _marker = _make_project(
            tmp_path / "keep", verification_subplan="optional",
        )
        _write_record(
            runtime,
            verdict="fail",
            head_sha=_head(project),
            invocation="python3 -m pytest -q",
            timestamp="2026-10-10T00:00:00+08:00",
        )
        _run_gate(project, runtime, tmp_path / "keep")
        on_disk = read_record(runtime)
        assert on_disk is not None
        assert on_disk.verdict == "fail", (
            "a measured fail record for the same HEAD must never be "
            f"replaced; on disk: {on_disk.verdict!r}"
        )

        # (b) the gate's own delegated verdict is unmeasured evidence —
        #     the class batch_gate.py:1112-1121 refuses to write over
        #     a measured record.
        project2, runtime2, _marker2 = _make_project(
            tmp_path / "fresh", verification_subplan="optional",
        )
        rec2 = _run_gate(project2, runtime2, tmp_path / "fresh")
        assert rec2 is not None
        assert rec2.verdict == "delegated", rec2.verdict
        assert not _is_measured(rec2), (
            "delegated must be unmeasured evidence (like not_configured / "
            "error) so the never-overwrite-measured rule applies to it"
        )
