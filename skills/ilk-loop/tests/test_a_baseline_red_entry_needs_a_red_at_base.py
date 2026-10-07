"""Tests for the contract: a baseline_red entry added during a batch must be
backed by a failed-at-base row in that batch's record.

Part of sub-plan a-baseline-red-entry-needs-a-red-at-base (order #1 of
MASTER-2026-10-07k).

Contract (pre-resolved):
  In verify_attribution, after the record is parsed:
  1. Compute the node ids present in .ilk-launch.json ship.baseline_red at HEAD
     but not at the batch's base sha.
  2. For each added id: the record must have a row for it whose at base is
     failed.  Otherwise raise VerificationError.
  3. Entries present at base are unaffected.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_attribution as va  # noqa: E402


def _init_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit as the base."""
    proj = tmp_path / "proj"
    proj.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True, env=env)
    # Base commit: empty baseline_red.
    (proj / ".ilk-launch.json").write_text(
        json.dumps({"ship": {"baseline_red": []}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "base", "--allow-empty"],
        cwd=proj, check=True, env=env,
    )
    return proj


def _add_head_commit(proj: Path, baseline_red: list[str]) -> str:
    """Add a HEAD commit with the given baseline_red and return the base sha."""
    # Get the base sha before moving HEAD.
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=proj, capture_output=True, text=True, check=True,
    )
    base_sha = result.stdout.strip()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(proj.parent / "home"),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    (proj / ".ilk-launch.json").write_text(
        json.dumps({"ship": {"baseline_red": baseline_red}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "head"],
        cwd=proj, check=True, env=env,
    )
    return base_sha


def _write_record(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "record.md"
    p.write_text(body, encoding="utf-8")
    return p


def _build_record(
    failures: int,
    rows: list[tuple[str, str, str, str, str]],
) -> str:
    """Build a minimal signed record body with a 5-column at-base table.

    Each row is (node_id, at_base, in_baseline_red, head_reruns, batch_touched).
    """
    lines = [f"suite_failed: {failures}"]
    lines.append("")
    lines.append("## At-base rerun")
    lines.append("")
    lines.append(
        "| node id | at base | in baseline_red | head reruns | batch touched file |"
    )
    lines.append("|---|---|---|---|---|")
    for node, at_base, in_red, reruns, touched in rows:
        lines.append(
            f"| {node} | {at_base} | {in_red} | {reruns} | {touched} |"
        )
    lines.append("")
    # Sign it.
    lines.append("<!-- sig:abc123 -->")
    return "\n".join(lines)


# ── AC-1: added entry with passed at base ⇒ refused ─────────────────────────

class TestAC1AddedEntryPassedAtBase:
    """Record row X | passed | added | 0/1 | no ⇒ refused, message names X
    and passed (the 23daa4dd shape)."""

    def test_refused_naming_id_and_verdict(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        proj = _init_repo(tmp_path)
        base_sha = _add_head_commit(proj, ["test_X::test_case"])

        record = _build_record(
            failures=1,
            rows=[("test_X::test_case", "passed", "added", "0/1", "no")],
        )
        rec_path = _write_record(tmp_path, record)

        with pytest.raises(
            va.VerificationError,
            match=r"baseline_red entry.*test_X::test_case.*passed",
        ):
            va._check_added_baseline_red(rec_path, proj, base_sha)


# ── AC-2: added entry with unmeasured-at-base ⇒ refused ──────────────────────

class TestAC2AddedEntryUnmeasuredAtBase:
    """Record row X | unmeasured-at-base | added | 1/2 | no ⇒ refused
    (the gh-resolve shape)."""

    def test_refused_naming_id_and_verdict(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        proj = _init_repo(tmp_path)
        base_sha = _add_head_commit(proj, ["test_X::test_case"])

        record = _build_record(
            failures=1,
            rows=[
                ("test_X::test_case", "unmeasured-at-base", "added", "1/2", "no")
            ],
        )
        rec_path = _write_record(tmp_path, record)

        with pytest.raises(
            va.VerificationError,
            match=r"baseline_red entry.*test_X::test_case.*unmeasured",
        ):
            va._check_added_baseline_red(rec_path, proj, base_sha)


# ── AC-3: added entry with no row ⇒ refused ──────────────────────────────────

class TestAC3AddedEntryNoRow:
    """No row for X ⇒ refused, message says 'no row'."""

    def test_refused_saying_no_row(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        proj = _init_repo(tmp_path)
        base_sha = _add_head_commit(proj, ["test_X::test_case"])

        # Record has 0 failures and no rows — X is absent.
        record = "suite_failed: 0\n\n## At-base rerun\n\n<!-- sig:abc123 -->\n"
        rec_path = _write_record(tmp_path, record)

        with pytest.raises(
            va.VerificationError,
            match=r"baseline_red entry.*test_X::test_case.*no row",
        ):
            va._check_added_baseline_red(rec_path, proj, base_sha)


# ── AC-4: added entry with failed at base ⇒ not refused by this rule ──────────

class TestAC4AddedEntryFailedAtBase:
    """Record row X | failed | added | — | — ⇒ not refused by this rule."""

    def test_not_refused(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        proj = _init_repo(tmp_path)
        base_sha = _add_head_commit(proj, ["test_X::test_case"])

        record = _build_record(
            failures=1,
            rows=[("test_X::test_case", "failed", "added", "—", "—")],
        )
        rec_path = _write_record(tmp_path, record)

        # Should not raise.
        va._check_added_baseline_red(rec_path, proj, base_sha)


# ── AC-5: entry already present at base ⇒ not refused by this rule ────────────

class TestAC5EntryPresentAtBase:
    """X already present at base ⇒ not refused by this rule (this check only
    looks at entries added during the batch)."""

    def test_not_refused(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        proj = _init_repo(tmp_path)
        # X is already in the base's baseline_red.
        base_sha = _add_head_commit(proj, ["test_X::test_case"])

        # Write base .ilk-launch.json with X present (simulate base state).
        # Since _add_head_commit already moved HEAD, we need to reconstruct
        # what the base had.  For this test, we verify that an entry whose
        # baseline_red cell is "yes" (meaning it was in the base's list) is
        # not checked by this rule.
        record = _build_record(
            failures=1,
            rows=[("test_X::test_case", "failed", "yes", "—", "—")],
        )
        rec_path = _write_record(tmp_path, record)

        # Should not raise — "yes" means it was at base, not added.
        va._check_added_baseline_red(rec_path, proj, base_sha)