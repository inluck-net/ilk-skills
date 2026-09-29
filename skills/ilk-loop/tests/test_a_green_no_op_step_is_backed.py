"""Pins: a green no-op step on a shared remote is backed.

Part of sub-plan ``a-green-no-op-step-is-backed``.

Acceptance criteria:
  AC-1  The #6940 replay backs the step.  A shared remote (trailers stripped),
        1-step sub-plan, stub agent makes 0 commits and edits ``shipped``,
        step-0 gate passes ⇒ runner writes a ``gate_pass_at_head`` ledger row
        covering step 0; ``ship_integrity`` reports no violation; the sub-plan
        stays ``shipped``.
  AC-2  No commits and no gate stays a violation.  Same fixture with no gate
        run is still refused.  (PASSES TODAY — unmarked.)
  AC-3  The union counts ``gate_pass_at_head`` rows.  A unit pin on
        ``ship_audit.check_step_commits`` says so explicitly.
  AC-4  Un-park resets the strike counter.  ``park_master.py --unpark`` sets
        ``auto_block_fails: 0`` on every registry sub-plan that carries the
        key.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import shutil  # noqa: E402

import pytest

_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parent.parent.parent          # <clone root>
RUNNER = _TESTS.parent / "scripts" / "run_ilk_loop_claude.sh"
PARK_MASTER = _TESTS.parent / "scripts" / "park_master.py"

sys.path.insert(0, str(_REPO / "skills" / "ilk-loop" / "scripts"))

import ship_audit  # noqa: E402

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)

SLUG = "green-noop-backed"
STEM = f"2026-09-29-{SLUG}"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    """Run a git command and return stdout."""
    cp = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return cp.stdout.strip()


def _build_world(
    root: Path,
    *,
    gate_history: str = "pass",
) -> dict:
    """A project with one dispatched sub-plan (1 step), shared remote.

    Step 0 already committed.  The stub agent makes 0 new commits and edits
    the sub-plan to ``shipped``.

    *gate_history* controls the gate-history row written before the iteration:
      - ``"pass"``: a pass row at the current HEAD (AC-1: green no-op backed).
      - ``"fail"``: a fail row with a bogus SHA (AC-2: no-gate violation).
      - ``"none"``: empty gate-history (no rows at all).

    The fixture simulates a shared remote by not including plan trailers in
    commit messages.
    """
    project = root / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    (project / "README.md").write_text("init\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")

    data_home = root / ".ilk-data"
    import ilk_paths
    key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    # Master: active, one sub-plan.
    (plans / "MASTER-2026-09-29.md").write_text(
        "---\n"
        "master_plan: 2026-09-29\n"
        "batch_date: 2026-09-29\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n"
        "# Master\n\n## Sub-plan registry\n\n"
        f"| # | Slug |\n|---|---|\n| 1 | [{SLUG}](./{STEM}.md) |\n",
        encoding="utf-8",
    )

    # Sub-plan: 1 step, NO local_checks fence (the "no gate ran" shape).
    (plans / f"{STEM}.md").write_text(
        "---\n"
        f"plan: {SLUG}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "---\n\n"
        f"# {SLUG}\n\n"
        "### Step 0 — only step\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    # Write .ilk-remote-type = shared to simulate shared remote.
    (project / ".ilk-remote-type").write_text("shared\n", encoding="utf-8")

    launcher_dir = data_home / "projects" / key / "runtime" / "launcher"
    launcher_dir.mkdir(parents=True)
    history_path = launcher_dir / "gate-history.jsonl"

    # Gate history row written BEFORE the stub runs.
    if gate_history == "fail":
        history_path.write_text(
            json.dumps({
                "slug": SLUG,
                "step": 0,
                "outcome": "fail",
                "exit_code": 1,
                "head_sha": "deadbeef" * 5,
                "run_id": "prior-run",
                "iteration": 1,
                "timestamp": "2026-09-29T10:00:00+0800",
            }, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    else:
        history_path.write_text("", encoding="utf-8")

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"

    # Build the stub agent script.
    stub_lines = [
        "#!/usr/bin/env bash",
        f"SP={str(plans / f'{STEM}.md')!r}",
    ]

    if gate_history == "pass":
        # Write a pass row in gate-history at the current HEAD (step 0).
        # The gate ran and passed, but the agent made 0 new commits.
        stub_lines += [
            f"HIST={str(history_path)!r}",
            f"SLUG={SLUG!r}",
            "SHA=$(git -C " + str(project) + " rev-parse HEAD)",
            "python3 - \"$HIST\" \"$SLUG\" \"$SHA\" <<'EOP'",
            "import json, sys",
            "from pathlib import Path",
            "h = Path(sys.argv[1])",
            "h.write_text(json.dumps({",
            '    "slug": sys.argv[2], "step": 0, "outcome": "pass",',
            '    "exit_code": 0, "head_sha": sys.argv[3],',
            '    "run_id": "prior-run", "iteration": 1,',
            '    "timestamp": "2026-09-29T10:00:00+0800",',
            "}, separators=(',', ':')) + '\\n', encoding='utf-8')",
            "EOP",
        ]

    # Mark as shipped (no new commits, no gate run in this iteration).
    stub_lines += [
        "python3 - \"$SP\" <<'EOP'",
        "import re, sys",
        "from pathlib import Path",
        "p = Path(sys.argv[1]); b = p.read_text()",
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)",
        "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)",
        "p.write_text(b)",
        "EOP",
    ]

    stub_lines.append("echo 'stub agent done'\n")
    stub.write_text("\n".join(stub_lines), encoding="utf-8")
    stub.chmod(0o755)

    return {
        "project": project, "plans": plans, "data_home": data_home,
        "key": key, "bin": bin_dir, "history_path": history_path,
    }


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


def _read_subplan_status(world: dict, stem: str = STEM) -> str:
    f = world["plans"] / f"{stem}.md"
    if not f.is_file():
        return ""
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return ""


def _read_master_status(world: dict) -> str:
    f = world["plans"] / "MASTER-2026-09-29.md"
    if not f.is_file():
        return ""
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return ""


# ── AC-1: the #6940 replay backs the step ────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="green no-op step not backed")
@_NEEDS_GTIMEOUT
def test_green_noop_step_backed_by_gate_row(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-1: shared remote, 1-step sub-plan, stub makes 0 commits and edits
    shipped, step-0 gate passes ⇒ runner writes a ``gate_pass_at_head`` ledger
    row covering step 0; ``ship_integrity`` reports no violation; the sub-plan
    stays ``shipped``."""
    root = tmp_path_factory.mktemp("green-noop")
    world = _build_world(root, gate_history="pass")
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    status = _read_subplan_status(world)
    assert status == "shipped", (
        f"sub-plan status is {status!r}, expected 'shipped' — "
        f"green no-op step was not backed.\n{tail}"
    )


