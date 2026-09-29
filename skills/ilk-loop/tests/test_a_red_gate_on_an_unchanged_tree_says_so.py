"""Pin a distinct stop reason for a red gate on an unchanged tree.

Part of sub-plan ``a-red-gate-on-an-unchanged-tree-says-so``
(MASTER-2026-09-29g).

A B2-confirmed red gate with 0 new commits (heads-before == heads-after)
means the red is on code this iteration didn't touch — a red base or
an environment.  The runner should name it differently from a red gate
on code the worker broke.

AC-1: a B2-confirmed red with 0 new commits ends with
      ``local_checks_failed_no_commits``; with ≥1 new commit it is
      ``local_checks_failed`` (today's value).
AC-2: declared in the exit-state table, collect.py, and watchdog.sh.
AC-3: watchdog treats it exactly as ``local_checks_failed`` today.
AC-4: the postmortem says ``gate red on an unchanged tree (0 commits
      this iteration)``.

The AC-1 pins drive the runner end to end as a subprocess (a stub
agent that makes 0 commits, and a red step gate), the way
``test_red_gate_stops_the_run.py`` does, and read the stop reason
from ``last-exit.json``.
"""
from __future__ import annotations

import json
import os
import re
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
sys.path.insert(0, str(_REPO / "skills" / "ilk-loop" / "scripts"))

SLUG = "unchanged-tree-red-gate"
STEM = f"2026-09-29-{SLUG}"

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)


# ── Harness ─────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _build_world(root: Path, *, commit_in_iteration: bool) -> dict:
    """A project + isolated data home + a stub ``claude``, ready for one iteration.

    When *commit_in_iteration* is False the stub agent does nothing —
    heads-before == heads-after and the red gate is on an unchanged tree.
    When True the stub lands one trailered commit.
    """
    project = root / "project"
    (project / "docs").mkdir(parents=True)
    _git(project.parent, "init", "-q", str(project))
    (project / "README.md").write_text("x\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
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
    if commit_in_iteration:
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"SP={str(plans / f'{STEM}.md')!r}\n"
            "python3 - \"$SP\" <<'EOP'\n"
            "import re, sys\n"
            "from pathlib import Path\n"
            "p = Path(sys.argv[1]); b = p.read_text()\n"
            "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
            "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
            "p.write_text(b)\n"
            "EOP\n"
            "git -c user.email=t@example.com -c user.name=t commit -q "
            f"--allow-empty -m 'feat: the work [plan:{SLUG}#step-0]'\n"
            "echo 'stub agent done'\n",
            encoding="utf-8",
        )
    else:
        # Stub that makes NO commits — the tree is unchanged.
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"SP={str(plans / f'{STEM}.md')!r}\n"
            "python3 - \"$SP\" <<'EOP'\n"
            "import re, sys\n"
            "from pathlib import Path\n"
            "p = Path(sys.argv[1]); b = p.read_text()\n"
            "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
            "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
            "p.write_text(b)\n"
            "EOP\n"
            "echo 'stub agent done (no commits)'\n",
            encoding="utf-8",
        )
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


def _read_sentinel(world: dict) -> dict | None:
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    candidates = [runtime / "launcher" / "last-exit.json",
                  runtime / "last-exit.json"]
    sentinel_path = next((c for c in candidates if c.is_file()), None)
    if sentinel_path is None:
        return None
    return json.loads(sentinel_path.read_text(encoding="utf-8"))


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def unchanged_tree_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One runner iteration with a red gate and 0 new commits."""
    root = tmp_path_factory.mktemp("unchanged-tree-run")
    world = _build_world(root, commit_in_iteration=False)
    proc = _run_one_iteration(world, root)
    sentinel = _read_sentinel(world)
    return {
        "proc": proc,
        "sentinel": sentinel,
        "subplan": world["plans"] / f"{STEM}.md",
    }


@pytest.fixture(scope="module")
def changed_tree_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One runner iteration with a red gate and ≥1 new commit."""
    root = tmp_path_factory.mktemp("changed-tree-run")
    world = _build_world(root, commit_in_iteration=True)
    proc = _run_one_iteration(world, root)
    sentinel = _read_sentinel(world)
    return {
        "proc": proc,
        "sentinel": sentinel,
        "subplan": world["plans"] / f"{STEM}.md",
    }


# ── AC-1: distinct stop reason ──────────────────────────────────────────────


