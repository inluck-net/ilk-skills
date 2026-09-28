"""Red-first pins: a #ship-credited final step that declares per-step
local_checks must have its gate on record.

Regression for gh-resolve-10 2026-09-28: ship_audit reports PROVEN for
the-contract-is-read-from-a-resolver-comment although step 1's per-step
gate never ran (ship_audit.py:322-323 credits the last step on the
#ship trailer alone).

Acceptance criteria:
  AC-1 (the repro): fixture repo with #step-0 and #ship commits, a
    sub-plan whose step 1 declares per-step local_checks, a loop log
    holding a pass record for step 0 only ⇒ audit_ship → proven: False,
    reason names step 1.  @xfail (red-first)
  AC-2: same, plus a loop-log pass record for step 1 ⇒ proven: True.
  AC-3: same as AC-1 but step 1 declares no per-step local_checks ⇒
    proven: True (today's behaviour).
  AC-4: a gate_pass_at_head ledger row covering step 1 instead of the
    loop-log record ⇒ proven: True.
  AC-5: check_step_commits returns the same (present, missing) as before
    for AC-1's fixture (the runner's verdict is unchanged).
  AC-6: loop log path pointing at a missing file ⇒ proven: False with
    the "loop log unreadable" reason.  @xfail (red-first)
  AC-7: existing tests stay green (regression guard — see
    test_ship_audit.py, test_ship_audit_ledger_gap.py,
    test_ship_audit_reads_record.py, test_ship_audit_authoritative.py,
    test_loop_status_proven_agrees.py, test_ship_integrity.py).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import ship_audit


# ── helpers ──────────────────────────────────────────────────────────────────

def _init_repo(path: Path) -> None:
    """Create a git repo with an initial commit so ``git log`` works."""
    subprocess.run(
        ["git", "init"], cwd=path, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=path,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    (path / ".gitkeep").write_text("")
    subprocess.run(
        ["git", "add", ".gitkeep"], cwd=path,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )


def _commit_with_message(path: Path, subject: str, body: str = "") -> None:
    """Create a commit with a specific subject and optional body."""
    (path / "marker.txt").write_text(subject)
    subprocess.run(
        ["git", "add", "marker.txt"], cwd=path,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    msg = subject if not body else f"{subject}\n\n{body}"
    subprocess.run(
        ["git", "commit", "-m", msg, "--allow-empty"],
        cwd=path, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )


# ── fixtures ─────────────────────────────────────────────────────────────────

# A shipped sub-plan where step 1 declares per-step local_checks.
# Mirrors the real repro: estimated_steps 2, commits for #step-0 and #ship,
# no #step-1 trailer.
_BODY_GATED_STEP1 = """\
### Step 0 — the driver fix
```yaml
local_checks:
  - command: echo step0-gate
    timeout: 30
```
- Fix the driver so step_to = estimated_steps when shipping.

### Step 1 — the audit asks for the gate
```yaml
local_checks:
  - command: echo step1-gate
    timeout: 30
```
- Require a local_checks record for the final step.
"""

# Same sub-plan shape but step 1 has no per-step local_checks (AC-3 control).
_BODY_NO_GATE_STEP1 = """\
### Step 0 — the driver fix
```yaml
local_checks:
  - command: echo step0-gate
    timeout: 30
```
- Fix the driver so step_to = estimated_steps when shipping.

### Step 1 — the audit asks for the gate
- Require a local_checks record for the final step.
"""


def _make_loop_log(
    tmp: Path,
    records: list[dict],
    *,
    missing: bool = False,
) -> Path | None:
    """Write a JSONL loop log and return its path.

    ``missing=True`` returns a path to a file that does NOT exist.
    """
    if missing:
        return tmp / "nonexistent" / ".ilk-loop.log"
    log_path = tmp / "logs" / ".ilk-loop.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return log_path


def _make_repo(tmp: Path) -> Path:
    """Create a fixture repo with #step-0 and #ship commits."""
    repo = tmp / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit_with_message(
        repo, "feat(x): step 0 [plan:a-ship-trailer-needs-its-gate#step-0]",
    )
    _commit_with_message(
        repo, "chore(plans): a-ship-trailer-needs-its-gate shipped "
              "[plan:a-ship-trailer-needs-its-gate#ship]",
    )
    return repo


def _audit(**kwargs) -> dict:
    """Call ``audit_ship`` with defaults for the common repro shape.

    ``gate_passed="true"`` so the batch-gate half passes today.
    The xfail tests catch the per-step gate logic (not yet implemented).
    """
    defaults = dict(
        status="shipped",
        body=_BODY_GATED_STEP1,
        declared_checks=[{"command": "echo step1-gate", "timeout": 30}],
        gate_passed="true",
        slug="a-ship-trailer-needs-its-gate",
    )
    defaults.update(kwargs)
    return ship_audit.audit_ship(**defaults)


