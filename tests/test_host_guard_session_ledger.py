"""The session ledger must catch a guard call that the test swallowed.

testing-principles §5: raising reds the test that made the call, but a
`BaseException` is still catchable.  Pair the raise with a session-level
ledger — record every blocked call and fail the run at
``pytest_sessionfinish`` if any were recorded but did not red a test.  That
second half is what catches code which swallowed even the `BaseException`.

Found dead on 2026-08-26 while checking whether xdist would break it — it
was broken sequentially too.  ``exempts_recorded_during`` was
``autouse=True`` and unconditional:

    before = len(_host_blocked_calls)
    yield
    del _host_blocked_calls[before:]

so EVERY test dropped its recordings, and ``_host_blocked_calls`` was always
empty when ``pytest_sessionfinish`` read it.  Measured: a test doing
``try: Popen(["launchctl", ...]) except BaseException: pass`` produced exit
0 and zero violations.

AC-5 asked for a *narrow* exemption — "scoped so it drops only the calls
recorded during that test", for the guard's own self-checks which trip it
deliberately.  Universal autouse turned the narrow exemption into a blanket
amnesty, which is the whole detection half of §5 silently disabled.  An
absent gate reads exactly like a passing one.

These tests run pytest as a SUBPROCESS because the behaviour under test is
``pytest_sessionfinish`` — it cannot be observed from inside the session it
would fail.  The probe file must live inside the repo to inherit the root
conftest, so it is created and removed in a ``finally``.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_probe(source: str) -> subprocess.CompletedProcess:
    """Write a throwaway test inside the repo, run it, always clean up."""
    probe = REPO_ROOT / "tests" / f"_probe_{uuid.uuid4().hex[:8]}.py"
    probe.write_text(textwrap.dedent(source), encoding="utf-8")
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", str(probe), "-q",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=120,
            cwd=str(REPO_ROOT), encoding="utf-8",
        )
    finally:
        probe.unlink(missing_ok=True)


# ── the detection half of §5 ────────────────────────────────────────────────

class TestSwallowedCallStillFailsTheRun:

    def test_swallowed_baseexception_fails_the_session(self) -> None:
        r = _run_probe('''
            import subprocess

            def test_swallows():
                try:
                    subprocess.Popen(["launchctl", "list"])
                except BaseException:
                    pass          # the exact swallow §5 exists to catch
                assert True
        ''')
        assert r.returncode != 0, (
            "a launchctl call swallowed by the test passed the run.  The "
            "session ledger is not enforcing.\n"
            f"stdout={r.stdout[-400:]!r}\nstderr={r.stderr[-400:]!r}"
        )

    def test_the_failure_names_the_binary_and_the_test(self) -> None:
        r = _run_probe('''
            import subprocess

            def test_swallows_quietly():
                try:
                    subprocess.Popen(["launchctl", "list"])
                except BaseException:
                    pass
        ''')
        combined = r.stdout + r.stderr
        assert "HOST-GUARD VIOLATION" in combined, combined[-600:]
        assert "test_swallows_quietly" in combined, (
            "the report must name the offending test"
        )


# ── the exemption must stay narrow ──────────────────────────────────────────

class TestExemptionIsNarrow:

    def test_marked_test_may_trip_the_guard_without_failing_the_run(self) -> None:
        """The guard's own self-checks trip it deliberately and catch the raise."""
        r = _run_probe('''
            import subprocess
            import pytest

            @pytest.mark.expects_blocked_host
            def test_self_check():
                with pytest.raises(BaseException):
                    subprocess.Popen(["launchctl", "list"])
        ''')
        assert r.returncode == 0, (
            "a test explicitly marked expects_blocked_host still failed the "
            f"session.\nstdout={r.stdout[-400:]!r}\nstderr={r.stderr[-400:]!r}"
        )

    def test_an_unmarked_neighbour_is_still_caught(self) -> None:
        """The exemption must not leak to other tests in the same file.

        The original bug was exactly this leak, taken to its limit.
        """
        r = _run_probe('''
            import subprocess
            import pytest

            @pytest.mark.expects_blocked_host
            def test_marked_self_check():
                with pytest.raises(BaseException):
                    subprocess.Popen(["launchctl", "list"])

            def test_unmarked_swallower():
                try:
                    subprocess.Popen(["launchctl", "print", "gui/501/x"])
                except BaseException:
                    pass
        ''')
        assert r.returncode != 0, (
            "an unmarked test's swallowed call was cleared by its marked "
            "neighbour — the exemption is leaking again"
        )
        assert "test_unmarked_swallower" in (r.stdout + r.stderr)


# ── a clean run stays clean ─────────────────────────────────────────────────

def test_a_run_touching_nothing_passes() -> None:
    """Regression guard: this must not become a blanket session failure."""
    r = _run_probe('''
        import subprocess

        def test_allowed_binary():
            p = subprocess.Popen(["git", "--version"],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            assert p.wait(timeout=30) == 0
    ''')
    assert r.returncode == 0, (
        f"a clean run failed.\nstdout={r.stdout[-400:]!r}\nstderr={r.stderr[-400:]!r}"
    )


def test_expects_blocked_host_marker_is_registered(request) -> None:
    """An unregistered marker silently exempts nothing."""
    markers = request.config.getini("markers")
    assert any(m.startswith("expects_blocked_host") for m in markers)