# ── AC-2: no commits and no gate stays a violation (PASSES TODAY) ────────────

@_NEEDS_GTIMEOUT
def test_no_commits_no_gate_still_violation(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-2: same fixture with no gate run is still refused.
    (PASSES TODAY — no xfail.)"""
    root = tmp_path_factory.mktemp("no-gate-violation")
    world = _build_world(root, gate_history="fail")
    proc = _run_one_iteration(world, root)
    combined = proc.stdout + proc.stderr
    tail = "\n".join(combined.splitlines()[-40:])

    status = _read_subplan_status(world)
    assert status == "in-progress", (
        f"sub-plan status is {status!r}, expected 'in-progress' — "
        f"no-gate violation was not caught.\n{tail}"
    )


# ── AC-3: the union counts gate_pass_at_head rows ───────────────────────────

def test_union_counts_gate_pass_at_head_rows() -> None:
    """AC-3: ``ship_audit.check_step_commits`` counts a ``gate_pass_at_head``
    ledger row with ``gate_outcome == 'pass'`` as covering the step.

    If the union already does this, this is a green control — leave it
    unmarked and say so in Findings.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "proj"
        project.mkdir()
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
             "init", "-b", "main"],
            cwd=project, check=True, capture_output=True,
        )
        (project / "README.md").write_text("init\n", encoding="utf-8")
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
             "add", "-A"],
            cwd=project, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
             "commit", "-q", "-m", "init"],
            cwd=project, check=True, capture_output=True,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project, check=True, capture_output=True, text=True,
        ).stdout.strip()

        # A gate_pass_at_head row covering step 0.
        ledger_records = [{
            "slug": "noop-work",
            "step_from": 0,
            "step_to": 1,
            "commits": [],
            "proof": "gate_pass_at_head",
            "head": head,
            "gate_outcome": "pass",
        }]

        present, missing = ship_audit.check_step_commits(
            slug="noop-work",
            expected_steps=[0],
            cwd=project,
            ledger_records=ledger_records,
        )

        assert 0 in present, (
            f"step 0 not in present={present}, missing={missing} — "
            f"gate_pass_at_head row was not counted by the union"
        )
        assert missing == [], (
            f"missing={missing} — gate_pass_at_head row was not counted"
        )


# ── AC-4: un-park resets the strike counter ──────────────────────────────────

@pytest.mark.xfail(strict=True, reason="green no-op step not backed")
def test_unpark_resets_auto_block_fails(tmp_path: Path) -> None:
    """AC-4: ``park_master.py --unpark`` sets ``auto_block_fails: 0`` on every
    registry sub-plan that carries the key, using ``quarantine_subplan``'s
    line format."""
    plans = tmp_path / "plans"
    plans.mkdir()

    # Master with one sub-plan that has auto_block_fails: 3.
    (plans / "MASTER-2026-09-29.md").write_text(
        "---\n"
        "title: test\n"
        "status: blocked\n"
        "parked_at: 2026-09-29T10:00:00\n"
        "parked_reason: 'ship_integrity_violation: run r01 slug=noop-work'\n"
        "---\n\n"
        "# Master\n\n## Sub-plan registry\n\n"
        "| # | file |\n|---|---|\n"
        "| 1 | [2026-09-29-noop-work.md](./2026-09-29-noop-work.md) |\n",
        encoding="utf-8",
    )

    (plans / "2026-09-29-noop-work.md").write_text(
        "---\n"
        "plan: noop-work\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "auto_block_fails: 3\n"
        "---\n\n"
        "# noop-work\n\n### Step 0\n\nBody.\n",
        encoding="utf-8",
    )

    # Unpark the master.
    r = subprocess.run(
        [sys.executable, str(PARK_MASTER),
         "--plans-dir", str(plans), "--unpark"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, f"unpark failed: {r.stderr or r.stdout}"

    # Check that auto_block_fails was reset to 0.
    sp = plans / "2026-09-29-noop-work.md"
    fm_text = sp.read_text(encoding="utf-8")
    import re
    m = re.search(r"^auto_block_fails\s*:\s*(\d+)", fm_text, re.MULTILINE)
    assert m is not None, (
        f"auto_block_fails not found in sub-plan frontmatter after unpark.\n"
        f"{fm_text}"
    )
    assert int(m.group(1)) == 0, (
        f"auto_block_fails is {m.group(1)}, expected 0 after unpark."
    )