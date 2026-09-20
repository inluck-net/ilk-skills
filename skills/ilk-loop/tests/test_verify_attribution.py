"""Tests for verify_attribution.py — the batch-verification step-1 gate.

The template told step 1 to run `python3 <path>/verify_attribution.py` from the
day it was written, and the script never existed. A planner that resolved the
placeholder shipped a gate that exits 127 (kira-cloudflare pv-verify,
2026-09-15); a planner that inlined its own checker survived (gh-resolve). One
coin flip per batch. These pin the script now that it is real.

Two bugs are pinned specifically because both were made while writing checkers
of this kind on 2026-09-15:

* reading the whole row for "YES" also matches the `yes` in `in baseline_red`
  and fails a correctly-exonerated row;
* an unparseable record must refuse, never read as zero failures.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_attribution as va  # noqa: E402


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "record.md"
    p.write_text(body, encoding="utf-8")
    return p


TABLE_HEAD = (
    "| node id | at base | in baseline_red | attributed |\n"
    "|---|---|---|---|\n"
)


# ── the record must exist and say something ─────────────────────────────────

class TestRecordPresence:
    def test_missing_record_fails(self, tmp_path: Path) -> None:
        """Missing is a failure, never a skip."""
        with pytest.raises(va.VerificationError, match="not found"):
            va.verify(tmp_path / "absent.md")

    def test_empty_record_fails(self, tmp_path: Path) -> None:
        with pytest.raises(va.VerificationError, match="empty"):
            va.verify(_write(tmp_path, "   \n\n"))


# ── the failure count: both dialects, and refusal ───────────────────────────

class TestFailureCount:
    def test_suite_failed_form(self) -> None:
        assert va.parse_failure_count("suite_failed: 3\n") == 3

    def test_suite_line_form(self) -> None:
        """gh-resolve writes `Suite: N passed, F failed, S skipped`."""
        assert va.parse_failure_count("Suite: 5123 passed, 2 failed, 2 skipped\n") == 2

    def test_unparseable_refuses_rather_than_reading_zero(self) -> None:
        """The load-bearing negative: no count is not a clean batch.

        Defaulting to 0 would turn every unreadable record into a pass — the
        'empty answer nobody looked for' shape these gates exist to prevent.
        """
        with pytest.raises(va.VerificationError, match="no failure count"):
            va.parse_failure_count("## At-base rerun\n\n_(no failures)_\n")


# ── the at-base section and its rows ────────────────────────────────────────

class TestSectionAndRows:
    def test_missing_section_fails(self, tmp_path: Path) -> None:
        with pytest.raises(va.VerificationError, match="At-base rerun"):
            va.verify(_write(tmp_path, "suite_failed: 0\n\nnothing here\n"))

    def test_green_passes(self, tmp_path: Path) -> None:
        rec = _write(tmp_path, "suite_failed: 0\n\n## At-base rerun\n\n_(no failures)_\n")
        msg, excused = va.verify(rec)
        assert "none attributed" in msg
        assert excused == 0

    def test_rows_must_equal_failures(self, tmp_path: Path) -> None:
        """2 failures explained in prose, 0 rows ⇒ cannot pass.

        This is the batch-2026-09-07 defect: the record argued both failures
        were environmental and the gate read back its own rendered count.
        """
        rec = _write(tmp_path, "suite_failed: 2\n\nAttributed regressions: 0\n\n"
                               "## At-base rerun\n\nBoth were environmental.\n")
        with pytest.raises(va.VerificationError, match="row"):
            va.verify(rec)

    def test_zero_claimed_but_rows_present_fails(self, tmp_path: Path) -> None:
        rec = _write(tmp_path, "suite_failed: 0\n\n## At-base rerun\n\n" + TABLE_HEAD +
                               "| a::t1 | passed | no | YES |\n")
        with pytest.raises(va.VerificationError, match="disagree"):
            va.verify(rec)


# ── the verdict cell ────────────────────────────────────────────────────────

class TestAttributionCell:
    def test_attributed_row_fails(self, tmp_path: Path) -> None:
        rec = _write(tmp_path, "suite_failed: 2\n\n## At-base rerun\n\n" + TABLE_HEAD +
                               "| a::t1 | passed | no | YES |\n"
                               "| b::t2 | failed | no | no |\n")
        with pytest.raises(va.VerificationError, match="attributed regression"):
            va.verify(rec)

    def test_baseline_red_yes_is_not_an_attribution(self, tmp_path: Path) -> None:
        """The bug this pins: `yes` in the baseline_red column is NOT a verdict.

        A substring search for YES across the row fails this record, which is
        correctly exonerated — both failures also fail at base.
        """
        rec = _write(tmp_path, "suite_failed: 2\n\n## At-base rerun\n\n" + TABLE_HEAD +
                               "| a::t1 | failed | no | no |\n"
                               "| b::t2 | failed | yes | no |\n")
        msg, excused = va.verify(rec)
        assert "2 failure(s), none attributed" in msg
        assert excused == 2, "both rows were accounted for and exonerated"

    def test_reads_the_last_cell_not_the_row(self) -> None:
        rows = [["a::t1", "failed", "yes", "no"]]
        assert va.attributed_rows(rows) == []
        rows = [["a::t1", "passed", "no", "YES"]]
        assert len(va.attributed_rows(rows)) == 1


# ── unrecognised tokens: the tolerant-reader family ────────────────────────
#
# Measured on gh-resolve batch-2026-09-16. The suite reported 5337 passed,
# 11 failed, 2 skipped against a 0-failed baseline. The at-base table had
# 11 rows for 11 failures — count assertion passed — but 6 of those rows
# carried values the gate should refuse, and it saw neither.
#
# Two distinct shapes:
#   4 rows  `passed | no | no (fixed)`  — verdict cell prose, not a token
#   2 rows  `N/A    | no | no`          — at-base not a measurement
#
# The gate returned `attribution verified: 11 failure(s), none attributed`,
# exit 0.  These tests pin the correct behaviour: both shapes raise.

_MEASURED_TABLE = (
    "| node id | at base | in baseline_red | attributed |\n"
    "|---|---|---|---|\n"
    # 4 rows: verdict cell is `no (fixed)` — prose claiming a fix without
    # re-running the suite.  The template says "no third column makes it
    # not-attributed" when at_base is passed and not in baseline_red.
    "| test_alpha.py::t1 | passed | no | no (fixed) |\n"
    "| test_alpha.py::t2 | passed | no | no (fixed) |\n"
    "| test_alpha.py::t3 | passed | no | no (fixed) |\n"
    "| test_alpha.py::t4 | passed | no | no (fixed) |\n"
    # 2 rows: at-base is `N/A` — the test did not exist at base, so a
    # present failure is the batch's own damage, not an exoneration.
    "| test_beta.py::t5 | N/A | no | no |\n"
    "| test_beta.py::t6 | N/A | no | no |\n"
    # 5 rows: genuinely failed at base, correctly exonerated.
    "| test_gamma.py::t7 | failed | no | no |\n"
    "| test_gamma.py::t8 | failed | no | no |\n"
    "| test_gamma.py::t9 | failed | no | no |\n"
    "| test_gamma.py::t10 | failed | no | no |\n"
    "| test_gamma.py::t11 | failed | no | no |\n"
)

_MEASURED_SUITE = "Suite: 5337 passed, 11 failed, 2 skipped\n"


class TestUnrecognisedTokens:
    """The tolerant-reader family: unrecognised ≠ benign."""

    def test_no_fixed_verdict_cell_refuses(self, tmp_path: Path) -> None:
        """`no (fixed)` is prose overturning a row — the gate must refuse it.

        The row says passed-at-base and not in baseline_red.  The template's
        rule is explicit: there is no third column that makes it not-attributed.
        A parenthetical claim that the failure was fixed is a hypothesis about
        why, not a measurement.
        """
        rec = _write(tmp_path, _MEASURED_SUITE + "\n## At-base rerun\n\n" +
                     _MEASURED_TABLE)
        with pytest.raises(va.VerificationError):
            va.verify(rec)

    def test_na_at_base_refuses(self, tmp_path: Path) -> None:
        """`N/A` at base means the test did not exist — that is attributed.

        A test introduced by this batch and failing now is the batch's own
        damage.  Reading N/A as `failed` would silently exonerate it.
        """
        table = (
            "| node id | at base | in baseline_red | attributed |\n"
            "|---|---|---|---|\n"
            "| test_new.py::t1 | N/A | no | no |\n"
        )
        rec = _write(tmp_path, "Suite: 1 passed, 1 failed, 0 skipped\n\n"
                     "## At-base rerun\n\n" + table)
        with pytest.raises(va.VerificationError):
            va.verify(rec)

    def test_full_measured_table_refuses(self, tmp_path: Path) -> None:
        """The complete gh-resolve batch-2026-09-16 table: 6 of 11 rows bad.

        This is the exact record the gate returned exit 0 on.  After the fix,
        it must refuse and name the offending rows.
        """
        rec = _write(tmp_path, _MEASURED_SUITE + "\n## At-base rerun\n\n" +
                     _MEASURED_TABLE)
        with pytest.raises(va.VerificationError) as exc:
            va.verify(rec)
        msg = str(exc.value)
        # The refusal must name the unrecognised values so the reader knows
        # what to fix.
        assert "no (fixed)" in msg or "N/A" in msg

    def test_no_fixed_only_verdict_section_refuses(self, tmp_path: Path) -> None:
        """Isolated: only `no (fixed)` rows, no N/A distraction."""
        table = (
            "| node id | at base | in baseline_red | attributed |\n"
            "|---|---|---|---|\n"
            "| test_alpha.py::t1 | passed | no | no (fixed) |\n"
            "| test_alpha.py::t2 | passed | no | no (fixed) |\n"
        )
        rec = _write(tmp_path, "Suite: 10 passed, 2 failed, 0 skipped\n\n"
                     "## At-base rerun\n\n" + table)
        with pytest.raises(va.VerificationError):
            va.verify(rec)

    def test_na_only_at_base_section_refuses(self, tmp_path: Path) -> None:
        """Isolated: only N/A at-base rows, no `no (fixed)` distraction."""
        table = (
            "| node id | at base | in baseline_red | attributed |\n"
            "|---|---|---|---|\n"
            "| test_beta.py::t1 | N/A | no | no |\n"
            "| test_beta.py::t2 | N/A | no | no |\n"
        )
        rec = _write(tmp_path, "Suite: 10 passed, 2 failed, 0 skipped\n\n"
                     "## At-base rerun\n\n" + table)
        with pytest.raises(va.VerificationError):
            va.verify(rec)

    def test_whitespace_and_case_tolerant(self, tmp_path: Path) -> None:
        """` YES `, `yes`, `Yes` are formatting, not meaning.

        AC-5: case and whitespace tolerance is preserved.  The refusal is
        about unrecognised *tokens*, not about punctuation.
        """
        table = (
            "| node id | at base | in baseline_red | attributed |\n"
            "|---|---|---|---|\n"
            "| a::t1 | passed | no | YES |\n"
            "| a::t2 | passed | no | yes |\n"
            "| a::t3 | passed | no |  Yes  |\n"
        )
        rec = _write(tmp_path, "Suite: 10 passed, 3 failed, 0 skipped\n\n"
                     "## At-base rerun\n\n" + table)
        with pytest.raises(va.VerificationError, match="attributed regression"):
            va.verify(rec)


# ── the CLI contract the gate depends on ────────────────────────────────────

class TestCli:
    def test_exit_codes(self, tmp_path: Path, capsys) -> None:
        good = _write(tmp_path, "suite_failed: 0\n\n## At-base rerun\n\n_(no failures)_\n")
        assert va.main([str(good)]) == 0
        bad = tmp_path / "nope.md"
        assert va.main([str(bad)]) == 1


# ── the bridge: a verified batch becomes a PROVEN one ───────────────────────
#
# Verification and proof were different files: this script validates
# logs/verification/<batch>-batch.md, while loop_status/ship_audit read
# runtime/batch-gate.json. Nothing bridged them, so both batches verified green
# on 2026-09-15 and both still read SHIP PROOF MISSING.

class TestGateRecordBridge:
    def _project(self, tmp_path: Path, monkeypatch, suite_cmd="echo hi"):
        import json as _json
        import subprocess
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        proj = tmp_path / "proj"
        proj.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "i"],
                       cwd=proj, check=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"),
                            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        if suite_cmd is not None:
            (proj / ".ilk-launch.json").write_text(
                _json.dumps({"ship": {"suite": {"command": suite_cmd, "flags": []}}}),
                encoding="utf-8")
        return proj

    def test_pass_writes_a_fresh_record(self, tmp_path: Path, monkeypatch) -> None:
        """The whole point: after a clean verify, the proof check sees it."""
        proj = self._project(tmp_path, monkeypatch)
        ok, detail = va.write_gate_record(proj, excused=0)
        assert ok, detail

        sys.path.insert(0, str(SCRIPTS_DIR))
        import batch_gate  # noqa: E402
        from ship_audit import _resolve_expected_invocation  # noqa: E402
        rd = batch_gate.resolve_runtime_dir(proj)
        assert batch_gate.validate_record(
            batch_gate.record_path(rd),
            batch_gate._git_head_sha(proj),
            _resolve_expected_invocation(proj),
            batch_gate._git_head_tree(proj),
        ) == "fresh"

    def test_records_the_expected_invocation_verbatim(self, tmp_path: Path, monkeypatch) -> None:
        """A different string is rejected as stale_invocation, so it must match."""
        proj = self._project(tmp_path, monkeypatch, suite_cmd="pytest -q")
        va.write_gate_record(proj, excused=0)
        sys.path.insert(0, str(SCRIPTS_DIR))
        import batch_gate  # noqa: E402
        from ship_audit import _resolve_expected_invocation  # noqa: E402
        rec = batch_gate.read_record(batch_gate.resolve_runtime_dir(proj))
        assert rec.invocation == _resolve_expected_invocation(proj)
        assert rec.writer == "verify_attribution"
        assert rec.undeclared == []          # computed-empty, not "not recorded"

    def test_refuses_when_suite_unconfigured(self, tmp_path: Path, monkeypatch) -> None:
        """A pass naming no invocation is rejected as 'unenforced' downstream.

        So the bridge must refuse to write rather than emit a vague one.
        """
        proj = self._project(tmp_path, monkeypatch, suite_cmd=None)
        ok, detail = va.write_gate_record(proj, excused=0)
        assert not ok
        assert "ship.suite" in detail

    def test_writes_tree_sha_so_an_empty_marker_does_not_invalidate_it(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """This step makes an empty marker commit; head moves, tree does not."""
        proj = self._project(tmp_path, monkeypatch)
        va.write_gate_record(proj, excused=0)
        sys.path.insert(0, str(SCRIPTS_DIR))
        import batch_gate  # noqa: E402
        rec = batch_gate.read_record(batch_gate.resolve_runtime_dir(proj))
        assert rec.tree_sha, "without tree_sha the marker commit invalidates the proof"


# ── the unsubstituted placeholder ───────────────────────────────────────────
#
# The template shipped `verify_attribution.py <record path>` and asked the
# planner to resolve it. On 2026-09-15 the planner resolved `<skill-root>` and
# `<batch-slug>` in the same file and left this one literal, on BOTH gh-resolve
# batches (2026-09-15c and 2026-09-15d). The gate then failed as "record not
# found" — which reads as "step 0 never wrote its record", so ship-integrity
# reverted each sub-plan from shipped to in-progress after a green suite.

class TestPlaceholderRefusal:
    def test_literal_placeholder_is_named_as_such(self) -> None:
        with pytest.raises(va.VerificationError, match="unsubstituted template placeholder"):
            va.reject_placeholder("<record path>")

    def test_placeholder_anywhere_in_the_argument_is_caught(self) -> None:
        """A half-resolved path is still unrunnable."""
        with pytest.raises(va.VerificationError, match="unsubstituted"):
            va.reject_placeholder("/Users/x/logs/verification/<batch-slug>-batch.md")

    def test_a_real_path_passes_through(self) -> None:
        va.reject_placeholder("/Users/x/logs/verification/batch-2026-09-15c-batch.md")

    def test_cli_refuses_the_placeholder_without_blaming_step_0(self, capsys) -> None:
        """The message must point at the plan file, not at a missing record."""
        assert va.main(["<record path>", "--no-write-gate-record"]) == 1
        err = capsys.readouterr().err
        assert "unsubstituted template placeholder" in err
        assert "do not re-run the suite" in err


# ── --batch: the record path is resolved, never hardcoded ───────────────────

class TestBatchResolution:
    def _project_with_record(self, tmp_path: Path, monkeypatch, body: str,
                             slug: str = "batch-2026-09-15c"):
        import subprocess
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        proj = tmp_path / "proj"
        proj.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)

        sys.path.insert(0, str(SCRIPTS_DIR))
        from ilk_paths import external_logs_dir, resolve_project_key  # noqa: E402
        key = resolve_project_key(proj)
        vdir = external_logs_dir(key) / "verification"
        vdir.mkdir(parents=True, exist_ok=True)
        if body is not None:
            (vdir / f"{slug}-batch.md").write_text(body, encoding="utf-8")
        return proj, vdir

    def test_resolves_the_record_from_the_slug(self, tmp_path: Path, monkeypatch) -> None:
        proj, vdir = self._project_with_record(
            tmp_path, monkeypatch, "suite_failed: 0\n\n## At-base rerun\n\n_(no failures)_\n")
        found = va.resolve_batch_record(proj, "batch-2026-09-15c")
        assert found == vdir / "batch-2026-09-15c-batch.md"

    def test_a_miss_lists_what_is_actually_there(self, tmp_path: Path, monkeypatch) -> None:
        """An unconstructible empty answer: name the siblings, not just the miss.

        "not found: <path>" and "you looked in the wrong directory" are the same
        string, and the second one is the expensive mistake.
        """
        proj, _ = self._project_with_record(
            tmp_path, monkeypatch, "suite_failed: 0\n", slug="batch-2026-09-15d")
        with pytest.raises(va.VerificationError) as exc:
            va.resolve_batch_record(proj, "batch-2026-09-15c")
        assert "batch-2026-09-15d-batch.md" in str(exc.value)

    def test_end_to_end_via_cli(self, tmp_path: Path, monkeypatch, capsys) -> None:
        proj, _ = self._project_with_record(
            tmp_path, monkeypatch, "suite_failed: 0\n\n## At-base rerun\n\n_(no failures)_\n")
        rc = va.main(["--batch", "batch-2026-09-15c", "--project", str(proj),
                      "--no-write-gate-record"])
        assert rc == 0
        assert "none attributed" in capsys.readouterr().out

    def test_both_forms_at_once_is_refused(self, tmp_path: Path, capsys) -> None:
        """A stale path beside a correct slug must not silently pick one."""
        rc = va.main([str(tmp_path / "r.md"), "--batch", "batch-x",
                      "--no-write-gate-record"])
        assert rc == 2
        assert "exactly one" in capsys.readouterr().err

    def test_neither_form_is_refused(self, capsys) -> None:
        assert va.main(["--no-write-gate-record"]) == 2
        assert "exactly one" in capsys.readouterr().err


# ── the proof must cover the tree the suite ran on ──────────────────────────
#
# write_gate_record stamps the proof with the tree at WRITE time. Downstream
# catches a proof that AGES (validate_record -> stale_head). What nothing
# catches is a proof born stale: a gate re-run on a tree the suite never saw.
# Measured on gh-resolve 2026-09-16 - re-running c's step-1 gate after a
# placeholder fix would have stamped pass over a tree 26 files / +2900 lines
# past the one its suite ran on.

class TestVerifiedTreeGuard:
    def _repo(self, tmp_path: Path, monkeypatch):
        import subprocess
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        proj = tmp_path / "proj"
        proj.mkdir()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"),
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        (proj / "code.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=proj, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=proj, check=True, env=env)
        return proj, env

    def _head(self, proj) -> str:
        import subprocess
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=proj,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", ).stdout.strip()

    def _record(self, tmp_path: Path, head: str | None) -> Path:
        body = "suite_failed: 0\n\n"
        if head:
            body += f"**Head:** {head}\n\n"
        body += "## At-base rerun\n\n_(no failures)_\n"
        p = tmp_path / "rec.md"
        p.write_text(body, encoding="utf-8")
        return p

    def test_empty_marker_commit_does_not_invalidate_the_proof(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Head moves, tree does not — this step makes such a commit by design.

        A head comparison would refuse every correct run.
        """
        import subprocess
        proj, env = self._repo(tmp_path, monkeypatch)
        rec = self._record(tmp_path, self._head(proj))
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "marker"],
                       cwd=proj, check=True, env=env)
        ok, why = va.check_verified_tree(proj, rec)
        assert ok, why

    def test_code_landing_after_the_suite_refuses_the_proof(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The gh-resolve 2026-09-16 case: a gate re-run after code landed."""
        import subprocess
        proj, env = self._repo(tmp_path, monkeypatch)
        rec = self._record(tmp_path, self._head(proj))
        (proj / "code.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", "later fix"], cwd=proj, check=True, env=env)
        ok, why = va.check_verified_tree(proj, rec)
        assert not ok
        assert "tree moved after verification" in why
        assert "re-run step 0" in why

    def test_a_record_naming_no_commit_refuses(self, tmp_path: Path, monkeypatch) -> None:
        """Unknown is not the same as unchanged; it must not read as a match."""
        proj, _ = self._repo(tmp_path, monkeypatch)
        ok, why = va.check_verified_tree(proj, self._record(tmp_path, None))
        assert not ok
        assert "declares no verified commit" in why

    def test_prose_where_a_sha_belongs_is_not_a_commit(self, tmp_path: Path, monkeypatch) -> None:
        """Two real records said `current main` and `asserted and confirmed`."""
        proj, _ = self._repo(tmp_path, monkeypatch)
        p = tmp_path / "rec.md"
        p.write_text("suite_failed: 0\n\n**HEAD:** current main\n\n"
                     "## At-base rerun\n\n_(no failures)_\n", encoding="utf-8")
        ok, why = va.check_verified_tree(proj, p)
        assert not ok
        assert "declares no verified commit" in why

    def test_a_base_line_is_not_read_as_the_head(self, tmp_path: Path, monkeypatch) -> None:
        """`**Base ≠ HEAD:** asserted` and `**Base:** <sha>` are not the head."""
        proj, _ = self._repo(tmp_path, monkeypatch)
        p = tmp_path / "rec.md"
        p.write_text(f"suite_failed: 0\n\n**Base:** {self._head(proj)}\n\n"
                     "## At-base rerun\n\n_(no failures)_\n", encoding="utf-8")
        ok, why = va.check_verified_tree(proj, p)
        assert not ok, "a Base line must not be mistaken for the verified head"

    def test_unresolvable_commit_refuses(self, tmp_path: Path, monkeypatch) -> None:
        proj, _ = self._repo(tmp_path, monkeypatch)
        ok, why = va.check_verified_tree(proj, self._record(tmp_path, "deadbeefdeadbeef"))
        assert not ok
        assert "cannot resolve" in why

    def test_machine_form_verified_tree_is_compared_directly(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        proj, _ = self._repo(tmp_path, monkeypatch)
        tree = va._git(proj, "rev-parse", "HEAD^{tree}")
        p = tmp_path / "rec.md"
        p.write_text(f"suite_failed: 0\n\nverified_tree: {tree}\n\n"
                     "## At-base rerun\n\n_(no failures)_\n", encoding="utf-8")
        ok, why = va.check_verified_tree(proj, p)
        assert ok, why

    def test_cli_still_exits_0_when_the_proof_is_refused(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Loud, not fatal. Attribution passed; a red gate would revert the sub-plan.

        That is the exact failure this batch spent the morning undoing.
        """
        import json as _json, subprocess
        proj, env = self._repo(tmp_path, monkeypatch)
        (proj / ".ilk-launch.json").write_text(
            _json.dumps({"ship": {"suite": {"command": "echo hi", "flags": []}}}),
            encoding="utf-8")
        rec = self._record(tmp_path, self._head(proj))
        (proj / "code.py").write_text("x = 3\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", "later"], cwd=proj, check=True, env=env)

        rc = va.main([str(rec), "--project", str(proj)])
        assert rc == 0
        out = capsys.readouterr()
        assert "PROOF NOT RECORDED" in out.out
        sys.path.insert(0, str(SCRIPTS_DIR))
        import batch_gate  # noqa: E402
        rd = batch_gate.resolve_runtime_dir(proj)
        assert not batch_gate.record_path(rd).is_file(), "a refused proof must write nothing"
