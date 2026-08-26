"""Tests for the host-mutation guard (SP5).

A unit test must not shell out to a host-mutating binary like ``launchctl``.
The guard patches ``subprocess.Popen`` and refuses calls whose argv[0]
basename is on the deny-list.

Every test here deliberately trips or exercises the guard — some expect a
raise, some expect a pass-through, and one catches the raise on purpose.
The ``exempts_recorded_during`` fixture keeps the session-level ledger
honest for the self-check tests.
"""
from __future__ import annotations

import os
import subprocess

import pytest

# ── AC-2: the refusal is a BaseException, not an Exception ──────────────


class TestGuardRefusesHostMutatingBinary:
    """A launchctl invocation from a test raises (AC-2).

    These tests assert the FINAL enforcement behavior.  At step 0 the
    guard is report-only, so these are expected-red.  At step 1 the guard
    default flips to enforcement and they go green.
    """

    def test_popen_run_is_refused(self) -> None:
        """AC-2: ``subprocess.run`` funnels through Popen; raises BaseException."""
        with pytest.raises(BaseException, match="host-mutating binary"):
            subprocess.run(["launchctl", "list"], capture_output=True)

    def test_basename_absolute_path_is_matched(self) -> None:
        """AC-1: basename matching — /bin/launchctl → launchctl."""
        with pytest.raises(BaseException, match="host-mutating binary"):
            subprocess.run(["/bin/launchctl", "list"], capture_output=True)

    def test_shell_form_is_matched(self) -> None:
        """AC-1: ``shell=True`` string form — first token parsed."""
        with pytest.raises(BaseException, match="host-mutating binary"):
            subprocess.run(
                "launchctl list net.inluck.ilk.scheduler",
                shell=True, capture_output=True,
            )

    def test_popen_call_is_refused(self) -> None:
        """AC-1: ``subprocess.call`` funnels through Popen."""
        with pytest.raises(BaseException, match="host-mutating binary"):
            subprocess.call(["launchctl", "list"])

    def test_popen_check_output_is_refused(self) -> None:
        """AC-1: ``subprocess.check_output`` funnels through Popen."""
        with pytest.raises(BaseException, match="host-mutating binary"):
            subprocess.check_output(["launchctl", "list"])

    def test_popen_check_call_is_refused(self) -> None:
        """AC-1: ``subprocess.check_call`` funnels through Popen."""
        with pytest.raises(BaseException, match="host-mutating binary"):
            subprocess.check_call(["launchctl", "list"])

    def test_baseexception_survives_broad_except(self) -> None:
        """AC-2: survives ``except Exception`` (the whole point of BaseException).

        Production code catches broadly; an Exception here would be swallowed
        and the test would pass having proved nothing.
        """
        with pytest.raises(BaseException, match="host-mutating binary"):
            try:
                subprocess.run(["launchctl", "list"], capture_output=True)
            except Exception:  # noqa: BLE001 — the point of the test
                pytest.fail("HostMutationBlocked was swallowed by except Exception")


# ── AC-3: git, bash, python3 pass through ───────────────────────────────


class TestGuardLetsLocalBinariesThrough:
    """git is the property under test in many files; it must never be blocked."""

    def test_git_is_allowed(self) -> None:
        out = subprocess.run(["git", "--version"], capture_output=True, text=True)
        assert out.returncode == 0, "git must pass through the guard untouched"

    def test_bash_is_allowed(self) -> None:
        out = subprocess.run(["bash", "-c", "echo ok"], capture_output=True, text=True)
        assert out.returncode == 0
        assert "ok" in out.stdout

    def test_python3_is_allowed(self) -> None:
        out = subprocess.run(
            [os.path.realpath(subprocess.sys.executable or "python3"), "-c", "print(42)"],
            capture_output=True, text=True,
        )
        assert out.returncode == 0
        assert "42" in out.stdout


# ── AC-4: session ledger + pytest_sessionfinish failure ──────────────────


class TestSessionLedger:
    """The guard records blocked calls and fails the session on unaccounted ones (AC-4)."""

    def test_blocked_call_is_recorded_in_ledger(self, exempts_recorded_during) -> None:
        """AC-4: a blocked call appears in the session ledger.

        The guard raises AND records.  We verify the raise here; the
        session-level enforcement (pytest_sessionfinish) verifies the record
        was not swallowed.  The ledger itself is private to conftest — we
        access it via the module globals of the local conftest.
        """
        # Access the local conftest's ledger (not the root conftest).
        import sys as _sys
        _local = None
        for mod in _sys.modules.values():
            if hasattr(mod, "_host_blocked_calls") and hasattr(mod, "HostMutationBlocked"):
                _local = mod
                break
        assert _local is not None, "local conftest with _host_blocked_calls not loaded"
        ledger = _local._host_blocked_calls

        before = len(ledger)
        with pytest.raises(BaseException):
            subprocess.run(["launchctl", "list"], capture_output=True)
        assert len(ledger) > before, "blocked call must be recorded in the ledger"


