"""Tests for release_train cut — tagging, pushing and baselining a proven HEAD.

Sub-plan: a-proven-release-is-tagged-and-pushed, step 0 (pins).

Each test builds a throwaway git repo under ``tmp_path`` with a tag
v0.0.1 and one commit after it.  A tmp bare repo serves as ``origin``.
Tests also build a fake data dir with a proof file.  They never push
to GitHub, never tag this repo, never touch the real ``~/.ilk-data``.

The five acceptance criteria:
  AC-1  proven proof + cut → v0.0.2 annotated tag, CHANGELOG row, bare repo has both.
  AC-2  proof file for a different head or verdict refused → exit 4, nothing changed.
  AC-3  bare repo rejects push (pre-receive hook exits 1) → exit 4, local tag kept, no force-push.
  AC-4  baseline file for v0.0.2 exists after cut, keyed on proof's invocation.
  AC-5  check right after cut reports not eligible (HEAD == tag after changelog commit).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))

from release_train import check  # noqa: E402


def _import_cut():
    """Import cut — deferred because it doesn't exist at step-0 commit time."""
    from release_train import cut
    return cut


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_fake_project(
    tmp_path: Path,
    release_train: bool = True,
    failing: bool = False,
) -> tuple[Path, Path]:
    """Create a minimal git repo with one commit, a tag, a bare origin, and a fake suite.

    Returns (project, bare_repo) — bare_repo is the ``origin`` remote.
    """
    project = tmp_path / "project"
    project.mkdir()

    bare = tmp_path / "bare.git"

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project, check=True, capture_output=True,
    )

    # .ilk-launch.json — includes suite config for prove
    (project / ".ilk-launch.json").write_text(json.dumps({
        "ship": {
            "release_train": release_train,
            "suite": {
                "command": "python3 -m pytest",
                "flags": [],
            },
        },
    }))

    # Initial commit + tag
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v0.0.1", "-m", "v0.0.1"],
        cwd=project, check=True, capture_output=True,
    )

    # Fake test suite (in HEAD, not in the tagged commit)
    assertion = "False" if failing else "True"
    (project / "test_trivial.py").write_text(textwrap.dedent(f"""\
        def test_one():
            assert True

        def test_two():
            assert {assertion}

        def test_three():
            assert True
    """))

    # Second commit (HEAD != tag)
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add tests"],
        cwd=project, check=True, capture_output=True,
    )

    # Create bare repo and add as origin
    subprocess.run(
        ["git", "init", "--bare", str(bare)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(bare)],
        cwd=project, check=True, capture_output=True,
    )
    # Push initial state so the bare repo has main
    subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=project, check=True, capture_output=True,
    )

    return project, bare


