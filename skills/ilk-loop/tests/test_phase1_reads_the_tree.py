"""Phase 1 reads the tree — AC-1 through AC-5.

Pins the contract that Phase 1 must pass `expected_tree_sha` to
`validate_record_detail` so that a record whose tree matches the current
tree is accepted, even when HEAD has moved (empty marker commits).

AC-1  A record whose ``head_sha`` differs from HEAD but whose ``tree_sha``
      equals the current tree is accepted by Phase 1 (``action: proceed``).
AC-2  That acceptance survives several empty commits after the record is
      written — the real shape, not a single marker.
AC-3  A record whose tree genuinely differs is still refused.
AC-4  A legacy record carrying no ``tree_sha`` still falls back to strict sha
      equality.
AC-5  No ``phase1-refusal.json`` is written on the AC-1 path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ── Path setup (follows test_phase1_refuses.py pattern) ─────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-ship" / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

sys.path.insert(0, str(SHIP_SCRIPTS))
sys.path.insert(0, str(LOOP_SCRIPTS))

from baseline_diff import (  # type: ignore[import-untyped]  # noqa: E402
    BaselineRef,
    BaselineReport,
    BaselineStatus,
    NodeIdDiff,
)
from phase1_verify import verify_phase1  # type: ignore[import-untyped]  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), *args],
        text=True, stderr=subprocess.DEVNULL,
    ).strip()


def _init_repo(tmp_path: Path) -> Path:
    """Create a throwaway git repo with one commit and return its root."""
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.local")
    _git(repo, "config", "user.name", "Test")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-m", "seed")
    return repo


def _head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _tree_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD^{tree}")


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create a throwaway project + runtime dir with pinned HOME.

    Pin both HOME and ILK_DATA_HOME (conftest.py requirement —
    ilk_data_root() checks ILK_DATA_HOME before Path.home(), so pinning
    one alone defeats the isolation).
    """
    repo = _init_repo(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(home / ".ilk-data"))
    return repo, runtime_dir


def _write_record(
    runtime_dir: Path,
    verdict: str = "pass",
    head_sha: str = "a" * 40,
    invocation: str = "echo ok",
    tree_sha: str | None = "b" * 40,
    timestamp: str = "2026-01-01T00:00:00+00:00",
) -> None:
    rec: dict = {
        "verdict": verdict,
        "head_sha": head_sha,
        "invocation": invocation,
        "timestamp": timestamp,
    }
    if tree_sha is not None:
        rec["tree_sha"] = tree_sha
    p = runtime_dir / "batch-gate.json"
    p.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")


def _refusal_path(runtime_dir: Path) -> Path:
    return runtime_dir / "phase1-refusal.json"


def _passing_baseline_report() -> BaselineReport:
    """Minimal baseline report that passes the baseline engine check."""
    ref = BaselineRef(tag="v0.0.0", resolved=True, status=BaselineStatus.FOUND)
    diff = NodeIdDiff(
        ref=ref,
        new_failures=frozenset(),
        inherited_failures=frozenset(),
        fixed=frozenset(),
        current_count=0,
        baseline_count=0,
        search_space=0,
        filtered=False,
    )
    return BaselineReport(
        diff=diff,
        stale_exclusions=(),
        denominator_statement="0 regressions across 0 collected tests vs v0.0.0",
    )


def _phase1(
    runtime_dir: Path,
    expected_head_sha: str,
    expected_invocation: str = "echo ok",
    *,
    baseline_report: BaselineReport | None = None,
):
    """Run Phase 1.  verify_phase1 does not receive expected_tree_sha
    today — that is the defect this test pins.  After the fix, the
    caller passes it through; until then AC-1/AC-2 are red."""
    return verify_phase1(
        runtime_dir,
        expected_head_sha,
        expected_invocation,
        baseline_report=baseline_report,
    )


# ── AC-1: tree matches, HEAD differs → proceed ──────────────────────────────

def test_ac1_fresh_by_tree_accepted(project):
    """AC-1: a record whose head_sha differs but tree_sha matches current
    tree must be accepted (action: proceed).

    RED until Phase 1 passes expected_tree_sha through.
    """
    repo, runtime_dir = project
    _write_record(
        runtime_dir,
        head_sha="0" * 40,          # differs from real HEAD
        tree_sha=_tree_sha(repo),   # matches current tree
    )
    result = _phase1(runtime_dir, _head_sha(repo))
    assert result.action == "proceed", (
        f"expected proceed, got {result.action}: {result.reason}"
    )


# ── AC-2: empty commits after record → tree still matches ───────────────────

def test_ac2_survives_empty_marker_commits(project):
    """AC-2: acceptance survives several empty commits after the record is
    written — the real batch-verification shape.

    RED until Phase 1 passes expected_tree_sha through.
    """
    repo, runtime_dir = project
    base_tree = _tree_sha(repo)
    _write_record(runtime_dir, head_sha="0" * 40, tree_sha=base_tree)
    # Three empty marker commits — HEAD moves, tree does not.
    for _ in range(3):
        _git(repo, "commit", "--allow-empty", "-m", "marker")
    result = _phase1(runtime_dir, _head_sha(repo))
    assert result.action == "proceed", (
        f"expected proceed after empty commits, got {result.action}: {result.reason}"
    )


# ── AC-3: tree genuinely differs → refuse ────────────────────────────────────

def test_ac3_real_change_refused(project):
    """AC-3: a record whose tree genuinely differs is refused.

    Proven by committing a real file change.
    """
    repo, runtime_dir = project
    # Write a real file so the tree changes.
    (repo / "new.txt").write_text("new content\n", encoding="utf-8")
    _git(repo, "add", "new.txt")
    _git(repo, "commit", "-m", "real change")
    _write_record(
        runtime_dir,
        head_sha=_head_sha(repo),
        tree_sha="0" * 40,          # stale tree
    )
    result = _phase1(runtime_dir, _head_sha(repo))
    assert result.action == "refuse", (
        f"expected refuse on stale tree, got {result.action}: {result.reason}"
    )


# ── AC-4: legacy record (no tree_sha) → strict equality fallback ────────────

def test_ac4_legacy_record_no_tree_sha_falls_back_to_head_equality(project):
    """AC-4: a legacy record carrying no tree_sha falls back to strict sha
    equality — the deliberate provenance rule in _head_is_current.

    A baseline_report is supplied so the baseline engine does not refuse
    before the batch-verdict path is exercised.
    """
    repo, runtime_dir = project
    head = _head_sha(repo)
    _write_record(runtime_dir, head_sha=head, tree_sha=None)
    result = _phase1(
        runtime_dir, head,
        baseline_report=_passing_baseline_report(),
    )
    assert result.action == "proceed", (
        f"expected proceed on legacy record, got {result.action}: {result.reason}"
    )


# ── AC-5: no refusal artifact on the AC-1 path ──────────────────────────────

def test_ac5_no_refusal_artifact_on_fresh_by_tree(project):
    """AC-5: when Phase 1 proceeds (AC-1 path), no phase1-refusal.json is
    written.

    RED until Phase 1 passes expected_tree_sha through (same fix as AC-1).
    A baseline_report is supplied so the baseline engine does not refuse
    before the batch-verdict path is exercised.
    """
    repo, runtime_dir = project
    _write_record(
        runtime_dir,
        head_sha="0" * 40,
        tree_sha=_tree_sha(repo),
    )
    _phase1(
        runtime_dir, _head_sha(repo),
        baseline_report=_passing_baseline_report(),
    )
    assert not _refusal_path(runtime_dir).exists(), (
        "phase1-refusal.json must not be written on the proceed path"
    )