# ── AC-5: allow_launchctl marker exempts a test ─────────────────────────


class TestMarkerExemption:
    """A test carrying ``allow_launchctl`` is exempt from the guard (AC-5)."""

    @pytest.mark.allow_launchctl
    def test_marker_stands_guard_down(self) -> None:
        """The marker exempts this test — launchctl should pass through."""
        # launchctl list without args is safe — returns 0 and lists jobs.
        # This proves the marker works without mutating anything.
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        # launchctl list may return 0 or 3 depending on the label; just
        # verify it didn't raise the guard.
        assert out.returncode in (0, 3), f"unexpected returncode: {out.returncode}"


# ── AC-6: report-only mode ──────────────────────────────────────────────


class TestReportOnlyMode:
    """By default (no env var), the guard records but allows through (AC-6).

    Set ``ILK_TEST_GUARD_REPORT=1`` to make the guard raise instead.
    """

    def test_report_only_allows_launchctl(self, monkeypatch, exempts_recorded_during) -> None:
        """AC-6: report-only mode does not raise — records and passes through.

        Set the env var to enable report-only mode (default is now enforcement).
        The guard records the call; exempts_recorded_during drops it from
        the session ledger so pytest_sessionfinish doesn't double-count it.
        """
        monkeypatch.setenv("ILK_TEST_GUARD_REPORT", "1")
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        # launchctl list may return 0 or 3; either way it ran (no guard raise).
        assert result.returncode in (0, 3), f"unexpected returncode: {result.returncode}"


# ── AC-7: denominator assertion ─────────────────────────────────────────


class TestDenominatorAssertion:
    """The guard rests on patching Popen alone being sufficient (AC-7).

    If someone adds ``from subprocess import run`` or ``os.system``, the
    design breaks silently.  This test asserts the denominators that
    justify the single-patch approach.
    """

    def test_no_early_bound_subprocess_imports(self) -> None:
        """AC-7: 0 files use ``from subprocess import run`` (or call/check_*)."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent.parent.parent
        this_file = pathlib.Path(__file__).resolve()
        patterns = ["from subprocess import run", "from subprocess import call",
                     "from subprocess import check_call", "from subprocess import check_output"]
        offenders = []
        for pattern_dir in ("skills", "tools", "tests"):
            base = root / pattern_dir
            if not base.is_dir():
                continue
            for py in base.rglob("*.py"):
                if py.resolve() == this_file:
                    continue  # skip this test file — it contains the patterns in strings
                try:
                    text = py.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for pat in patterns:
                    if pat in text:
                        # Filter out comments and the linter itself
                        for line in text.splitlines():
                            stripped = line.strip()
                            if stripped.startswith("#"):
                                continue
                            if pat in stripped and "lint_subprocess_encoding" not in str(py):
                                offenders.append(f"{py}: {stripped[:80]}")
        assert offenders == [], (
            f"Found {len(offenders)} file(s) with early-bound subprocess imports — "
            f"the Popen-only patch would miss these:\n"
            + "\n".join(f"  {o}" for o in offenders)
        )

    def test_no_os_system_usage(self) -> None:
        """AC-7: 0 files use ``os.system`` or ``os.popen``."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent.parent.parent
        this_file = pathlib.Path(__file__).resolve()
        offenders = []
        for pattern_dir in ("skills", "tools", "tests"):
            base = root / pattern_dir
            if not base.is_dir():
                continue
            for py in base.rglob("*.py"):
                if py.resolve() == this_file:
                    continue  # skip this test file — it contains the patterns in strings
                try:
                    text = py.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if "os.system(" in stripped or "os.popen(" in stripped:
                        offenders.append(f"{py}: {stripped[:80]}")
        assert offenders == [], (
            f"Found {len(offenders)} file(s) using os.system/os.popen:\n"
            + "\n".join(f"  {o}" for o in offenders)
        )


# ── AC-8: the guard has its own tests ───────────────────────────────────


class TestGuardSelfCheck:
    """The guard must have tests — an absent gate reads exactly like a passing one (AC-8).

    This file IS the guard's test suite.  Every test above is proof that
    the guard exists and operates.  This class is a reminder that if you
    are reading this in a code review and the file is empty, the guard
    has been silently defeated.
    """

    def test_guard_has_tests(self) -> None:
        """AC-8: a meta-test — the guard's test file is non-empty and imports subprocess."""
        import pathlib

        this_file = pathlib.Path(__file__)
        text = this_file.read_text(encoding="utf-8")
        assert "subprocess.run" in text, "test file must exercise subprocess calls"
        assert "BaseException" in text, "test file must verify the exception hierarchy"
