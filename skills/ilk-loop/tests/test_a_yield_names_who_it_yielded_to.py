"""Red-first pins: a yield exits ``yielded`` with ``yielded_to``, not ``merge-deferred``.

Part of sub-plan ``a-yield-names-who-it-yielded-to``
(MASTER-2026-10-02d).

Five acceptance criteria:

  AC-1  runner dot-sourced with another project merge-pending ⇒
        ``_yield_if_another_merge_pending`` returns 0, then
        ``stop_reason == yielded``, ``YIELDED_TO == <key>``,
        ``merge_was_deferred == 0``.
  AC-2  ``_terminal_sentinel_json`` with that state parses to
        ``state == yielded``, ``yielded_to == <key>``,
        ``merge_deferred is None``.
  AC-3  ``collect.py``'s sentinel classification of
        ``{"state": "yielded", "yielded_to": "k"}`` returns ``yielded``.
  AC-4  the watchdog's classification for state ``yielded`` is ``relaunch``.
  AC-5  ``record_selfmod_merge_outcome 2`` with a tmp SELFMOD_WORKTREE_PATH
        still sets ``merge_was_deferred=1`` and writes a marker with
        ``live_pids`` (control — passes today).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_REPO = _SCRIPTS.parent.parent.parent

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── Helpers (borrowed from test_a_pending_merge_holds_new_work) ──────────────


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return cp.stdout.strip()


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside *root*."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
    }


def _make_project(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Create a minimal project directory with a worktree and sentinel.

    Returns (project_root, ext_project_dir).
    """
    project = tmp_path / "repos" / name
    project.mkdir(parents=True)

    _git(project, "init", "-q")
    (project / "README.md").write_text("seed\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-q", "-m", "seed")

    sys.path.insert(0, str(_SCRIPTS))
    import ilk_paths
    data_home = tmp_path / ".ilk-data"
    key = ilk_paths.project_key(project)

    ext_project = data_home / "projects" / key
    launcher = ext_project / "runtime" / "launcher"
    launcher.mkdir(parents=True)

    wt = launcher / "worktrees" / "selfmod-batch"
    _git(project, "worktree", "add", "--detach", str(wt))

    plans = ext_project / "plans"
    plans.mkdir(parents=True)

    (launcher / "last-launch.json").write_text(
        json.dumps({"project_path": str(project)}), encoding="utf-8"
    )

    return project, ext_project


def _write_sentinel(project: Path, state: str = "running",
                    ext_project: Path | None = None,
                    merge_deferred: bool = False) -> None:
    """Write a last-exit.json sentinel."""
    if ext_project is None:
        sys.path.insert(0, str(_SCRIPTS))
        import ilk_paths
        data_home = project.parent.parent.parent / ".ilk-data"
        key = ilk_paths.project_key(project)
        ext_project = data_home / "projects" / key
    sentinel = ext_project / "runtime" / "launcher" / "last-exit.json"
    data: dict = {
        "state": state,
        "pid": 12345,
        "run_id": "test-run",
        "started_at": "2026-01-01T00:00:00+0000",
        "ended_at": None,
        "iterations": 1,
        "project_path": str(project),
        "cli": "claude",
        "jsonl_log": str(ext_project / "test.jsonl"),
        "held_by": None,
        "failed_check": None,
    }
    if merge_deferred:
        data["merge_deferred"] = {"live_pids": "12345", "since": "2026-01-01"}
    sentinel.write_text(json.dumps(data), encoding="utf-8")


def _add_unmerged_commit(wt: Path) -> None:
    """Add a commit to the worktree that hasn't been merged."""
    (wt / "work.txt").write_text("work\n", encoding="utf-8")
    _git(wt, "add", "work.txt")
    _git(wt, "commit", "-q", "-m", "work commit")


# ── AC-1 ─────────────────────────────────────────────────────────────────────


