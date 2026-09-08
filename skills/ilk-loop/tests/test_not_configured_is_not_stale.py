"""Red-first: `not_configured` must not be graded for freshness.

`batch_gate` writes a record on every path, including the case where the
project has no suite to run at all::

    # batch_gate.py:599-605
    inv = "not_configured: no .ilk-launch.json found"
    return BatchGateRecord(verdict="not_configured", ...)

That verdict exists for one reason, stated at `batch_gate.py:755-758`:

    `not_configured` stays 0 deliberately: SP6 created that verdict so
    "no suite" would not read as "suite failed", and one exit code for
    both undoes it.

But `_resolve_batch_record` grades every record for freshness before reading
its verdict, and freshness compares the record's `invocation` against the one
resolved from `.ilk-launch.json`. When nothing is configured there IS no
expected invocation — `_resolve_expected_invocation` returns `''` — so the
comparison is between `"not_configured: no .ilk-launch.json found"` and `''`,
which can only mismatch. The record comes back `stale_invocation`, and
`audit_ship:477` refuses it.

**Comparing invocations is meaningless when nothing is configured.** The effect
is the one SP6 created the verdict to prevent: "no suite" reading as failure,
reached by a different route.

The parity argument for the fix: `ship_integrity.py:70-71` already returns
`ok=True, "no gate declared — nothing to enforce"` when a sub-plan declares no
checks. Absence of a *declared gate* is already not a refusal; `not_configured`
is the same shape one level up — absence of a *configured suite*.

Scope of the change, and what must NOT move: records that were actually
computed keep failing closed. `absent` in particular means the gate never ran,
which is an anomaly, and 08d deliberately made it refuse on 2026-09-08.

AC-1  a not_configured record is not graded stale, and does not block `proven`
AC-2  `absent` still refuses (the 08d guard is untouched)
AC-3  a real `fail` record still refuses
AC-4  a not_configured record on a project that HAS since gained a suite is
      graded normally — the short-circuit reads the live config, not just the
      record
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _git(repo: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=60,
    )
    return proc.stdout.strip()


def _write_record(runtime: Path, **fields) -> None:
    """Write a batch-gate record at the path batch_gate resolves."""
    from batch_gate import record_path

    runtime.mkdir(parents=True, exist_ok=True)
    rp = record_path(runtime)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(fields), encoding="utf-8")


# ── AC-1 ─────────────────────────────────────────────────────────────────────

def test_not_configured_is_not_graded_stale(tmp_path: Path) -> None:
    """A not_configured record must pass through, not become stale_invocation.

    This is the whole defect: nothing is configured, so there is no expected
    invocation to compare against, and comparing anyway can only mismatch.
    """
    from ship_audit import _resolve_batch_record

    repo = _repo(tmp_path)
    runtime = tmp_path / "runtime"
    _write_record(
        runtime,
        verdict="not_configured",
        head_sha=_head(repo),
        invocation="not_configured: no .ilk-launch.json found",
        timestamp="2026-09-09T00:00:00+08:00",
    )

    verdict, reason = _resolve_batch_record(runtime, cwd=repo)

    assert verdict == "not_configured", (
        "a not_configured record must keep its own verdict, not be regraded "
        f"as a staleness outcome; got {verdict!r} (reason: {reason!r}). "
        "Comparing invocations is meaningless when nothing is configured."
    )


def test_not_configured_does_not_block_proven(tmp_path: Path) -> None:
    """`proven` must survive a not_configured gate (AC-1, end to end)."""
    from ship_audit import audit_ship

    repo = _repo(tmp_path)
    runtime = tmp_path / "runtime"
    _write_record(
        runtime,
        verdict="not_configured",
        head_sha=_head(repo),
        invocation="not_configured: no .ilk-launch.json found",
        timestamp="2026-09-09T00:00:00+08:00",
    )

    result = audit_ship(
        status="shipped",
        body="### Step 0 — work\n\nBody.\n",
        declared_checks=[{"command": "pytest x", "timeout": 60}],
        gate_passed="unknown",
        slug="whatever",
        cwd=repo,
        runtime_dir=runtime,
        # Supply the step attribution so the commit half is satisfied and the
        # gate half is the only thing under test.
        ledger_records=[{"slug": "whatever", "step_from": 0, "step_to": 1}],
    )

    assert result["proven"] is True, (
        "a project with no suite configured must not be unprovable on that "
        f"basis alone; reasons: {result['reasons']}"
    )


# ── AC-2 / AC-3 — the guards that must NOT move ──────────────────────────────

@pytest.mark.parametrize(
    "verdict,fields",
    [
        ("absent", None),
        (
            "fail",
            dict(
                verdict="fail",
                invocation="python3 -m pytest",
                timestamp="2026-09-09T00:00:00+08:00",
            ),
        ),
    ],
)
def test_computed_verdicts_still_refuse(
    tmp_path: Path, verdict: str, fields: dict | None
) -> None:
    """absent and fail keep failing closed — 08d's guard is untouched.

    `absent` means the gate never executed, which is an anomaly, not a
    project without a suite. Relaxing it would reopen the fail-open hole
    closed on 2026-09-08.
    """
    from ship_audit import _resolve_batch_record

    repo = _repo(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    if fields is not None:
        _write_record(runtime, head_sha=_head(repo), **fields)

    got, _reason = _resolve_batch_record(runtime, cwd=repo)

    assert got != "pass", f"{verdict} must never resolve to pass; got {got!r}"
    assert got != "not_configured", (
        f"{verdict} must not be swept into the not_configured short-circuit; "
        f"got {got!r}"
    )


# ── AC-4 — the short-circuit reads the live config ───────────────────────────

def test_stale_not_configured_is_graded_when_a_suite_now_exists(
    tmp_path: Path,
) -> None:
    """A not_configured record is only trustworthy while nothing is configured.

    If the project has since gained a ship block, the old record describes a
    world that no longer exists and must be graded normally — otherwise the
    short-circuit would hide a genuinely stale record.
    """
    from ship_audit import _resolve_batch_record

    repo = _repo(tmp_path)
    (repo / ".ilk-launch.json").write_text(
        json.dumps({"ship": {"suite": {"command": "python3 -m pytest",
                                       "flags": ["--timeout=60"]}}}),
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add suite")

    runtime = tmp_path / "runtime"
    _write_record(
        runtime,
        verdict="not_configured",
        head_sha=_head(repo),
        invocation="not_configured: no .ilk-launch.json found",
        timestamp="2026-09-09T00:00:00+08:00",
    )

    got, _reason = _resolve_batch_record(runtime, cwd=repo)

    assert got != "not_configured", (
        "once a suite is configured, a not_configured record is stale and "
        f"must be graded, not short-circuited; got {got!r}"
    )
