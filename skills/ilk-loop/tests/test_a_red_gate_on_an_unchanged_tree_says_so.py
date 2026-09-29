"""Red-first: a red gate on an unchanged tree gets its own stop reason.

A B2-confirmed red gate sets ``iter_stop_reason="local_checks_failed"``
whether or not the iteration committed anything (run_ilk_loop_claude.sh:5478).
When heads-before == heads-after (0 new commits across all repos), the red is
on code this iteration didn't touch: a red base or an environment.  It should
not read as "the worker broke something".

AC-1, AC-2, AC-3, AC-4 of sub-plan ``a-red-gate-on-an-unchanged-tree-says-so``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parent.parent.parent          # <clone root>
RUNNER = _TESTS.parent / "scripts" / "run_ilk_loop_claude.sh"

sys.path.insert(0, str(_REPO / "skills" / "ilk-feedback" / "scripts"))
import collect  # noqa: E402

SLUG = "a-red-gate-on-unchanged-tree"
STEM = f"2026-09-29-{SLUG}"

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)


# ── the end-to-end harness ───────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _build_world(root: Path) -> dict:
    """A project + isolated data home + a stub ``claude`` that does nothing.

    The stub agent makes zero commits — the iteration's tree is unchanged.
    The sub-plan stays ``in-progress`` (never shipped), so gate enforcement
    reports ``local_checks_failed`` or ``local_checks_failed_no_commits``,
    not ``ship_integrity_violation``.
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

    (plans / "MASTER-2026-09-29-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-09-29-execution\n"
        "batch_date: 2026-09-29\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# MASTER\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "### Step 0 — do the thing\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"python3 -c 'raise SystemExit(1)'\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    # The stub agent does nothing — zero commits, unchanged tree.
    # The gate will be resolved via PRE_ITER_TARGET (the fallback when
    # no commit trailers are found) and will fail, giving us the
    # local_checks_failed_no_commits stop reason.
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)

    return {"project": project, "plans": plans, "data_home": data_home,
            "key": key, "bin": bin_dir}


def _run_one_iteration(world: dict, root: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": str(root),
        "ILK_DATA_HOME": str(world["data_home"]),
        "ILK_SKILL_HOME": str(_REPO / "skills"),
        "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
        "CLAUDE_CONFIG_DIR": str(root / ".claude"),
    }
    env.pop("ILK_DATA_DIR", None)
    (root / ".claude").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(RUNNER),
         "--project-path", str(world["project"]),
         "--max-iterations", "1",
         "--iteration-timeout-min", "2",
         "--run-local-checks"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env=env, cwd=str(root),
    )


@pytest.fixture(scope="module")
def unchanged_tree_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One real runner iteration where the agent makes zero commits."""
    root = tmp_path_factory.mktemp("unchanged-tree-run")
    world = _build_world(root)
    proc = _run_one_iteration(world, root)
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    candidates = [runtime / "launcher" / "last-exit.json",
                  runtime / "last-exit.json"]
    sentinel = next((c for c in candidates if c.is_file()), None)
    return {
        "proc": proc,
        "sentinel": json.loads(sentinel.read_text(encoding="utf-8"))
        if sentinel is not None else None,
        "sentinel_path": sentinel or " or ".join(str(c) for c in candidates),
    }


# ── AC-1: 0 new commits → local_checks_failed_no_commits ────────────────────

@_NEEDS_GTIMEOUT
def test_red_gate_with_no_new_commits_stops_as_local_checks_failed_no_commits(
    unchanged_tree_run: dict,
) -> None:
    """AC-1 — a red gate on an unchanged tree (0 new commits) gets a distinct
    stop reason so it is not misread as "the worker broke something"."""
    proc = unchanged_tree_run["proc"]
    sentinel = unchanged_tree_run["sentinel"]
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    assert sentinel is not None, (
        f"the runner wrote no sentinel at {unchanged_tree_run['sentinel_path']}.\n{tail}"
    )
    assert sentinel.get("state") == "local_checks_failed_no_commits", (
        "a B2-confirmed red gate with 0 new commits (unchanged tree) should "
        "end the run with state=local_checks_failed_no_commits, not "
        f"{sentinel.get('state')!r}. The gate was red on code this iteration "
        "didn't touch.\n"
        f"last 30 lines:\n{tail}"
    )


# ── AC-2: the failed_check field is present ──────────────────────────────────

@_NEEDS_GTIMEOUT
def test_sentinel_carries_failed_check_for_unchanged_tree(
    unchanged_tree_run: dict,
) -> None:
    """AC-2 — the sentinel's ``failed_check`` field must name the failing gate
    so the panel alert can identify it without re-reading the JSONL."""
    sentinel = unchanged_tree_run["sentinel"]
    tail = "\n".join(
        (unchanged_tree_run["proc"].stdout + unchanged_tree_run["proc"].stderr).splitlines()[-30:]
    )
    assert sentinel is not None, (
        f"no sentinel at {unchanged_tree_run['sentinel_path']}.\n{tail}"
    )
    failed = sentinel.get("failed_check")
    assert failed is not None, (
        "the sentinel for local_checks_failed_no_commits has no failed_check "
        "field. The panel alert cannot identify which gate failed.\n"
        f"sentinel keys: {list(sentinel.keys())}\n"
        f"last 30 lines:\n{tail}"
    )


# ── AC-3: the watchdog treats it the same as local_checks_failed ─────────────

def test_watchdog_classifies_local_checks_failed_no_commits_as_block() -> None:
    """AC-3 — the watchdog must not auto-relaunch on a red-base condition.

    Same block action as local_checks_failed, distinct label.
    """
    watchdog = _REPO / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"
    action = subprocess.run(
        ["bash", "-c",
         f"source '{watchdog}' >/dev/null 2>&1; classify_action local-checks-unchanged"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    ).stdout.strip()
    assert action == "block", (
        f"watchdog.sh routes local-checks-unchanged to {action!r}; it must be "
        "'block' (same as local_checks_failed) so the run is not auto-relaunched "
        "on a red base"
    )


# ── AC-4: the postmortem label is honest ─────────────────────────────────────

def test_postmortem_says_gate_red_on_unchanged_tree() -> None:
    """AC-4 — collect.py must classify local_checks_failed_no_commits as
    ``local-checks-unchanged`` with a reason_detail naming the cause."""
    sentinel = {
        "state": "local_checks_failed_no_commits",
        "run_id": "20260929-120000",
        "iteration": 1,
    }
    iters = [{
        "run_id": "20260929-120000", "iteration": 1, "exit_code": 0,
        "new_commits_total": 0, "duration_sec": 120,
        "local_checks": {"outcome": "fail", "command": "python3 -c 'raise SystemExit(1)'"},
    }]

    with patch.object(collect, "read_sentinel", return_value=sentinel):
        with patch.object(collect, "collect_self_hosting_facts", return_value={}):
            label, facts = collect.classify(iters, None, Path("/tmp/fake-project"))

    assert label == "local-checks-unchanged", (
        f"sentinel state=local_checks_failed_no_commits classified as {label!r}; "
        "expected 'local-checks-unchanged'. The postmortem must not launder "
        "the distinct stop reason into the generic local_checks_failed label."
    )
    assert "unchanged tree" in facts.get("reason_detail", ""), (
        f"reason_detail should mention 'unchanged tree'; got {facts.get('reason_detail')!r}"
    )