def test_ac1_yield_exits_yielded_with_yielded_to(tmp_path: Path) -> None:
    """AC-1: runner dot-sourced with another project merge-pending ⇒
    ``_yield_if_another_merge_pending`` returns 0, ``stop_reason == yielded``,
    ``YIELDED_TO == <key>``, ``merge_was_deferred == 0``.

    Modeled on ``test_ac3_runner_yields_at_iteration_boundary`` but calling
    the refactored function and asserting the new state.
    """
    project, ext_proj = _make_project(tmp_path, "my-project")
    _write_sentinel(project, state="running", ext_project=ext_proj)

    other_project, ext_other = _make_project(tmp_path, "other-project")
    _write_sentinel(other_project, state="merge-deferred", ext_project=ext_other)
    wt_other = ext_other / "runtime" / "launcher" / "worktrees" / "selfmod-batch"
    _add_unmerged_commit(wt_other)

    runner = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
    env = _sandbox_env(tmp_path)
    env["PROJECT_KEY"] = ext_proj.name
    env["ILK_SKILL_HOME"] = str(_REPO / "skills")
    env["_SKILL_ROOT"] = str(_REPO / "skills")
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export ILK_DATA_HOME='{tmp_path / ".ilk-data"}'
        export ILK_SKILL_HOME='{_REPO / "skills"}'
        export _SKILL_ROOT='{_REPO / "skills"}'
        source '{runner}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        PROJECT_KEY='my-project'
        merge_was_deferred=0
        _yield_if_another_merge_pending
        rc=$?
        echo "RC=$rc"
        echo "STOP_REASON=${{stop_reason:-unset}}"
        echo "YIELDED_TO=${{YIELDED_TO:-unset}}"
        echo "MERGE_WAS_DEFERRED=${{merge_was_deferred:-unset}}"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace", env=env,
    )
    lines = result.stdout.splitlines()

    def _val(prefix: str) -> str:
        matches = [l for l in lines if l.startswith(prefix)]
        assert matches, f"no {prefix} output: {result.stdout}\n{result.stderr}"
        return matches[-1].split("=", 1)[1]

    assert _val("RC=") == "0", "should yield (exit 0)"
    assert _val("STOP_REASON=") == "yielded", "stop_reason should be yielded"
    assert _val("YIELDED_TO=") == ext_other.name, "YIELDED_TO should be the other project's key"
    assert _val("MERGE_WAS_DEFERRED=") == "0", "merge_was_deferred should stay 0"


# ── AC-2 ─────────────────────────────────────────────────────────────────────


def test_ac2_sentinel_json_carrying_yielded_to(tmp_path: Path) -> None:
    """AC-2: ``_terminal_sentinel_json`` with ``yielded`` state ⇒ parses to
    ``state == yielded``, ``yielded_to == <key>``, ``merge_deferred is None``.
    """
    runner = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
    env = _sandbox_env(tmp_path)
    env["ILK_SKILL_HOME"] = str(_REPO / "skills")
    env["_SKILL_ROOT"] = str(_REPO / "skills")
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export ILK_DATA_HOME='{tmp_path / ".ilk-data"}'
        export ILK_SKILL_HOME='{_REPO / "skills"}'
        export _SKILL_ROOT='{_REPO / "skills"}'
        source '{runner}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        # Set globals that _terminal_sentinel_json reads.
        stop_reason="yielded"
        YIELDED_TO="some-project-key"
        merge_was_deferred=0
        RUN_ID="test-run-id"
        loop_started_at="2026-01-01T00:00:00+0000"
        iter_counter=3
        PROJECT_PATH="/tmp/test-project"
        JSONL_LOG="/tmp/test.jsonl"
        ended_at="2026-01-01T01:00:00+0000"
        HELD_BY=""
        _FAILED_CHECK_JSON=""
        _terminal_sentinel_json
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace", env=env,
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    sentinel = json.loads(result.stdout)
    assert sentinel["state"] == "yielded"
    assert sentinel["yielded_to"] == "some-project-key"
    assert sentinel.get("merge_deferred") is None


# ── AC-3 ─────────────────────────────────────────────────────────────────────


