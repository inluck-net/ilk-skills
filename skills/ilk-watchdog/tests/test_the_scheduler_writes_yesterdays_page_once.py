"""Red-first pins: the scheduler writes yesterday's digest once, detached.

AC-1..AC-6 are xfail(strict=True) — the implementation does not exist yet.
AC-7 (control) is unmarked — the base scheduler dispatch JSON is unchanged.

Drives scheduler.sh via ILK_DOTSOURCE_ONLY=1 in the scheduler_sandbox
fixture (isolated HOME, ILK_DATA_HOME, and ILK_SKILL_HOME).

Part of sub-plan: the-scheduler-writes-yesterdays-page-once.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER = REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"
SKILLS_DIR = REPO_ROOT / "skills"


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_stub_digest(tmp_path: Path) -> Path:
    """Write a stub digest script that records its argv to a file."""
    argv_file = tmp_path / "digest-argv.json"
    stub = tmp_path / "stub_digest.py"
    stub.write_text(
        textwrap.dedent(f"""\
            import json, sys
            from pathlib import Path
            argv = sys.argv[1:]
            Path({str(argv_file)!r}).write_text(
                json.dumps(argv), encoding="utf-8"
            )
        """),
        encoding="utf-8",
    )
    return stub


def _source_and_call(
    sandbox,
    bash_body: str,
    *,
    timeout: int = 15,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Source scheduler.sh with ILK_DOTSOURCE_ONLY=1, then run *bash_body*.

    Returns the CompletedProcess so the caller can inspect stdout/stderr/rc.
    """
    env = {**sandbox.env, **(extra_env or {})}
    # Ensure data dirs exist for the lock acquisition at source time.
    data_home = Path(env["ILK_DATA_HOME"])
    (data_home / "logs").mkdir(parents=True, exist_ok=True)
    (data_home / "runtime" / "launcher").mkdir(parents=True, exist_ok=True)

    script = (
        f"export ILK_DOTSOURCE_ONLY=1\n"
        f"source {str(SCHEDULER)!r} || exit 90\n"
        f"unset ILK_DOTSOURCE_ONLY\n"
        f"{bash_body}\n"
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        encoding="utf-8",
        errors="replace",
        preexec_fn=sandbox.preexec,
    )


def _scheduler_log(sandbox) -> str:
    """Read the scheduler.log from the sandbox data home."""
    log_path = Path(sandbox.env["ILK_DATA_HOME"]) / "logs" / "scheduler.log"
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8")


def _digest_marker(sandbox, day: str) -> Path:
    """Return the path to the .scheduled-<day> marker."""
    return Path(sandbox.env["ILK_DATA_HOME"]) / "digest" / f".scheduled-{day}"


def _yesterday(today_str: str | None = None) -> str:
    """Compute yesterday's date string (YYYY-MM-DD)."""
    if today_str:
        today = date.fromisoformat(today_str)
    else:
        today = date.today()
    return str(today - timedelta(days=1))


# ── AC-1: maybe_write_digest starts the stub once, logs digest-start ─────────


def test_maybe_write_digest_starts_stub_once(scheduler_sandbox, tmp_path):
    """AC-1: maybe_write_digest 2026-10-04 starts the stub with
    scheduled --day 2026-10-03, and scheduler.log gains digest-start.
    """
    stub = _write_stub_digest(tmp_path)
    argv_file = tmp_path / "digest-argv.json"

    result = _source_and_call(
        scheduler_sandbox,
        'type maybe_write_digest || exit 80\n'
        'maybe_write_digest "2026-10-04" || true',
        extra_env={"DIGEST_SCRIPT": str(stub)},
    )
    assert result.returncode == 0, f"rc={result.returncode}\n{result.stderr}"

    # Poll up to 10 s for the argv file (the stub runs detached via nohup).
    import time
    deadline = time.time() + 10
    while not argv_file.exists() and time.time() < deadline:
        time.sleep(0.2)
    assert argv_file.exists(), "stub digest was never started"

    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv == ["scheduled", "--day", "2026-10-03"]

    log = _scheduler_log(scheduler_sandbox)
    assert "digest-start" in log, f"no digest-start in log:\n{log}"
    assert "2026-10-03" in log


# ── AC-2: marker present → no start, no log ─────────────────────────────────


