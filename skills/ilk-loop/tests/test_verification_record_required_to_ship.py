"""Pin that a batch_verification sub-plan must leave its record.

Part of `a-verification-subplan-cannot-ship-without-its-record` step 0.
Tests marked ``xfail(strict=True)`` are red-first pins that step 1 removes.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
CLI = SCRIPTS_DIR / "ship_integrity.py"


# ── fixtures (adapted from test_verify_attribution + test_ship_integrity_step_commits) ──


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout.strip()


def _project_with_record(tmp_path: Path, monkeypatch, *, body: str | None = None,
                         slug: str = "batch-t"):
    """Set up ILK_DATA_HOME, HOME, a git repo, and optionally a verification record."""
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")
    _git(proj, "commit", "-q", "--allow-empty", "-m", "init")

    sys.path.insert(0, str(SCRIPTS_DIR))
    from ilk_paths import external_logs_dir, resolve_project_key  # noqa: E402
    key = resolve_project_key(proj)
    vdir = external_logs_dir(key) / "verification"
    vdir.mkdir(parents=True, exist_ok=True)
    if body is not None:
        (vdir / f"{slug}-batch.md").write_text(body, encoding="utf-8")
    return proj, vdir, key


def _subplan_with_batch_gate(proj: Path, slug: str = "batch-t",
                             status: str = "shipped") -> Path:
    """Put a batch_verification sub-plan inside the git repo at proj/docs/plans/."""
    plans = proj / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent("""\
        ### Step 0 — verify

        ```yaml
        local_checks:
          - command: python3 -c "pass" --batch batch-t
            timeout: 60
        ```
        - work
    """)
    sp = plans / f"{slug}.md"
    sp.write_text(
        f"---\nplan: {slug}\nstatus: {status}\ncurrent_step: 1\n"
        f"estimated_steps: 1\nbatch_verification: true\nlocal_checks: []\n"
        f"---\n\n# {slug}\n\n{body}",
        encoding="utf-8",
    )
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", f"feat: step 0 [plan:{slug}#step-0]")
    return sp


def _run(subplan: Path, repo: Path, gate: str = "true") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), "--subplan", str(subplan), "--gate-passed", gate],
        capture_output=True, text=True, timeout=60, cwd=repo, encoding="utf-8",
    )


# ── record-absence tests (step 1: xfail markers removed) ────────────────────


def test_absent_record_is_reported_with_resolved_path(tmp_path: Path, monkeypatch) -> None:
    """_missing_record_reason must name the resolved directory it checked."""
    from ship_integrity import _missing_record_reason

    proj, vdir, _ = _project_with_record(tmp_path, monkeypatch, body=None)
    sp = _subplan_with_batch_gate(proj)

    result = _missing_record_reason(sp)
    assert result is not None
    assert str(vdir) in result


def test_absent_record_is_warn_only_while_not_enforcing(tmp_path: Path, monkeypatch) -> None:
    """While ENFORCE_RECORD_REQUIRED is False, CLI exits 0 with a warning."""
    proj, _, _ = _project_with_record(tmp_path, monkeypatch, body=None)
    sp = _subplan_with_batch_gate(proj)

    result = _run(sp, proj)
    assert result.returncode == 0
    assert "WARN RECORD ABSENT" in result.stderr


def test_absent_record_refuses_when_enforcing(tmp_path: Path, monkeypatch) -> None:
    """When ENFORCE_RECORD_REQUIRED is True, absent record blocks the ship."""
    import ship_integrity
    from ship_integrity import _cli

    monkeypatch.setattr(ship_integrity, "ENFORCE_RECORD_REQUIRED", True)
    proj, _, _ = _project_with_record(tmp_path, monkeypatch, body=None)
    sp = _subplan_with_batch_gate(proj)

    # Call _cli directly so the monkeypatch is visible (subprocess won't see it).
    exit_code = _cli(["--subplan", str(sp), "--gate-passed", "true"])
    assert exit_code == 1


# ── positive controls (green today) ─────────────────────────────────────────


def test_present_record_ships(tmp_path: Path, monkeypatch) -> None:
    """A batch_verification sub-plan with a record on disk ships."""
    proj, vdir, _ = _project_with_record(
        tmp_path, monkeypatch,
        body="suite_failed: 0\n\n## At-base rerun\n\n_(no failures)_\n",
    )
    sp = _subplan_with_batch_gate(proj)

    result = _run(sp, proj)
    assert result.returncode == 0


def test_non_batch_verification_subplan_unaffected(tmp_path: Path, monkeypatch) -> None:
    """A sub-plan without batch_verification: true is unaffected."""
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")
    _git(proj, "commit", "-q", "--allow-empty", "-m", "init")

    plans = proj / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent("""\
        ### Step 0 — verify

        ```yaml
        local_checks:
          - command: python3 -c "pass"
            timeout: 60
        ```
        - work
    """)
    sp = plans / "no-batch.md"
    sp.write_text(
        "---\nplan: no-batch\nstatus: shipped\ncurrent_step: 1\n"
        "estimated_steps: 1\nlocal_checks: []\n---\n\n# no-batch\n\n" + body,
        encoding="utf-8",
    )
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "feat: step 0 [plan:no-batch#step-0]")

    result = _run(sp, proj)
    assert result.returncode == 0
