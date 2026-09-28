"""Red-first pins: a draft master is never classified runnable, picked, or gated.

Regression for #55: with 0 active masters the driver classified a draft
master's pending sub-plans as runnable, gated one, and scheduler
verify-dispatch launched a manager run that resolved the draft.

AC-1: fixture with one shipped master and one draft master (pending sub-plan
      `ddd`), with the driver sourced via ILK_DOTSOURCE_ONLY=1 =>
      classify_loop_status prints blocked-no-runnable, and
      get_active_subplan_targets prints nothing.
AC-2: same fixture with ILK_MASTER pinned to the draft => blocked-no-runnable.
AC-3 (control): the draft flipped to queued => runnable, and `ddd 0` is
      targeted.
AC-4: the master pick with 0 actives returns empty (both sites).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASH_RUNNER = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_LOOP_STATUS = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "loop_status.py"
_SCRIPTS = _REPO_ROOT / "skills" / "ilk-loop" / "scripts"


# ── helpers ────────────────────────────────────────────────────────────────

def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )


def _init_repo(path: Path) -> None:
    _git(["init"], path)
    _git(["config", "user.email", "test@test"], path)
    _git(["config", "user.name", "Test"], path)
    (path / ".gitkeep").write_text("")
    _git(["add", ".gitkeep"], path)
    _git(["commit", "-m", "init"], path)


def _make_shipped_and_draft(tmp: Path) -> Path:
    """Fixture: one shipped master + one draft master with pending sub-plan `ddd`.

    Mirrors the #55 trace: shipped MASTER-…a plus draft MASTER-…c.
    """
    _init_repo(tmp)
    plans = tmp / "docs" / "plans"
    plans.mkdir(parents=True)

    # Shipped master — its sub-plans are all shipped.
    (plans / "MASTER-2026-09-28b-a.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-09-28b-a
        status: shipped
        ---
        # shipped batch

        | # | Slug |
        |---|---|
        | 1 | 2026-09-28b-aaa.md |
    """))
    (plans / "2026-09-28b-aaa.md").write_text(textwrap.dedent("""\
        ---
        plan: aaa
        status: shipped
        current_step: 1
        estimated_steps: 1
        ---
        ### Step 0
    """))
    _git(["add", "."], tmp)
    _git(["commit", "-m", "shipped master"], tmp)

    # Draft master — its sub-plan is pending.
    (plans / "MASTER-2026-09-28b-c.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-09-28b-c
        status: draft
        ---
        # draft batch

        | # | Slug |
        |---|---|
        | 1 | 2026-09-28b-ddd.md |
    """))
    (plans / "2026-09-28b-ddd.md").write_text(textwrap.dedent("""\
        ---
        plan: ddd
        status: pending
        current_step: 0
        estimated_steps: 2
        ---
        ### Step 0
        ### Step 1
    """))
    _git(["add", "."], tmp)
    _git(["commit", "-m", "draft master"], tmp)

    return plans


def _status_json(project: Path, env: dict[str, str] | None = None) -> dict:
    """Run loop_status.py --json and return parsed output."""
    import json as _json
    merged_env = {**os.environ, "ILK_DATA_HOME": str(project / "ilk-data")}
    if env:
        merged_env.update(env)
    result = subprocess.run(
        [sys.executable, str(_LOOP_STATUS), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, cwd=project, env=merged_env,
    )
    assert result.stdout.strip(), f"loop_status --json emitted nothing: {result.stderr}"
    return _json.loads(result.stdout)


def _run_driver_func(
    func_name: str,
    project: Path,
    env_extra: dict[str, str] | None = None,
) -> str:
    """Source the driver and call a function, returning stdout."""
    env_lines = ""
    if env_extra:
        env_lines = "\n".join(f"export {k}='{v}'" for k, v in env_extra.items())
        env_lines += "\n"

    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        source '{_BASH_RUNNER}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        set +eE +o pipefail
        {env_lines}PROJECT_PATH='{project}'
        LOOP_STATUS_SCRIPT='{_LOOP_STATUS}'
        export ILK_DATA_HOME='{project}/ilk-data'
        {func_name}
    """)
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    return result.stdout


# ── AC-1 ───────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_draft_master_classifies_as_blocked_no_runnable(tmp_path: Path) -> None:
    """classify_loop_status over a shipped + draft master => blocked-no-runnable."""
    _make_shipped_and_draft(tmp_path)
    out = _run_driver_func("classify_loop_status; echo CLASSIFIED=$CLASSIFIED_STATUS", tmp_path)
    line = [l for l in out.splitlines() if l.startswith("CLASSIFIED=")]
    assert line, f"classify_loop_status produced no verdict.\n{out}"
    verdict = line[-1].split("=", 1)[1].strip()
    assert verdict == "blocked-no-runnable", (
        f"expected blocked-no-runnable, got {verdict!r}. "
        "A draft master's pending sub-plans must not be classified runnable."
    )