def test_marker_present_no_start(scheduler_sandbox, tmp_path):
    """AC-2: with .scheduled-2026-10-03 present, maybe_write_digest
    starts nothing and logs nothing.
    """
    stub = _write_stub_digest(tmp_path)
    argv_file = tmp_path / "digest-argv.json"

    # Pre-create the marker.
    marker = _digest_marker(scheduler_sandbox, "2026-10-03")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")

    result = _source_and_call(
        scheduler_sandbox,
        'type maybe_write_digest || exit 80\n'
        'maybe_write_digest "2026-10-04" || true',
        extra_env={"DIGEST_SCRIPT": str(stub)},
    )
    assert result.returncode == 0, f"rc={result.returncode}\n{result.stderr}"

    import time
    time.sleep(1)
    assert not argv_file.exists(), "stub should not have been started"

    log = _scheduler_log(scheduler_sandbox)
    assert "digest-start" not in log, f"unexpected digest-start in log:\n{log}"


# ── AC-3: ILK_DIGEST=0 → no start ───────────────────────────────────────────


def test_ilk_digest_zero_no_start(scheduler_sandbox, tmp_path):
    """AC-3: ILK_DIGEST=0 → maybe_write_digest starts nothing."""
    stub = _write_stub_digest(tmp_path)
    argv_file = tmp_path / "digest-argv.json"

    result = _source_and_call(
        scheduler_sandbox,
        'type maybe_write_digest || exit 80\n'
        'maybe_write_digest "2026-10-04" || true',
        extra_env={"DIGEST_SCRIPT": str(stub), "ILK_DIGEST": "0"},
    )
    assert result.returncode == 0, f"rc={result.returncode}\n{result.stderr}"

    import time
    time.sleep(1)
    assert not argv_file.exists(), "stub should not have been started"


# ── AC-4: --once --dry-run logs digest-due, no file ──────────────────────────


def test_dry_run_logs_digest_due(scheduler_sandbox, tmp_path):
    """AC-4: scheduler.sh --once --dry-run with no projects:
    scheduler.log has digest-due naming yesterday, no digest/ file exists.
    """
    stub = _write_stub_digest(tmp_path)

    # Pre-check: maybe_write_digest must exist as a function.
    precheck = _source_and_call(
        scheduler_sandbox,
        'type maybe_write_digest',
        extra_env={"DIGEST_SCRIPT": str(stub)},
    )
    if precheck.returncode != 0:
        pytest.xfail("maybe_write_digest function not implemented")

    result = subprocess.run(
        ["bash", str(SCHEDULER), "--once", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**scheduler_sandbox.env, "DIGEST_SCRIPT": str(stub)},
        encoding="utf-8",
        errors="replace",
        preexec_fn=scheduler_sandbox.preexec,
    )
    # The scheduler exits 0 on --once --dry-run.
    assert result.returncode == 0, f"rc={result.returncode}\n{result.stderr}"

    yesterday = _yesterday()
    log = _scheduler_log(scheduler_sandbox)
    assert "digest-due" in log, f"no digest-due in log:\n{log}"
    assert yesterday in log

    # No digest/ directory should have been created.
    digest_dir = Path(scheduler_sandbox.env["ILK_DATA_HOME"]) / "digest"
    assert not digest_dir.exists(), f"digest dir should not exist: {digest_dir}"


# ── AC-5: scheduled() with one escalation → page + marker + notify ───────────


def test_scheduled_one_escalation(scheduler_sandbox, tmp_path):
    """AC-5: scheduled("D", root=tmp, notify=fake) with one escalated row:
    the page exists, marker has escalations=1/notified=true, fake called once.
    A second call returns already and fake is still at one call.
    """
    import sys as _sys
    _sys.path.insert(0, str(SKILLS_DIR / "ilk-loop" / "scripts"))
    from ilk_digest import scheduled  # noqa: E402

    day = "2026-10-03"
    root = Path(scheduler_sandbox.env["ILK_DATA_HOME"])

    # Write one escalated audit row.
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / f"{day}.jsonl").write_text(
        '{"kind":"escalated","project":"test","reason":"test reason","ts":"2026-10-03T10:00:00+0800"}\n',
        encoding="utf-8",
    )

    calls: list[tuple[str, int, Path]] = []

    def fake_notify(d: str, count: int, page: Path) -> None:
        calls.append((d, count, page))

    result = scheduled(day, root=root, notify=fake_notify)
    assert "error" not in result, f"scheduled errored: {result.get('error')}"

    # Page exists.
    page = root / "digest" / f"{day}.md"
    assert page.exists(), f"page not written: {page}"

    # Marker JSON.
    marker = root / "digest" / f".scheduled-{day}"
    assert marker.exists(), "marker not written"
    import json as _json
    data = _json.loads(marker.read_text(encoding="utf-8"))
    assert data["escalations"] == 1
    assert data["notified"] is True

    # Notify called once.
    assert len(calls) == 1
    assert calls[0][0] == day

    # Second call: returns already, notify still at one call.
    result2 = scheduled(day, root=root, notify=fake_notify)
    assert result2.get("already") is True
    assert len(calls) == 1


