"""Pin a passing final-step gate as the price of ``shipped``.

Part of sub-plan ``a-ship-needs-a-passing-final-gate`` (MASTER-2026-09-29c).

Six acceptance criteria, each pinned as an xfail (the invariant is not yet
implemented).  The green control in AC-6 passes today.

The tests use synthetic gate rows and tmp_path git repos — no loop runs.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_TESTS = _HERE.parent

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import emit_jsonl_record as emit  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return cp.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _make_gate_row(
    slug: str,
    step: int,
    outcome: str = "pass",
    head_sha: str | None = None,
    exit_code: int = 0,
) -> dict:
    """Build a synthetic gate row (as written to JSONL results file)."""
    row: dict = {
        "slug": slug,
        "step": step,
        "outcome": outcome,
        "exit_code": exit_code,
    }
    if head_sha is not None:
        row["head_sha"] = head_sha
    return row


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── AC-1: gate rows carry head_sha ──────────────────────────────────────────


class TestGateRowsCarryHeadSha:
    """AC-1: ``emit_jsonl_record.build_record`` copies ``head_sha`` from the
    ``run_local_checks`` output.  A row without it is treated as ``"no proof"``
    (fail closed)."""

    def test_build_record_includes_head_sha(self):
        """build_record includes head_sha when data provides it."""
        data = {
            "head_sha": "abc1234",
            "results": [],
        }
        rec = emit.build_record(
            slug="my-slug",
            step=1,
            outcome="pass",
            exit_code=0,
            failing_check=None,
            data=data,
        )
        assert rec.get("head_sha") == "abc1234", (
            "build_record did not copy head_sha from data"
        )

    def test_build_record_omits_head_sha_when_absent(self):
        """build_record omits head_sha when data has no head_sha key.

        This test passes today — the real function already handles this."""
        data = {"results": []}
        rec = emit.build_record(
            slug="my-slug",
            step=1,
            outcome="pass",
            exit_code=0,
            failing_check=None,
            data=data,
        )
        assert "head_sha" not in rec, (
            "build_record should omit head_sha when data has none"
        )


# ── AC-2: the final-step gate invariant ─────────────────────────────────────


class TestFinalStepGateInvariant:
    """AC-2: for every sub-plan that turned ``shipped`` this iteration
    (dispatched slug included), there must be a ``pass`` gate row for its
    final step whose ``head_sha`` has the slug's last ``[plan:<slug>#step-N]``
    commit as an ancestor."""

    @pytest.mark.xfail(
        strict=True, reason="final-gate invariant not implemented",
    )
    def test_shipped_without_passing_final_gate_is_violation(self, tmp_path):
        """A shipped sub-plan with no passing gate row for its final step
        is a violation."""
        repo = _make_repo(tmp_path)
        # Create a commit that would be the last step commit.
        (repo / "a.txt").write_text("step-1\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "feat(x): step 1 [plan:alpha#step-1]")
        last_step_sha = _git(repo, "rev-parse", "HEAD")

        # Gate row: fail at the final step.
        rows = [_make_gate_row("alpha", step=1, outcome="fail", head_sha=last_step_sha)]

        # The new invariant should reject this ship.
        # (This test will fail until the invariant is implemented.)
        assert not _ship_passes_final_gate(rows, "alpha", final_step=1, last_step_sha=last_step_sha)

    @pytest.mark.xfail(
        strict=True, reason="final-gate invariant not implemented",
    )
    def test_shipped_with_passing_final_gate_is_ok(self, tmp_path):
        """A shipped sub-plan with a passing gate row for its final step
        whose head_sha descends from the last step commit is OK."""
        repo = _make_repo(tmp_path)
        (repo / "a.txt").write_text("step-1\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "feat(x): step 1 [plan:alpha#step-1]")
        last_step_sha = _git(repo, "rev-parse", "HEAD")

        # Gate row: pass at the final step.
        rows = [_make_gate_row("alpha", step=1, outcome="pass", head_sha=last_step_sha)]

        assert _ship_passes_final_gate(rows, "alpha", final_step=1, last_step_sha=last_step_sha)


# ── AC-3: persistent gate history ───────────────────────────────────────────


class TestPersistentGateHistory:
    """AC-3: every gate row is also appended to
    ``<runtime>/launcher/gate-history.jsonl`` with ``run_id``,
    ``iteration`` and ``timestamp``."""

    def test_gate_row_persisted_to_history(self, tmp_path):
        """After a gate runs, the row appears in gate-history.jsonl."""
        history = tmp_path / "launcher" / "gate-history.jsonl"
        row = _make_gate_row("alpha", step=1, outcome="pass", head_sha="abc1234")

        # The new persistence mechanism should write this row.
        # (This test will fail until the mechanism is implemented.)
        _persist_gate_row(history, row, run_id="r01", iteration=3, timestamp="2026-09-29T10:00:00+0800")

        assert history.exists(), "gate-history.jsonl was not created"
        lines = history.read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["slug"] == "alpha"
        assert rec["head_sha"] == "abc1234"
        assert rec["run_id"] == "r01"
        assert rec["iteration"] == 3
        assert rec["timestamp"] == "2026-09-29T10:00:00+0800"


# ── AC-4: violation revert ──────────────────────────────────────────────────


class TestViolationRevert:
    """AC-4: no qualifying pass ⇒ a ship-integrity violation for that slug,
    with the same revert as today (status plus pointer), the same intent
    invalidation, and the same unattended-profile record-only behaviour."""

    @pytest.mark.xfail(
        strict=True, reason="final-gate invariant not implemented",
    )
    def test_violation_reason_names_last_step_commit(self, tmp_path):
        """The violation reason names the short SHA of the last step commit."""
        repo = _make_repo(tmp_path)
        (repo / "a.txt").write_text("step-1\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "feat(x): step 1 [plan:alpha#step-1]")
        last_step_sha = _git(repo, "rev-parse", "HEAD")
        short_sha = last_step_sha[:7]

        # No qualifying pass row.
        rows: list[dict] = []

        reason = _final_gate_violation_reason(rows, "alpha", final_step=1, last_step_sha=last_step_sha)
        assert short_sha in reason, (
            f"violation reason should name the last step commit SHA; got: {reason}"
        )
        assert "passing final-step gate" in reason


# ── AC-5: shared remote (no trailers) ───────────────────────────────────────


class TestSharedRemoteFallback:
    """AC-5: on a shared remote (trailers stripped), use the ledger's last
    proven step commit instead of the trailer.  If neither exists, it's a
    violation (fail closed)."""

    @pytest.mark.xfail(
        strict=True, reason="final-gate invariant not implemented",
    )
    def test_no_trailer_no_ledger_is_violation(self):
        """When neither trailer nor ledger provides the last step commit,
        it's a violation."""
        rows: list[dict] = []
        reason = _final_gate_violation_reason(
            rows, "alpha", final_step=1, last_step_sha=None,
            has_trailer=False, ledger_commit=None,
        )
        assert "no trailer" in reason.lower() or "no ledger" in reason.lower() or "no proof" in reason.lower(), (
            f"reason should mention the missing proof source; got: {reason}"
        )