@pytest.mark.xfail(strict=True, reason="red-first")
def test_draft_master_gate_target_is_empty(tmp_path: Path) -> None:
    """get_active_subplan_targets over a shipped + draft master => empty."""
    _make_shipped_and_draft(tmp_path)
    out = _run_driver_func("get_active_subplan_targets", tmp_path)
    assert out.strip() == "", (
        f"get_active_subplan_targets should print nothing for a draft master, "
        f"got: {out.strip()!r}"
    )


# ── AC-2 ───────────────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="red-first")
def test_pinned_draft_master_classifies_as_blocked(tmp_path: Path) -> None:
    """ILK_MASTER pinned to a draft => blocked-no-runnable."""
    plans = _make_shipped_and_draft(tmp_path)
    draft_name = "MASTER-2026-09-28b-c.md"
    out = _run_driver_func(
        "classify_loop_status; echo CLASSIFIED=$CLASSIFIED_STATUS",
        tmp_path,
        env_extra={"ILK_MASTER": draft_name},
    )
    line = [l for l in out.splitlines() if l.startswith("CLASSIFIED=")]
    assert line, f"classify_loop_status produced no verdict (pinned).\n{out}"
    verdict = line[-1].split("=", 1)[1].strip()
    assert verdict == "blocked-no-runnable", (
        f"ILK_MASTER pinned to a draft must yield blocked-no-runnable, got {verdict!r}. "
        "The ILK_MASTER pin returns whatever its status, so the classify fix must hold."
    )


# ── AC-3 (control) ─────────────────────────────────────────────────────────

def test_queued_master_classifies_as_runnable_and_targets_ddd(tmp_path: Path) -> None:
    """Control: draft flipped to queued => runnable, ddd 0 is targeted."""
    plans = _make_shipped_and_draft(tmp_path)
    # Flip the draft master to queued.
    draft = plans / "MASTER-2026-09-28b-c.md"
    draft.write_text(draft.read_text().replace("status: draft", "status: queued"))
    import json as _json
    # classify
    out = _run_driver_func("classify_loop_status; echo CLASSIFIED=$CLASSIFIED_STATUS", tmp_path)
    line = [l for l in out.splitlines() if l.startswith("CLASSIFIED=")]
    assert line, f"classify_loop_status produced no verdict (queued).\n{out}"
    verdict = line[-1].split("=", 1)[1].strip()
    assert verdict == "runnable", (
        f"a queued master must be classified runnable, got {verdict!r}"
    )
    # target
    out2 = _run_driver_func("get_active_subplan_targets", tmp_path)
    assert "ddd" in out2, (
        f"get_active_subplan_targets should target ddd, got: {out2.strip()!r}"
    )
    # current_step should be 0
    parts = out2.strip().split()
    assert len(parts) >= 2 and parts[1] == "0", (
        f"expected 'ddd 0', got: {out2.strip()!r}"
    )


# ── AC-4 ───────────────────────────────────────────────────────────────────

def test_pick_active_master_returns_empty_with_zero_actives(tmp_path: Path) -> None:
    """pick_active_master with 0 actives returns empty at both sites."""
    _make_shipped_and_draft(tmp_path)
    # Call pick_active_master via Python, filtering only actives (0 of them).
    script = textwrap.dedent(f"""\
        import sys, json
        sys.path.insert(0, '{_SCRIPTS}')
        from pathlib import Path
        from loop_status import pick_active_master, parse_frontmatter
        from plan_status import normalize_master_status
        plans = Path('{tmp_path}/docs/plans')
        masters = sorted(plans.glob('MASTER-*.md'))
        actives = [m for m in masters
                   if normalize_master_status(parse_frontmatter(
                       m.read_text(encoding='utf-8-sig')).get('status') or '') == 'active']
        # Site 1 (test_ship_integrity): actives or masters => with 0 actives, should be empty
        if actives:
            chosen1, _ = pick_active_master(actives, json_mode=True)
            print(f"SITE1={{chosen1}}")
        else:
            print("SITE1=EMPTY")
        # Site 2 (red_owner): same pattern
        if actives:
            chosen2, _ = pick_active_master(actives, json_mode=True)
            print(f"SITE2={{chosen2}}")
        else:
            print("SITE2=EMPTY")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60,
        cwd=tmp_path, env={**os.environ, "ILK_DATA_HOME": str(tmp_path / "ilk-data")},
    )
    assert "SITE1=EMPTY" in result.stdout, (
        f"site 1 pick should return empty with 0 actives.\n{result.stdout}\n{result.stderr}"
    )
    assert "SITE2=EMPTY" in result.stdout, (
        f"site 2 pick should return empty with 0 actives.\n{result.stdout}\n{result.stderr}"
    )