"""Tests for the HEAD-dependency probe — sub-plan a-report-never-advises-discarding-work.

Pins the kira-cloudflare shape (run 20260915-112812): HEAD imports a symbol
defined only in the uncommitted diff, and the report today advises discarding
it. Three acceptance criteria, all expected to FAIL until the probe and the
three-way verdict exist.

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
Both HOME and ILK_DATA_HOME are pinned to tmp_path (§23 half-pinned trap).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "ilk-feedback" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import collect  # noqa: E400


# -- helpers ------------------------------------------------------------------


def _make_kira_repo(tmp_path: Path) -> Path:
    """Create a real git repo reproducing the kira-cloudflare shape.

    Commits both consumer.ts and producer.ts, then modifies producer.ts
    in the working tree so it becomes a tracked-but-uncommitted change
    visible to `git diff --numstat`. The original kira run had the export
    uncommitted; the `detect_uncommitted_changes` function uses
    `git diff --numstat` which only sees tracked modifications, so the
    fixture must leave producer.ts in a modified tracked state.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    consumer = repo / "consumer.ts"
    consumer.write_text(
        'import { TOOL_NAME } from "./producer";\n'
        'console.log(TOOL_NAME);\n'
    )
    producer = repo / "producer.ts"
    producer.write_text("// placeholder\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add consumer and producer stub"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    # Now modify producer.ts — tracked, so `git diff --numstat` sees it.
    # This is the actual export that HEAD's consumer already imports.
    producer.write_text(
        'export const TOOL_NAME = "kira_verify_patient";\n'
    )
    return repo


def _make_independent_repo(tmp_path: Path) -> Path:
    """Create a repo whose uncommitted diff defines nothing HEAD imports.

    Tracks util.ts with a placeholder, then modifies it in the working tree
    so `git diff HEAD` sees it (untracked files are invisible to git diff).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    main_file = repo / "main.ts"
    main_file.write_text('console.log("hello");\n')
    util = repo / "util.ts"
    util.write_text("// placeholder\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add main and util stub"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    # Modify util.ts — tracked, so `git diff HEAD` sees it.
    # Defines a symbol nothing imports.
    util.write_text('export function helper(): string { return "unused"; }\n')
    return repo


# -- AC-1: probe returns LOAD_BEARING for the kira shape ----------------------


class TestProbeLoadBearing:
    """Given HEAD importing a symbol defined only in the uncommitted diff,
    probe_head_dependency must return LOAD_BEARING and name the symbol."""

    def test_kira_shape_is_load_bearing(self, tmp_path):
        """The kira fixture: consumer imports TOOL_NAME from producer,
        producer.ts is uncommitted. Probe must return LOAD_BEARING."""
        repo = _make_kira_repo(tmp_path)

        # Pin both HOME and ILK_DATA_HOME (§23).
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        old_home = os.environ.get("HOME")
        old_data = os.environ.get("ILK_DATA_HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            os.environ["ILK_DATA_HOME"] = str(data_home)

            uncommitted = collect.detect_uncommitted_changes(repo)
            assert len(uncommitted) >= 1, "fixture has no uncommitted changes"

            result = collect.probe_head_dependency(repo, uncommitted)
            assert result.verdict == "LOAD_BEARING", (
                f"expected LOAD_BEARING, got {result.verdict}: {result.detail}"
            )
            assert "TOOL_NAME" in result.symbols, (
                f"expected TOOL_NAME in symbols, got {result.symbols}"
            )
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)
            if old_data is not None:
                os.environ["ILK_DATA_HOME"] = old_data
            else:
                os.environ.pop("ILK_DATA_HOME", None)


# -- AC-2: INDEPENDENT with files_scanned > 0 for a non-load-bearing diff ------


class TestProbeIndependent:
    """Given a repo whose uncommitted diff defines nothing HEAD imports,
    probe_head_dependency must return INDEPENDENT with files_scanned > 0."""

    def test_independent_shape_returns_independent(self, tmp_path):
        """No symbol in the diff is imported by HEAD → INDEPENDENT."""
        repo = _make_independent_repo(tmp_path)

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        old_home = os.environ.get("HOME")
        old_data = os.environ.get("ILK_DATA_HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            os.environ["ILK_DATA_HOME"] = str(data_home)

            uncommitted = collect.detect_uncommitted_changes(repo)
            result = collect.probe_head_dependency(repo, uncommitted)
            assert result.verdict == "INDEPENDENT", (
                f"expected INDEPENDENT, got {result.verdict}: {result.detail}"
            )
            assert result.files_scanned > 0, (
                "INDEPENDENT requires files_scanned > 0"
            )
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)
            if old_data is not None:
                os.environ["ILK_DATA_HOME"] = old_data
            else:
                os.environ.pop("ILK_DATA_HOME", None)


# -- AC-4: git failure yields UNDECIDABLE with stderr --------------------------


class TestProbeUndecidable:
    """When git is unavailable or returns non-zero, the probe must return
    UNDECIDABLE carrying the stderr — never an empty symbol list."""

    def test_no_git_repo_yields_undecidable(self, tmp_path):
        """A directory that is not a git repo → UNDECIDABLE."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        old_home = os.environ.get("HOME")
        old_data = os.environ.get("ILK_DATA_HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            os.environ["ILK_DATA_HOME"] = str(data_home)

            bare_dir = tmp_path / "not-a-repo"
            bare_dir.mkdir()
            result = collect.probe_head_dependency(bare_dir, [{"path": "x.ts", "line_count": 1}])
            assert result.verdict == "UNDECIDABLE", (
                f"expected UNDECIDABLE, got {result.verdict}: {result.detail}"
            )
            assert result.detail, "UNDECIDABLE must carry a detail string"
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)
            if old_data is not None:
                os.environ["ILK_DATA_HOME"] = old_data
            else:
                os.environ.pop("ILK_DATA_HOME", None)


# -- AC-3: INDEPENDENT with files_scanned == 0 raises -------------------------


class TestConstructibility:
    """Constructing an INDEPENDENT verdict with files_scanned == 0 must raise.
    A vacuous pass is the bug this probe exists to prevent."""

    def test_constructible_independent_zero_scan_raises(self, tmp_path):
        """INDEPENDENT with files_scanned == 0 is unconstructible."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        old_home = os.environ.get("HOME")
        old_data = os.environ.get("ILK_DATA_HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            os.environ["ILK_DATA_HOME"] = str(data_home)

            with pytest.raises(ValueError, match="files_scanned"):
                collect.HeadDependency(
                    verdict="INDEPENDENT",
                    symbols=[],
                    files_scanned=0,
                    symbols_examined=0,
                    detail="vacuous",
                )
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)
            if old_data is not None:
                os.environ["ILK_DATA_HOME"] = old_data
            else:
                os.environ.pop("ILK_DATA_HOME", None)


# -- AC-6: report builder does not emit "Do not commit these" for kira tree ----


class TestReportLoadBearingWording:
    """Replaying the kira fixture through render_report must produce
    the load-bearing wording, NOT the unconditional 'Do not commit these'."""

    def test_kira_report_says_load_bearing(self, tmp_path):
        """render_report with the kira-shaped repo must not advise
        discarding the uncommitted changes."""
        repo = _make_kira_repo(tmp_path)

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        data_home = tmp_path / "ilk-data"
        data_home.mkdir()
        old_home = os.environ.get("HOME")
        old_data = os.environ.get("ILK_DATA_HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            os.environ["ILK_DATA_HOME"] = str(data_home)

            iters = [
                {
                    "iteration": 1,
                    "duration_sec": 300,
                    "new_commits_total": 3,
                    "exit_code": 1,
                    "stop_reason": "local_checks_failed",
                    "timestamp": "2026-09-15T11:28:12+08:00",
                },
            ]
            facts = {
                "fail_iters_in_window": 1,
                "pass_iters_in_window": 0,
                "window_size": 1,
                "iter_at_stop": 1,
            }
            report = collect.render_report(
                project_path=repo,
                project_name="kira-cloudflare",
                run_id="20260915-112812",
                iters=iters,
                last_launch=None,
                label="local-checks-broken",
                facts=facts,
                rec_max=10,
                rec_to=30,
                rationale="gate failed",
                tail=[],
            )

            assert "Do not commit these" not in report, (
                "report must not unconditionally advise discarding work — "
                "the uncommitted diff may be load-bearing"
            )
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home
            else:
                os.environ.pop("HOME", None)
            if old_data is not None:
                os.environ["ILK_DATA_HOME"] = old_data
            else:
                os.environ.pop("ILK_DATA_HOME", None)