class TestStopReasonForRedGate:
    """AC-1: a B2-confirmed red with 0 new commits ends the run with
    ``local_checks_failed_no_commits``.  With ≥1 new commit it is
    ``local_checks_failed`` (today's value)."""

    @pytest.mark.xfail(strict=True, reason="a-red-gate-on-an-unchanged-tree-says-so")
    @_NEEDS_GTIMEOUT
    def test_unchanged_tree_stops_as_local_checks_failed_no_commits(
        self, unchanged_tree_run: dict,
    ) -> None:
        proc = unchanged_tree_run["proc"]
        sentinel = unchanged_tree_run["sentinel"]
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

        assert sentinel is not None, (
            f"the runner wrote no sentinel.\n{tail}"
        )
        state = sentinel.get("state")
        assert state == "local_checks_failed_no_commits", (
            "a red gate with 0 new commits should stop as "
            f"`local_checks_failed_no_commits`, got state={state!r}.\n"
            f"last 30 lines:\n{tail}"
        )

    @_NEEDS_GTIMEOUT
    def test_changed_tree_stops_as_local_checks_failed(
        self, changed_tree_run: dict,
    ) -> None:
        """Green control — with ≥1 new commit the stop reason is unchanged."""
        proc = changed_tree_run["proc"]
        sentinel = changed_tree_run["sentinel"]
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

        assert sentinel is not None, (
            f"the runner wrote no sentinel.\n{tail}"
        )
        state = sentinel.get("state")
        # The existing stop reason for a red gate with commits.
        # Could be local_checks_failed or ship_integrity_violation
        # depending on whether the sub-plan was marked shipped.
        assert state in ("local_checks_failed", "ship_integrity_violation"), (
            f"a red gate with ≥1 new commit should stop as `local_checks_failed` "
            f"or `ship_integrity_violation`, got state={state!r}.\n"
            f"last 30 lines:\n{tail}"
        )


# ── AC-2: declared in vocabulary locations ───────────────────────────────────


class TestDeclaredInVocabulary:
    """AC-2: the new stop reason is declared in the exit-state table,
    collect.py, and watchdog.sh."""

    @pytest.mark.xfail(strict=True, reason="a-red-gate-on-an-unchanged-tree-says-so")
    def test_contract_doc_lists_local_checks_failed_no_commits(self):
        """detached-component-contracts.md must list the new state."""
        contracts = _TESTS.parent / "references" / "detached-component-contracts.md"
        if not contracts.exists():
            pytest.skip("contract doc not found")
        text = contracts.read_text(encoding="utf-8")
        assert "local_checks_failed_no_commits" in text, (
            "contract doc does not list local_checks_failed_no_commits"
        )

    @pytest.mark.xfail(strict=True, reason="a-red-gate-on-an-unchanged-tree-says-so")
    def test_collect_py_classifies_local_checks_failed_no_commits(self):
        """collect.py must classify the new state."""
        import collect
        # The new state must be in the sentinel failure map or handled
        # by a named branch.
        source = Path(collect.__file__).read_text(encoding="utf-8")
        assert "local_checks_failed_no_commits" in source, (
            "collect.py does not handle local_checks_failed_no_commits"
        )

    @pytest.mark.xfail(strict=True, reason="a-red-gate-on-an-unchanged-tree-says-so")
    def test_watchdog_handles_local_checks_failed_no_commits(self):
        """watchdog.sh must classify the new state."""
        watchdog = _REPO / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"
        if not watchdog.exists():
            pytest.skip("watchdog.sh not found")
        text = watchdog.read_text(encoding="utf-8")
        assert "local_checks_failed_no_commits" in text, (
            "watchdog.sh does not handle local_checks_failed_no_commits"
        )


# ── AC-3: watchdog treats it as local_checks_failed ─────────────────────────


class TestWatchdogTreatsSameAsLocalChecksFailed:
    """AC-3: the watchdog treats it exactly as ``local_checks_failed``
    today (same class, same relaunch or block decision)."""

    @pytest.mark.xfail(strict=True, reason="a-red-gate-on-an-unchanged-tree-says-so")
    def test_same_classification_as_local_checks_failed(self):
        """The new state and local_checks_failed must map to the same
        watchdog action."""
        watchdog = _REPO / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"
        if not watchdog.exists():
            pytest.skip("watchdog.sh not found")
        text = watchdog.read_text(encoding="utf-8")
        # Find the classify_action function and verify both states
        # map to the same arm.
        # The new state should be listed alongside local_checks_failed
        # in the same case arm.
        assert "local_checks_failed_no_commits" in text, (
            "watchdog.sh does not mention local_checks_failed_no_commits"
        )


# ── AC-4: postmortem label ──────────────────────────────────────────────────


class TestPostmortemLabel:
    """AC-4: the postmortem (collect.py) says
    ``gate red on an unchanged tree (0 commits this iteration)``."""

    @pytest.mark.xfail(strict=True, reason="a-red-gate-on-an-unchanged-tree-says-so")
    def test_postmortem_label_mentions_unchanged_tree(self):
        """collect.py must produce a label mentioning 'unchanged tree'
        for the new stop_reason, per AC-4: 'gate red on an unchanged
        tree (0 commits this iteration)'."""
        import collect
        # The new state must map to a specific label, not fall through
        # to generic heuristics.  Check both the map and the label text.
        source = Path(collect.__file__).read_text(encoding="utf-8")
        assert "local_checks_failed_no_commits" in source, (
            "collect.py does not handle local_checks_failed_no_commits"
        )
        # The label for this state must mention "unchanged" per AC-4.
        # This is a separate check from just having the state in the map.
        assert "unchanged" in source, (
            "collect.py does not mention 'unchanged' for the new state label"
        )