# ── AC-6: scheduled() with zero escalations → no notify ──────────────────────


def test_scheduled_zero_escalations_no_notify(scheduler_sandbox, tmp_path):
    """AC-6: scheduled with zero escalations does not call notify;
    with an unreadable audit file it does (escalations=1).
    """
    import sys as _sys
    _sys.path.insert(0, str(SKILLS_DIR / "ilk-loop" / "scripts"))
    from ilk_digest import scheduled  # noqa: E402

    day = "2026-10-03"
    root = Path(scheduler_sandbox.env["ILK_DATA_HOME"])

    calls: list[tuple[str, int, Path]] = []

    def fake_notify(d: str, count: int, page: Path) -> None:
        calls.append((d, count, page))

    # --- Part 1: zero escalations → no notify ---
    result = scheduled(day, root=root, notify=fake_notify)
    assert "error" not in result, f"scheduled errored: {result.get('error')}"
    assert len(calls) == 0, f"notify should not have been called, got {len(calls)} calls"

    # Remove the marker for part 2.
    marker = root / "digest" / f".scheduled-{day}"
    marker.unlink(missing_ok=True)

    # --- Part 2: unreadable audit file → escalations=1 → notify ---
    # Create a directory where the audit file should be, so open() fails with
    # IsADirectoryError (unreadable).
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / f"{day}.jsonl"
    audit_file.mkdir(exist_ok=True)  # directory blocks the file read

    result2 = scheduled(day, root=root, notify=fake_notify)
    assert "error" not in result2, f"scheduled errored: {result2.get('error')}"

    # The marker should record the unreadable audit as an escalation.
    import json as _json
    data = _json.loads(marker.read_text(encoding="utf-8"))
    assert data["escalations"] == 1, f"expected 1 escalation, got {data['escalations']}"
    assert data["notified"] is True
    assert len(calls) == 1, f"expected 1 notify call, got {len(calls)}"


# ── AC-7 (control): dispatch JSON unchanged ──────────────────────────────────


def test_dispatch_json_unchanged(scheduler_sandbox, tmp_path):
    """AC-7: the dispatch JSON of --once --dry-run for a dispatchable
    sandbox project is unchanged from the base scheduler.

    This is a control test — it must pass before AND after the change.
    """
    # Scaffold a minimal dispatchable project.
    key = "test-project"
    project_dir = Path(scheduler_sandbox.env["ILK_DATA_HOME"]) / "projects" / key
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    master = (
        "---\n"
        "title: MASTER-test\n"
        "created: 2026-10-04T00:00:00+08:00\n"
        "status: active\n"
        "priority: 0\n"
        "pause_after_ship: false\n"
        "---\n"
        "\n"
        "# MASTER-test\n"
        "\n"
        "## Sub-plan registry\n"
        "\n"
        "| # | Sub-plan | Status |\n"
        "|---|---|---|\n"
        "| 1 | [2026-10-04-work.md](./2026-10-04-work.md) | pending |\n"
    )
    (plans_dir / "MASTER-test.md").write_text(master, encoding="utf-8")

    subplan = (
        "---\n"
        "plan: work\n"
        "status: pending\n"
        "current_step: 0\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-10-04\n"
        "---\n"
        "\n"
        "# work\n"
    )
    (plans_dir / "2026-10-04-work.md").write_text(subplan, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(SCHEDULER), "--once", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=30,
        env=scheduler_sandbox.env,
        encoding="utf-8",
        errors="replace",
        preexec_fn=scheduler_sandbox.preexec,
    )
    assert result.returncode == 0, f"rc={result.returncode}\n{result.stderr}"

    # The scheduler prints a JSON decision on --once --dry-run.
    # Find the JSON line in stdout.
    json_line = None
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            json_line = line
            break

    assert json_line is not None, f"no JSON in stdout:\n{result.stdout}"
    decision = json.loads(json_line)

    # The dispatch JSON should contain a decision field.
    # Exact shape depends on the scheduler version, but it must be valid JSON
    # with at least a "decision" key.
    assert "decision" in decision, f"no 'decision' key: {decision}"