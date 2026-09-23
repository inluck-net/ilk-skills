"""Red-first: a ship-integrity violation parks the master that owns the violating slug.

rezmac run 20260923-150625: the driver called ``park_master.py`` with no
``--master`` and no ``--owner-of``, so it parked whichever master happened to
be the sole ``queued`` one — an unrelated ``MASTER-issue-6396`` — while the
violating ``MASTER-issue-6392`` was reconciled back to ``queued`` and
re-dispatched.

AC-1  Two masters — A ``shipped`` with sub-plan slug ``s``, B ``queued``
      unrelated.  ``--owner-of s`` parks A (even though it is ``shipped``);
      B is byte-identical.
AC-2  ``--owner-of nobody`` ⇒ exit 1, JSON has ``error``, ``slug``, and
      ``masters_searched``; no file changes.
AC-3  End-to-end: one runner iteration where the red gate belongs to A's
      sub-plan while B is the only ``queued`` master ⇒ A is ``blocked``
      naming A's slug, B unchanged.
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

_REPO = Path(__file__).resolve().parent.parent.parent.parent
RUNNER = _REPO / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
SCRIPT = _REPO / "skills" / "ilk-loop" / "scripts" / "park_master.py"

sys.path.insert(0, str(_REPO / "skills" / "ilk-loop" / "scripts"))
from plan_status import parse_frontmatter  # noqa: E402

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout (preflight:190)",
)


def _run(plans: Path, *extra: str, expect: int = 0) -> dict:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--plans-dir", str(plans), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == expect, (
        f"exit {r.returncode}: {r.stderr or r.stdout}"
    )
    return json.loads(r.stdout)


def _fm_master(plans: Path, name: str) -> dict:
    return parse_frontmatter(
        (plans / name).read_text(encoding="utf-8-sig"))


# ── fixtures ─────────────────────────────────────────────────────────────────

SLUG_A = "an-owned-subplan"
STEM_A = f"2026-09-23-{SLUG_A}"


@pytest.fixture
def two_masters(tmp_path: Path) -> Path:
    """Two masters: A ``shipped`` owning sub-plan slug ``s``, B ``queued``."""
    d = tmp_path / "plans"
    d.mkdir()
    # Master A — shipped, registers sub-plan whose slug is SLUG_A.
    (d / "MASTER-issue-6392.md").write_text(
        "---\ntitle: issue-6392\nstatus: shipped\nsupervised_only: false\n---\n\n"
        "# A\n\n## Sub-plan registry\n\n"
        f"| # | file |\n|---|---|\n| 1 | [{STEM_A}](./{STEM_A}.md) |\n",
        encoding="utf-8",
    )
    (d / f"{STEM_A}.md").write_text(
        f"---\nplan: {SLUG_A}\nstatus: shipped\ncurrent_step: 1\n"
        "estimated_steps: 1\n---\n\n# sub\n",
        encoding="utf-8",
    )
    # Master B — queued, unrelated.
    (d / "MASTER-issue-6396.md").write_text(
        "---\ntitle: issue-6396\nstatus: queued\nsupervised_only: false\n---\n\n"
        "# B\n\n## Sub-plan registry\n\n"
        "| # | file |\n|---|---|\n| 1 | [2026-09-23-unrelated.md](./2026-09-23-unrelated.md) |\n",
        encoding="utf-8",
    )
    (d / "2026-09-23-unrelated.md").write_text(
        "---\nplan: unrelated\nstatus: pending\ncurrent_step: 0\n"
        "estimated_steps: 1\n---\n\n# unrelated\n",
        encoding="utf-8",
    )
    return d


# ── AC-1: --owner-of parks the master owning the slug ────────────────────────


def test_owner_of_parks_the_master_registering_the_slug(
    two_masters: Path,
) -> None:
    """AC-1a: A is parked even though it is ``shipped`` (the common case —
    the violating master is usually ``shipped`` at park time because the
    reconcile runs after)."""
    b_before = (two_masters / "MASTER-issue-6396.md").read_bytes()
    out = _run(two_masters, "--owner-of", SLUG_A, "--reason", "test-park")
    assert out["master"] == "MASTER-issue-6392.md", (
        f"wrong master parked: {out}"
    )
    assert out["to"] == "blocked"
    fm = _fm_master(two_masters, "MASTER-issue-6392.md")
    assert fm["status"] == "blocked"
    assert fm["parked_reason"] == "test-park"
    # B is untouched.
    assert (two_masters / "MASTER-issue-6396.md").read_bytes() == b_before



def test_owner_of_includes_shipped_masters(
    two_masters: Path,
) -> None:
    """AC-1b: ``--owner-of`` considers ``shipped`` masters, not just
    ``PARKABLE`` ones.  Without this, the violating master — which is
    ``shipped`` at park time — would never be found."""
    out = _run(two_masters, "--owner-of", SLUG_A, "--reason", "r")
    assert out["from"] == "shipped", (
        f"expected to find a shipped master; got from={out['from']!r}"
    )


# ── AC-2: --owner-of nobody ⇒ informative failure ────────────────────────────


def test_owner_of_nobody_exits_1_with_slug_and_searched(
    two_masters: Path,
) -> None:
    """Exit 1, JSON names the slug and every master searched."""
    b_before = (two_masters / "MASTER-issue-6396.md").read_bytes()
    a_before = (two_masters / "MASTER-issue-6392.md").read_bytes()
    out = _run(two_masters, "--owner-of", "nobody-slug", expect=1)
    assert out["error"], f"no error field: {out}"
    assert out["slug"] == "nobody-slug", f"slug not echoed: {out}"
    assert len(out["masters_searched"]) == 2, (
        f"expected 2 masters searched, got {out['masters_searched']}"
    )
    # No file changes.
    assert (two_masters / "MASTER-issue-6396.md").read_bytes() == b_before
    assert (two_masters / "MASTER-issue-6392.md").read_bytes() == a_before


# ── AC-3: end-to-end driver parks the owning master ──────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _build_world_two_masters(root: Path) -> dict:
    """Project + isolated data home + stub ``claude`` + two masters.

    Master A owns the slug whose gate will be red.  Master B is ``queued``
    and unrelated — the trap that the old code fell into.
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

    # Master A — owns slug, gate will be red.
    (plans / "MASTER-issue-6392.md").write_text(
        "---\ntitle: issue-6392\nstatus: active\nsupervised_only: false\n---\n\n"
        "# A\n\n## Sub-plan registry\n\n"
        f"| # | file |\n|---|---|\n| 1 | [{STEM_A}](./{STEM_A}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{STEM_A}.md").write_text(
        "---\n"
        f"plan: {SLUG_A}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        "estimated_steps: 1\n"
        "---\n\n"
        f"# {SLUG_A}\n\n"
        "### Step 0 — do the thing\n\n"
        "```yaml\n"
        "local_checks:\n"
        "  - command: \"python3 -c 'raise SystemExit(1)'\"\n"
        "    timeout: 60\n"
        "```\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    # Master B — queued, unrelated.  The old code would park this one.
    (plans / "MASTER-issue-6396.md").write_text(
        "---\ntitle: issue-6396\nstatus: queued\nsupervised_only: false\n---\n\n"
        "# B\n\n## Sub-plan registry\n\n"
        "| # | file |\n|---|---|\n| 1 | [2026-09-23-unrelated.md](./2026-09-23-unrelated.md) |\n",
        encoding="utf-8",
    )
    (plans / "2026-09-23-unrelated.md").write_text(
        "---\nplan: unrelated\nstatus: pending\ncurrent_step: 0\n"
        "estimated_steps: 1\n---\n\n# unrelated\n",
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    # The stub agent lands a trailered commit and ships A's sub-plan.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"SP={str(plans / f'{STEM_A}.md')!r}\n"
        "python3 - \"$SP\" <<'EOP'\n"
        "import re, sys\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^status: in-progress', 'status: shipped', b, count=1, flags=re.M)\n"
        "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
        "p.write_text(b)\n"
        "EOP\n"
        "git -c user.email=t@example.com -c user.name=t commit -q "
        f"--allow-empty -m 'feat: the work [plan:{SLUG_A}#step-0]'\n"
        "echo 'stub agent done'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    return {"project": project, "plans": plans, "data_home": data_home,
            "key": key, "bin": bin_dir}


@_NEEDS_GTIMEOUT

def test_e2e_violation_parks_the_owning_master(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """AC-3: end-to-end driver run.  Red gate belongs to A's sub-plan, B is
    the only ``queued`` master.  After the run A is ``blocked`` naming A's
    slug, and B is unchanged.

    The harness is a simplified ``test_red_gate_stops_the_run._build_world``
    with two masters instead of one.
    """
    root = tmp_path_factory.mktemp("owner-of-e2e")
    world = _build_world_two_masters(root)
    b_before = (world["plans"] / "MASTER-issue-6396.md").read_bytes()

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
    proc = subprocess.run(
        ["bash", str(RUNNER),
         "--project-path", str(world["project"]),
         "--max-iterations", "1",
         "--iteration-timeout-min", "2",
         "--run-local-checks"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env=env, cwd=str(root),
    )
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])

    # A is parked and its reason names SLUG_A.
    fm_a = parse_frontmatter(
        (world["plans"] / "MASTER-issue-6392.md").read_text(encoding="utf-8-sig"))
    assert fm_a["status"] == "blocked", (
        f"A is not blocked after the run: {fm_a.get('status')!r}\n{tail}"
    )
    reason = fm_a.get("parked_reason", "")
    assert SLUG_A in reason, (
        f"parked_reason does not name the violating slug {SLUG_A!r}: {reason!r}\n{tail}"
    )

    # B is untouched.
    assert (world["plans"] / "MASTER-issue-6396.md").read_bytes() == b_before, (
        f"B was modified by the park — the old bug, replayed.\n{tail}"
    )
