"""A test cannot rmtree a live data dir.

Part of `a-test-cannot-rmtree-a-live-data-dir`.

Drives ``rmtree_guard.py`` and the conftest autouse fixture that wraps
``shutil.rmtree``. HOME and ILK_DATA_HOME are pinned together so nothing
reads the real ``~/.ilk-data``.

AC-1: ``refuses`` is true for ``<fake_live>/projects/k`` and for
      ``<fake_live>`` itself; false for ``<tmp>/x``; false for
      ``<fake_live>/projects/k/runtime/launcher/worktrees/selfmod-batch/scratch/x``
      when that worktree is in ``allowed_roots``. Red-first.
AC-2: ``scan_source`` on a snippet shaped like ``_cleanup_project_key``
      returns one finding; on ``shutil.rmtree(tmp_path / "x")``, on
      ``SCRATCH = REPO_ROOT / "scratch"`` followed by
      ``shutil.rmtree(SCRATCH)``, and on a waived line it returns none;
      on a waiver with no reason it returns one. Red-first.
AC-3: the repo scan (every ``test_*.py`` under ``skills/*/tests`` and
      ``tests``, plus ``conftest.py``) returns 0 findings. Red-first.
AC-4 (runtime): inside a test, with ``rmtree_guard.live_roots``
      monkeypatched to ``[tmp_path / "live"]`` and
      ``rmtree_guard.allowed_roots`` to return ``[]`` (since ``tmp_path``
      is under the temp dir, which is otherwise allowed), a call to
      ``shutil.rmtree(tmp_path / "live" / "projects" / "k")`` raises
      ``LiveDataRmtreeRefused``, and the directory still exists. Red-first.
AC-5 (control): ``shutil.rmtree(tmp_path / "x")`` still works under the
      fixture.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ── AC-1: refuses predicate ──────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_refuses_live_root(tmp_path: Path) -> None:
    """refuses is true for <fake_live>/projects/k and for <fake_live> itself."""
    import rmtree_guard

    fake_live = tmp_path / "live"
    fake_live.mkdir()
    allowed = rmtree_guard.allowed_roots(tmp_path)

    assert rmtree_guard.refuses(
        fake_live / "projects" / "k",
        live_roots=[fake_live],
        allowed_roots=allowed,
    )
    assert rmtree_guard.refuses(
        fake_live,
        live_roots=[fake_live],
        allowed_roots=allowed,
    )


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_refuses_false_for_tmp(tmp_path: Path) -> None:
    """refuses is false for <tmp>/x."""
    import rmtree_guard

    fake_live = tmp_path / "live"
    fake_live.mkdir()
    allowed = rmtree_guard.allowed_roots(tmp_path)

    assert not rmtree_guard.refuses(
        tmp_path / "x",
        live_roots=[fake_live],
        allowed_roots=allowed,
    )


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_refuses_false_for_worktree_scratch(tmp_path: Path) -> None:
    """refuses is false for <fake_live>/projects/k/runtime/launcher/worktrees/selfmod-batch/scratch/x
    when that worktree is in allowed_roots."""
    import rmtree_guard

    fake_live = tmp_path / "live"
    fake_live.mkdir()
    worktree_scratch = (
        fake_live
        / "projects"
        / "k"
        / "runtime"
        / "launcher"
        / "worktrees"
        / "selfmod-batch"
        / "scratch"
    )
    worktree_scratch.mkdir(parents=True)

    allowed = [worktree_scratch]

    assert not rmtree_guard.refuses(
        worktree_scratch / "x",
        live_roots=[fake_live],
        allowed_roots=allowed,
    )


# ── AC-2: scan_source predicate ──────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_scan_source_finds_cleanup_project_key() -> None:
    """scan_source on a snippet shaped like _cleanup_project_key returns one finding."""
    import rmtree_guard

    snippet = textwrap.dedent("""\
        import os
        import shutil
        from pathlib import Path

        def _cleanup_project_key(key: str) -> None:
            pw_dir = Path(os.getpwuid(os.getuid()).pw_dir)
            target = pw_dir / ".ilk-data" / "projects" / key
            shutil.rmtree(target)
    """)
    findings = rmtree_guard.scan_source(snippet, "test_file.py")
    assert len(findings) == 1, f"expected 1 finding, got {len(findings)}: {findings}"


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_scan_source_no_finding_for_tmp_path() -> None:
    """scan_source on shutil.rmtree(tmp_path / "x") returns none."""
    import rmtree_guard

    snippet = textwrap.dedent("""\
        import shutil

        def test_something(tmp_path):
            shutil.rmtree(tmp_path / "x")
    """)
    findings = rmtree_guard.scan_source(snippet, "test_file.py")
    assert len(findings) == 0, f"expected 0 findings, got {len(findings)}: {findings}"


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_scan_source_no_finding_for_scratch() -> None:
    """scan_source on SCRATCH = REPO_ROOT / "scratch" followed by shutil.rmtree(SCRATCH) returns none."""
    import rmtree_guard

    snippet = textwrap.dedent("""\
        import shutil
        from pathlib import Path

        REPO_ROOT = Path(__file__).resolve().parent.parent
        SCRATCH = REPO_ROOT / "scratch"

        def test_something():
            shutil.rmtree(SCRATCH)
    """)
    findings = rmtree_guard.scan_source(snippet, "test_file.py")
    assert len(findings) == 0, f"expected 0 findings, got {len(findings)}: {findings}"


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_scan_source_no_finding_for_waived_line() -> None:
    """scan_source on a waived line returns none."""
    import rmtree_guard

    snippet = textwrap.dedent("""\
        import os
        import shutil
        from pathlib import Path

        def _cleanup_project_key(key: str) -> None:
            pw_dir = Path(os.getpwuid(os.getuid()).pw_dir)
            target = pw_dir / ".ilk-data" / "projects" / key
            shutil.rmtree(target)  # rmtree-guard: ok test fixture
    """)
    findings = rmtree_guard.scan_source(snippet, "test_file.py")
    assert len(findings) == 0, f"expected 0 findings, got {len(findings)}: {findings}"


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_scan_source_finding_for_waiver_no_reason() -> None:
    """scan_source on a waiver with no reason returns one finding."""
    import rmtree_guard

    snippet = textwrap.dedent("""\
        import os
        import shutil
        from pathlib import Path

        def _cleanup_project_key(key: str) -> None:
            pw_dir = Path(os.getpwuid(os.getuid()).pw_dir)
            target = pw_dir / ".ilk-data" / "projects" / key
            shutil.rmtree(target)  # rmtree-guard: ok
    """)
    findings = rmtree_guard.scan_source(snippet, "test_file.py")
    assert len(findings) == 1, f"expected 1 finding, got {len(findings)}: {findings}"


# ── AC-3: repo scan ─────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_repo_scan_returns_zero_findings() -> None:
    """The repo scan (every test_*.py under skills/*/tests and tests, plus conftest.py)
    returns 0 findings.

    Red-first: at HEAD the module is missing, and once it exists,
    ``_cleanup_project_key`` is one finding until step 1 deletes it.
    """
    import rmtree_guard

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    test_files = list(repo_root.glob("skills/*/tests/test_*.py"))
    test_files.extend(repo_root.glob("tests/test_*.py"))
    test_files.append(repo_root / "conftest.py")

    findings = []
    for test_file in test_files:
        if test_file.exists():
            text = test_file.read_text(encoding="utf-8")
            findings.extend(rmtree_guard.scan_source(text, str(test_file)))

    assert len(findings) == 0, f"expected 0 findings, got {len(findings)}: {findings}"


# ── AC-4 (runtime): fixture refuses rmtree of live data dir ──────────────────


@pytest.mark.xfail(strict=True, reason="rmtree_guard module does not yet exist")
def test_fixture_refuses_rmtree_of_live_data_dir(tmp_path: Path, monkeypatch) -> None:
    """Inside a test, with rmtree_guard.live_roots monkeypatched to [tmp_path / "live"]
    and rmtree_guard.allowed_roots to return [], a call to
    shutil.rmtree(tmp_path / "live" / "projects" / "k") raises
    LiveDataRmtreeRefused, and the directory still exists.
    """
    import rmtree_guard

    fake_live = tmp_path / "live"
    fake_live.mkdir()
    target = fake_live / "projects" / "k"
    target.mkdir(parents=True)

    monkeypatch.setattr(rmtree_guard, "live_roots", lambda: [fake_live])
    monkeypatch.setattr(rmtree_guard, "allowed_roots", lambda _: [])

    with pytest.raises(rmtree_guard.LiveDataRmtreeRefused):
        shutil.rmtree(target)

    assert target.exists(), "directory must still exist after refused rmtree"


# ── AC-5 (control): rmtree still works for non-live paths ────────────────────


def test_fixture_allows_rmtree_of_non_live_path(tmp_path: Path) -> None:
    """Control: shutil.rmtree(tmp_path / "x") still works under the fixture."""
    target = tmp_path / "x"
    target.mkdir()
    (target / "file.txt").write_text("hello")

    shutil.rmtree(target)

    assert not target.exists(), "directory must be removed"


# ── helpers ──────────────────────────────────────────────────────────────────

import textwrap  # noqa: E402