# ── AC-1: the repro ─────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac1_ship_credited_final_step_needs_gate_record(tmp_path: Path) -> None:
    """#step-0 + #ship, step 1 declares per-step local_checks, loop log
    has a pass record for step 0 only ⇒ proven: False, reason names step 1.
    """
    repo = _make_repo(tmp_path)
    log_path = _make_loop_log(tmp_path, [
        {
            "slug": "a-ship-trailer-needs-its-gate",
            "step": 0,
            "outcome": "pass",
            "exit_code": 0,
            "command": "echo step0-gate",
        },
    ])

    result = _audit(cwd=repo, loop_log_path=log_path)
    assert result["proven"] is False, (
        f"AC-1: final step 1 has per-step local_checks but no gate record; "
        f"must be unproven. Got {result}"
    )
    assert len(result["reasons"]) >= 1, f"AC-1: expected at least 1 reason. Got {result}"
    assert "step 1" in result["reasons"][0], (
        f"AC-1: reason must name step 1. Got {result['reasons']}"
    )


# ── AC-2: gate record exists for step 1 ─────────────────────────────────────

def test_ac2_gate_record_exists_for_final_step(tmp_path: Path) -> None:
    """Same as AC-1 but loop log also has a pass record for step 1 ⇒ proven: True."""
    repo = _make_repo(tmp_path)
    log_path = _make_loop_log(tmp_path, [
        {
            "slug": "a-ship-trailer-needs-its-gate",
            "step": 0,
            "outcome": "pass",
            "exit_code": 0,
            "command": "echo step0-gate",
        },
        {
            "slug": "a-ship-trailer-needs-its-gate",
            "step": 1,
            "outcome": "pass",
            "exit_code": 0,
            "command": "echo step1-gate",
        },
    ])

    result = _audit(cwd=repo, loop_log_path=log_path)
    assert result["proven"] is True, f"AC-2: gate on record ⇒ proven. Got {result}"


# ── AC-3: no per-step local_checks ⇒ exempt ─────────────────────────────────

def test_ac3_no_per_step_gate_is_exempt(tmp_path: Path) -> None:
    """Step 1 declares no per-step local_checks ⇒ proven: True (today's behaviour)."""
    repo = _make_repo(tmp_path)
    result = ship_audit.audit_ship(
        status="shipped",
        body=_BODY_NO_GATE_STEP1,
        declared_checks=[{"command": "echo step0-gate", "timeout": 30}],
        gate_passed="true",
        slug="a-ship-trailer-needs-its-gate",
        cwd=repo,
    )
    assert result["proven"] is True, (
        f"AC-3: step 1 has no per-step local_checks ⇒ exempt. Got {result}"
    )


# ── AC-4: ledger gate_pass_at_head covering step 1 ──────────────────────────

def test_ac4_ledger_gate_pass_at_head_covers_step(tmp_path: Path) -> None:
    """A gate_pass_at_head ledger row covering step 1 ⇒ proven: True."""
    repo = _make_repo(tmp_path)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout.strip()

    ledger = [
        {
            "proof": "gate_pass_at_head",
            "gate_outcome": "pass",
            "slug": "a-ship-trailer-needs-its-gate",
            "step_from": 1,
            "step_to": 2,
            "commits": [sha],
        },
    ]

    result = _audit(
        cwd=repo,
        ledger_records=ledger,
        loop_log_path=_make_loop_log(tmp_path, []),
    )
    assert result["proven"] is True, (
        f"AC-4: ledger gate_pass_at_head covers step 1 ⇒ proven. Got {result}"
    )


# ── AC-5: check_step_commits unchanged ──────────────────────────────────────

def test_ac5_check_step_commits_unchanged(tmp_path: Path) -> None:
    """check_step_commits returns the same (present, missing) for AC-1's
    fixture — the runner's verdict is unchanged.
    """
    repo = _make_repo(tmp_path)

    # Step 1 is in `present` because #ship covers max(authored).
    # Step 0 is in `present` because #step-0 covers it.
    present, missing = ship_audit.check_step_commits(
        "a-ship-trailer-needs-its-gate", [0, 1], cwd=repo,
    )
    assert sorted(present) == [0, 1], (
        f"AC-5: both steps should be present (#step-0 + #ship). Got {present}"
    )
    assert missing == [], f"AC-5: no steps should be missing. Got {missing}"


# ── AC-6: loop log missing file ─────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_ac6_loop_log_missing_file(tmp_path: Path) -> None:
    """Loop log path points at a missing file ⇒ proven: False with
    "loop log unreadable" reason.
    """
    repo = _make_repo(tmp_path)
    log_path = _make_loop_log(tmp_path, [], missing=True)

    result = _audit(cwd=repo, loop_log_path=log_path)
    assert result["proven"] is False, (
        f"AC-6: missing loop log ⇒ unproven. Got {result}"
    )
    assert any("unreadable" in r for r in result["reasons"]), (
        f"AC-6: reason must mention 'unreadable'. Got {result['reasons']}"
    )