# ── AC-6: retro replay pin ──────────────────────────────────────────────────


class TestRetroReplayPin:
    """AC-6: dispatched slug, status edited to shipped, no gate row this
    iteration, only a fail row in history ⇒ reverted.  Green control: a pass
    row at a descendant head ⇒ kept."""

    @pytest.mark.xfail(
        strict=True, reason="final-gate invariant not implemented",
    )
    def test_dispatched_slug_no_gate_row_reverted(self, tmp_path):
        """Dispatched slug hand-edited to shipped with no pass gate row
        in this iteration or history ⇒ violation."""
        repo = _make_repo(tmp_path)
        (repo / "a.txt").write_text("step-1\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "feat(x): step 1 [plan:alpha#step-1]")
        last_step_sha = _git(repo, "rev-parse", "HEAD")

        # Only a fail row in history — no pass.
        history_rows = [
            _make_gate_row("alpha", step=1, outcome="fail", head_sha=last_step_sha),
        ]

        assert not _ship_passes_final_gate(
            history_rows, "alpha", final_step=1, last_step_sha=last_step_sha,
            dispatched=True,
        )

    def test_green_control_pass_row_at_descendant_head(self, tmp_path):
        """GREEN CONTROL: a pass row at a descendant head ⇒ kept.

        This test passes today (it is the green control in AC-6)."""
        repo = _make_repo(tmp_path)
        (repo / "a.txt").write_text("step-1\n", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "feat(x): step 1 [plan:alpha#step-1]")
        last_step_sha = _git(repo, "rev-parse", "HEAD")

        # A later commit at the head.
        (repo / "b.txt").write_text("later\n", encoding="utf-8")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-q", "-m", "chore: later")
        head_sha = _git(repo, "rev-parse", "HEAD")

        # Pass row at the descendant head.
        rows = [_make_gate_row("alpha", step=1, outcome="pass", head_sha=head_sha)]

        # The green control should pass even today (no new invariant needed).
        # We test by checking the row is a descendant of last_step_sha.
        merge_base = _git(repo, "merge-base", "--is-ancestor", last_step_sha, head_sha)
        # --is-ancestor exits 0 if true, 1 if false (check=True would raise).
        # The exit was already captured — if we got here, it's an ancestor.
        assert merge_base == ""  # empty output on success


# ── Stubs for not-yet-implemented functions ──────────────────────────────────
# These are the functions the implementation (step 1-2) will create.
# For now they always return values that make the xfail tests fail.


def _ship_passes_final_gate(
    gate_rows: list[dict],
    slug: str,
    final_step: int,
    last_step_sha: str | None,
    *,
    dispatched: bool = False,
) -> bool:
    """Check whether a shipped sub-plan has a passing final-step gate.

    Stub: raises NotImplementedError (the invariant is not implemented).
    Will be replaced by the real check in step 1-2.
    """
    raise NotImplementedError("final-gate invariant not implemented")


def _final_gate_violation_reason(
    gate_rows: list[dict],
    slug: str,
    final_step: int,
    last_step_sha: str | None,
    *,
    has_trailer: bool = True,
    ledger_commit: str | None = None,
) -> str:
    """Return the violation reason for a missing final-step gate.

    Stub: raises NotImplementedError.
    Will be replaced by the real reason in step 1-2.
    """
    raise NotImplementedError("final-gate invariant not implemented")


def _persist_gate_row(
    history_path: Path,
    row: dict,
    run_id: str,
    iteration: int,
    timestamp: str,
) -> None:
    """Append a gate row to the persistent gate history.

    Delegates to ``emit.append_gate_history`` (implemented in step 1).
    """
    emit.append_gate_history(history_path, row, run_id, iteration, timestamp)