def _make_data_dir(tmp_path: Path) -> Path:
    """Create a minimal data directory structure."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    runtime = data_dir / "runtime" / "release"
    runtime.mkdir(parents=True)
    return data_dir


def _write_proof(
    data_dir: Path,
    head: str,
    verdict: str = "proven",
    invocation: str = "python3 -m pytest",
    last_tag: str = "v0.0.1",
    failing_nodes: list | None = None,
    baseline_ids: list | None = None,
) -> Path:
    """Write a proof file for the given head."""
    proof_dir = data_dir / "runtime" / "release"
    proof_dir.mkdir(parents=True, exist_ok=True)
    p = proof_dir / f"proof-{head[:12]}.json"
    payload = {
        "head": head,
        "last_tag": last_tag,
        "invocation": invocation,
        "verdict": verdict,
        "new_failing_ids": [],
        "failing_nodes": failing_nodes or [],
        "baseline_ids": baseline_ids or [],
    }
    p.write_text(json.dumps(payload, indent=2) + "\n")
    return p


def _write_baseline(data_dir: Path, tag: str, invocation: str, ids: list) -> None:
    """Write a baseline file in canonical .ilk-baselines/ format."""
    baselines_dir = data_dir / ".ilk-baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(invocation.encode()).hexdigest()[:12]
    key = f"{tag}__{h}"
    data = {
        "tag": tag,
        "suite_invocation": invocation,
        "node_ids": sorted(ids),
        "search_space": len(ids),
    }
    (baselines_dir / f"{key}.json").write_text(json.dumps(data, indent=2) + "\n")


def _get_head_sha(project: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project, capture_output=True, text=True,
    )
    return r.stdout.strip()


def _tag_exists(project: Path, tag: str) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", tag],
        cwd=project, capture_output=True, text=True,
    )
    return r.returncode == 0


def _tag_subject(project: Path, tag: str) -> str:
    """Return the subject line of an annotated tag."""
    r = subprocess.run(
        ["git", "tag", "-l", "--format=%(subject)", tag],
        cwd=project, capture_output=True, text=True,
    )
    return r.stdout.strip()


def _changelog_rows(project: Path) -> list[str]:
    """Return non-header, non-separator rows from the CHANGELOG table."""
    changelog = project / "CHANGELOG.md"
    if not changelog.exists():
        return []
    rows = []
    in_table = False
    for line in changelog.read_text().splitlines():
        if line.startswith("| Version"):
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            rows.append(line)
        elif in_table and not line.startswith("|"):
            break
    return rows


# ── AC-1: cut creates v0.0.2, CHANGELOG row, bare repo has both ────────────

class TestCutCreatesTagAndChangelog:
    """AC-1: proven proof → cut creates v0.0.2 (annotated), CHANGELOG row, bare repo has both."""

    def test_cut_creates_v002_and_changelog(self, tmp_path: Path) -> None:
        """Proven proof for HEAD: cut → v0.0.2 tag, CHANGELOG row, push succeeded."""
        project, bare = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        head = _get_head_sha(project)
        invocation = "python3 -m pytest"
        _write_proof(data_dir, head, verdict="proven", invocation=invocation)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        cut = _import_cut()
        result = cut(project, data_dir)

        assert result["tag"] == "v0.0.2"
        assert result["pushed"] is True
        assert result["commit"] is not None

        # Tag exists and is annotated
        assert _tag_exists(project, "v0.0.2")
        assert "v0.0.2" in _tag_subject(project, "v0.0.2")

        # CHANGELOG has a new row as first data row
        rows = _changelog_rows(project)
        assert len(rows) >= 1
        assert "v0.0.2" in rows[0]

        # Bare repo has both main and the tag
        r = subprocess.run(
            ["git", "-C", str(bare), "tag", "-l", "v0.0.2"],
            capture_output=True, text=True,
        )
        assert "v0.0.2" in r.stdout.strip()


# ── AC-2: refused proof → exit 4, nothing changed ──────────────────────────

class TestCutRefusedProof:
    """AC-2: proof for a different head or verdict refused → exit 4, no tag, no commit."""

    def test_refused_when_proof_for_different_head(self, tmp_path: Path) -> None:
        """Proof file for a different HEAD → cut refuses, nothing changes."""
        project, bare = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        # Write proof for a DIFFERENT head
        _write_proof(data_dir, "0" * 40, verdict="proven", invocation="python3 -m pytest")
        _write_baseline(data_dir, "v0.0.1", "python3 -m pytest", [])

        cut = _import_cut()
        with pytest.raises(SystemExit) as exc_info:
            cut(project, data_dir)
        assert exc_info.value.code == 4

        # No tag created
        assert not _tag_exists(project, "v0.0.2")

        # CHANGELOG unchanged
        rows = _changelog_rows(project)
        assert not any("v0.0.2" in r for r in rows)

    def test_refused_when_verdict_refused(self, tmp_path: Path) -> None:
        """Proof file with verdict=refused → cut refuses, nothing changes."""
        project, bare = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        head = _get_head_sha(project)
        _write_proof(data_dir, head, verdict="refused", invocation="python3 -m pytest")

        cut = _import_cut()
        with pytest.raises(SystemExit) as exc_info:
            cut(project, data_dir)
        assert exc_info.value.code == 4

        assert not _tag_exists(project, "v0.0.2")
        rows = _changelog_rows(project)
        assert not any("v0.0.2" in r for r in rows)


# ── AC-3: bare repo rejects push → exit 4, local tag kept, no force-push ────

class TestCutPushRejected:
    """AC-3: bare repo rejects push (pre-receive hook) → exit 4, local tag exists, no force-push."""

    def test_refused_when_push_rejected(self, tmp_path: Path) -> None:
        """Bare repo with a rejecting pre-receive hook → exit 4, local tag kept."""
        project, bare = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        # Install a pre-receive hook that always rejects
        hooks_dir = bare / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        pre_receive = hooks_dir / "pre-receive"
        pre_receive.write_text("#!/bin/sh\nexit 1\n")
        pre_receive.chmod(0o755)

        head = _get_head_sha(project)
        invocation = "python3 -m pytest"
        _write_proof(data_dir, head, verdict="proven", invocation=invocation)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        cut = _import_cut()
        with pytest.raises(SystemExit) as exc_info:
            cut(project, data_dir)
        assert exc_info.value.code == 4

        # Local tag exists (created before push attempt)
        assert _tag_exists(project, "v0.0.2")

        # But bare repo should NOT have the tag
        r = subprocess.run(
            ["git", "-C", str(bare), "tag", "-l", "v0.0.2"],
            capture_output=True, text=True,
        )
        assert r.stdout.strip() == ""


# ── AC-4: baseline file exists after cut ────────────────────────────────────

class TestCutStoresBaseline:
    """AC-4: after cut, the v0.0.2 baseline file exists, keyed on proof's invocation."""

    def test_baseline_stored_after_cut(self, tmp_path: Path) -> None:
        """After cut, baseline file exists for v0.0.2 under the proof's invocation."""
        project, bare = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        head = _get_head_sha(project)
        invocation = "python3 -m pytest"
        _write_proof(data_dir, head, verdict="proven", invocation=invocation, baseline_ids=[])
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        cut = _import_cut()
        result = cut(project, data_dir)

        # Baseline file for v0.0.2 should exist in canonical .ilk-baselines/
        baselines_dir = data_dir / ".ilk-baselines"
        h = hashlib.sha256(invocation.encode()).hexdigest()[:12]
        key = f"v0.0.2__{h}"
        baseline_path = baselines_dir / f"{key}.json"
        assert baseline_path.exists(), f"baseline file not found: {baseline_path}"

        baseline_data = json.loads(baseline_path.read_text())
        assert isinstance(baseline_data, dict)
        assert "node_ids" in baseline_data


# ── AC-5: check after cut reports not eligible ──────────────────────────────

class TestCheckAfterCut:
    """AC-5: check right after cut → not eligible (HEAD == tag after changelog commit is tagged)."""

    def test_check_not_eligible_after_cut(self, tmp_path: Path) -> None:
        """After cut tags HEAD, check should report not eligible."""
        project, bare = _make_fake_project(tmp_path)
        data_dir = _make_data_dir(tmp_path)

        head = _get_head_sha(project)
        invocation = "python3 -m pytest"
        _write_proof(data_dir, head, verdict="proven", invocation=invocation)
        _write_baseline(data_dir, "v0.0.1", invocation, [])

        cut = _import_cut()
        cut(project, data_dir)

        # Now check should say not eligible (HEAD == tag)
        result = check(project, data_dir)
        assert result["eligible"] is False
        assert "tag" in result["reason"].lower() or "head" in result["reason"].lower()