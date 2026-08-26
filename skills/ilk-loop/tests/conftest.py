"""Host-mutation guard for the ilk-skills test suite.

A unit test must not shell out to a host-mutating binary.  The guard patches
``subprocess.Popen`` and matches on ``basename(argv[0])``.

Deny-list: ``launchctl`` alone.  ``git`` is deliberately absent — our git runs
offline against tmp_path repos and is frequently the property under test
(testing-principles §2).  ``bash`` and ``python3`` likewise pass through.

Report-only mode (AC-6): set ``ILK_TEST_GUARD_REPORT=1`` to record and allow
through, printing an inventory at session end.

This conftest is local to the test directory — the guard does not affect the
rest of the suite until it is promoted to the root conftest.
"""
from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

# ── Guard configuration ─────────────────────────────────────────────────

_HOST_DENYLIST = frozenset({"launchctl"})
_HOST_ENFORCE_ENV = "ILK_TEST_GUARD_ENFORCE"
_host_blocked_calls: list[tuple[str, str]] = []  # (nodeid, described_argv)


class HostMutationBlocked(BaseException):
    """Raised when a test reaches a host-mutating binary.

    Derives from ``BaseException`` rather than ``Exception`` **deliberately**.
    Production code catches broadly; an ``Exception`` here would be swallowed
    and the test would pass having proved nothing, which is precisely the
    failure this guard exists to prevent (testing-principles §4).
    """


def _argv_basename(args: object, shell: bool) -> str | None:
    """Return basename of the program ``args`` would execute, or None."""
    if isinstance(args, (list, tuple)):
        if not args:
            return None
        first = str(args[0])
    elif isinstance(args, (str, bytes, os.PathLike)):
        first = os.fsdecode(args) if not isinstance(args, str) else args
        if shell:
            first = first.strip().split()[0] if first.strip() else ""
    else:
        return None
    if not first:
        return None
    return os.path.basename(first)


def _describe_argv(args: object) -> str:
    if isinstance(args, (list, tuple)):
        return " ".join(str(x) for x in list(args)[:4])
    return str(args)[:70]


def pytest_configure(config) -> None:  # noqa: ARG001
    """Register the ``allow_launchctl`` marker."""
    config.addinivalue_line(
        "markers",
        "allow_launchctl: exempt this test from the host-mutation guard",
    )


@pytest.fixture(autouse=True)
def _host_guard_active(request: pytest.FixtureRequest):
    """Patch ``subprocess.Popen`` to refuse host-mutating binaries.

    Autouse: the leak is defined by what a test *forgot* to guard against.
    An opt-in guard is only consulted by the tests that did not need it.
    """
    if request.node.get_closest_marker("allow_launchctl"):
        yield
        return

    nodeid = request.node.nodeid
    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            prog = _argv_basename(
                args[0] if args else None,
                bool(kwargs.get("shell")),
            )
            if prog is not None and prog in _HOST_DENYLIST:
                described = _describe_argv(args[0] if args else None)
                _host_blocked_calls.append((nodeid, described))
                # Enforce behind an env var; report-only by default.
                # Step 0 ships report-only; step 1 flips to enforcement.
                # Check at call time so a test's monkeypatch.setenv takes
                # effect before the guard sees the call.
                is_enforced = os.environ.get(
                    _HOST_ENFORCE_ENV, "",
                ).strip().lower() not in ("", "0", "false", "no")
                if is_enforced:
                    raise HostMutationBlocked(
                        f"{nodeid} shelled out to `{prog}`, a host-mutating binary:\n"
                        f"    {described}\n"
                        f"A unit test must not mutate production infrastructure. "
                        f"If this test genuinely needs the real binary, mark it "
                        f"`@pytest.mark.allow_launchctl` and say why.\n"
                        f"To inventory every such call instead of failing on the "
                        f"first, run without {_HOST_ENFORCE_ENV}."
                    )
            super().__init__(*args, **kwargs)

    with patch.object(subprocess, "Popen", GuardedPopen):
        yield


@pytest.fixture()
def exempts_recorded_during() -> None:
    """For tests that deliberately trip the guard and catch the raise.

    Drops calls recorded during the test so the session-level enforcement
    does not double-count them.  Scoped to the test that asked for it.
    """
    before = len(_host_blocked_calls)
    yield
    del _host_blocked_calls[before:]


def _enforce_no_host_mutations(session) -> None:
    """Fail the session if any host-mutation call was not accounted for (AC-4)."""
    if not _host_blocked_calls:
        return
    is_enforced = os.environ.get(
        _HOST_ENFORCE_ENV, "",
    ).strip().lower() not in ("", "0", "false", "no")
    if not is_enforced:
        return  # report-only: the inventory is the deliverable
    by_test: dict[str, int] = {}
    for nodeid, _argv in _host_blocked_calls:
        by_test[nodeid] = by_test.get(nodeid, 0) + 1
    session.exitstatus = 1
    print(
        f"\nHOST-GUARD VIOLATION: {len(_host_blocked_calls)} call(s) to a "
        f"host-mutating binary ({', '.join(sorted(_HOST_DENYLIST))}) in "
        f"{len(by_test)} test(s):\n"
        + "".join(f"  {n}  ({c} call(s))\n" for n, c in sorted(by_test.items()))
        + "Stub the call or mark the test `@pytest.mark.allow_launchctl`.\n",
        file=sys.stderr,
    )


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Enforce the host-mutation guard at session end (AC-4)."""
    _enforce_no_host_mutations(session)