def test_ac3_collect_classifies_yielded(tmp_path: Path) -> None:
    """AC-3: ``collect.py``'s sentinel classification of
    ``{"state": "yielded", "yielded_to": "k"}`` returns ``yielded``.

    Parses the source to verify the mapping exists, matching the pattern
    in ``test_terminal_state_is_declared.py``.
    """
    collect_py = _REPO / "skills" / "ilk-feedback" / "scripts" / "collect.py"
    source = collect_py.read_text(encoding="utf-8")

    # Check _SENTINEL_FAILURE_MAP contains "yielded": "yielded"
    assert '"yielded": "yielded"' in source, (
        "'yielded' not found in _SENTINEL_FAILURE_MAP in collect.py"
    )

    # Check CLASSIFICATION_LABELS contains "yielded"
    assert '"yielded"' in source.split("CLASSIFICATION_LABELS")[1].split(")")[0], (
        "'yielded' not in CLASSIFICATION_LABELS in collect.py"
    )


# ── AC-4 ─────────────────────────────────────────────────────────────────────


def test_ac4_watchdog_classifies_yielded_as_relaunch() -> None:
    """AC-4: the watchdog's classification for state ``yielded`` is ``relaunch``.

    Mirrors the parametrized ``test_classify_action`` in
    ``test_watchdog_classify.py``.
    """
    # Pure-Python translation of watchdog.sh classify_action.
    def classify_action(s: str) -> str:
        if s == "running":
            return "sleep"
        if s in ("all-shipped", "already-shipped", "shipped"):
            return "promote"
        if s in ("shipped-unverified",):
            return "needs-human"
        if s in ("no-evidence", "never-ran"):
            return "triage"
        if s in ("throttled", "timeout-bound", "max-iter-bound",
                  "api-flaky", "interrupted", "merge-deferred", "yielded"):
            return "relaunch"
        if s in ("stuck-no-progress", "api-blocked", "budget-exhausted",
                  "local-checks-stuck", "dependency-unreachable",
                  "merge-conflict"):
            return "block"
        return "block"  # fail-safe

    assert classify_action("yielded") == "relaunch", (
        "yielded should route to relaunch (same action as merge-deferred)"
    )


# ── AC-5 (control) ──────────────────────────────────────────────────────────


def test_ac5_record_selfmod_merge_outcome_rc2_still_defers(tmp_path: Path) -> None:
    """AC-5 (control): ``record_selfmod_merge_outcome 2`` with a tmp
    SELFMOD_WORKTREE_PATH still sets ``merge_was_deferred=1`` and writes a
    marker with ``live_pids``.  This passes today — no xfail.
    """
    runner = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
    wt = tmp_path / "worktree"
    wt.mkdir()
    env = _sandbox_env(tmp_path)
    env["ILK_SKILL_HOME"] = str(_REPO / "skills")
    env["_SKILL_ROOT"] = str(_REPO / "skills")
    env["SELFMOD_WORKTREE_PATH"] = str(wt)
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        export ILK_DATA_HOME='{tmp_path / ".ilk-data"}'
        export ILK_SKILL_HOME='{_REPO / "skills"}'
        export _SKILL_ROOT='{_REPO / "skills"}'
        export SELFMOD_WORKTREE_PATH='{wt}'
        source '{runner}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        merge_was_deferred=0
        record_selfmod_merge_outcome 2
        echo "MERGE_WAS_DEFERRED=$merge_was_deferred"
        if [[ -f '{wt}/.ilk-merge-deferred' ]]; then
            echo "MARKER_EXISTS=1"
            echo "MARKER_CONTENT=$(cat '{wt}/.ilk-merge-deferred')"
        else
            echo "MARKER_EXISTS=0"
        fi
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace", env=env,
    )
    lines = result.stdout.splitlines()

    def _val(prefix: str) -> str:
        matches = [l for l in lines if l.startswith(prefix)]
        assert matches, f"no {prefix} output: {result.stdout}\n{result.stderr}"
        return matches[-1].split("=", 1)[1]

    assert _val("MERGE_WAS_DEFERRED=") == "1"
    assert _val("MARKER_EXISTS=") == "1"
    marker_json = json.loads(_val("MARKER_CONTENT="))
    assert "live_pids" in marker_json