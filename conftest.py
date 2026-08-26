"""Root pytest configuration for the ilk-skills toolkit.

The suite is not a package: test modules live in many sibling
``skills/*/tests`` and ``tools/*/tests`` directories, several share a basename
(``test_status_all_repo_path.py`` etc.), and a few import a sibling module
directly (``from test_master_selection_agreement import ...``).

Two things make a single root-level ``pytest`` run work:

* ``--import-mode=importlib`` (set in ``pytest.ini``) — lets same-named test
  modules coexist, which the default ``prepend`` mode cannot.
* this file — importlib mode does *not* add each test file's own directory to
  ``sys.path``, so sibling imports would fail. We re-add them explicitly.

Without both, a root ``pytest`` run dies during collection and the suite only
works when invoked one directory at a time.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent

# Directories that may hold importable test helpers / modules under test.
_SEARCH_BASES = ("skills", "tools", "tests")


def _dirs_with_tests() -> list[Path]:
    """Every directory under the search bases that contains test modules."""
    found: list[Path] = []
    for base in _SEARCH_BASES:
        root = _ROOT / base
        if not root.is_dir():
            continue
        if any(root.glob("test_*.py")):
            found.append(root)
        for path in root.rglob("*"):
            if path.is_dir() and any(path.glob("test_*.py")):
                found.append(path)
    return found


def pytest_configure(config) -> None:
    """Put each test directory on sys.path, and register guard markers.

    Both duties live in ONE hook on purpose: a second ``def
    pytest_configure`` in this module would silently replace the first,
    and the failure would surface as unrelated import errors during
    collection rather than as a missing marker.
    """
    for directory in _dirs_with_tests():
        entry = str(directory)
        if entry not in sys.path:
            sys.path.insert(0, entry)
    config.addinivalue_line(
        "markers",
        "allow_launchctl: exempt this test from the host-mutation guard",
    )
    config.addinivalue_line(
        "markers",
        "expects_blocked_host: this test deliberately trips the host-mutation "
        "guard and catches the raise; drop its recordings from the session "
        "ledger",
    )


def _ambiguous_module_names() -> set[str]:
    """Module basenames that exist in more than one skill/tool scripts dir.

    ``skills/ilk-inbox-tickets/scripts/cli.py`` and
    ``skills/ilk-lark-tickets/scripts/cli.py`` are different modules competing
    for the name ``cli``. Each test file prepends its *own* scripts dir to
    sys.path and then does ``import cli``, which works in isolation but not in
    a combined run: the first import wins ``sys.modules["cli"]`` and every
    later test file silently gets the wrong module.
    """
    seen: dict[str, int] = {}
    for base in ("skills", "tools"):
        root = _ROOT / base
        if not root.is_dir():
            continue
        for scripts_dir in root.glob("*/scripts"):
            for module in scripts_dir.glob("*.py"):
                seen[module.stem] = seen.get(module.stem, 0) + 1
    return {name for name, count in seen.items() if count > 1}


_AMBIGUOUS = _ambiguous_module_names()


# Stub source for `live_ilk_pid`.  The orphan check is a safety net: if
# pytest is killed before teardown, the stub notices its parent changed
# and exits on its own rather than sleeping out the hour.
_STUB_SRC = """\
import os
import time

_parent = os.getppid()
_deadline = time.time() + 3600
while time.time() < _deadline:
    time.sleep(0.5)
    if os.getppid() != _parent:
        break
"""


@pytest.fixture(scope="session")
def live_ilk_pid():
    """PID of a live process that reads as an ilk runner.

    Sentinel liveness is command-verified (``pid_health.ilk_pid_alive``),
    so ``os.getpid()`` can no longer stand in for "a running loop" — the
    pytest process is precisely the unrelated command a recycled PID
    lands on, which is the bug that check exists to catch.  A test that
    needs a *live run* needs a process whose argv names a runner.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        stub = Path(tmpdir) / "run_ilk_loop_stub.py"
        stub.write_text(_STUB_SRC, encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(stub)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            yield proc.pid
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


def pytest_collectstart(collector) -> None:
    """Evict ambiguous modules so each test file re-imports its own copy.

    Fires before pytest imports each test module, i.e. before that module's
    own ``sys.path.insert(0, .../scripts)`` + ``import cli`` runs. Already
    imported test modules hold direct references to the names they bound, so
    dropping the cache entry here does not affect them.
    """
    for name in _AMBIGUOUS:
        sys.modules.pop(name, None)


# ───────────────────────────────────────────────────────────────────────────
# Host-mutation guard — suite-wide.
#
# A unit test must not shell out to a host-mutating binary.  Promoted here
# from skills/ilk-loop/tests/conftest.py on 2026-08-26: a conftest is scoped
# to its own directory and below, so the guard covered ilk-loop's tests only
# while the offender it was written for
# (skills/ilk-watchdog/tests/test_install_scheduler_autostart.py) sat in a
# sibling tree and ran unguarded.  Measured in the batch gate at 14:41: that
# file reported ".....s" with its launchctl calls passing straight through.
#
# Deny-list: `launchctl` alone.  `git` is deliberately absent — our git runs
# offline against tmp_path repos and is frequently the property under test
# (testing-principles §2).  Add a binary when a call site appears, not
# speculatively.
#
# Report-only: ILK_TEST_GUARD_REPORT=1 records and allows through.
# ───────────────────────────────────────────────────────────────────────────
# ── Guard configuration ─────────────────────────────────────────────────

_HOST_DENYLIST = frozenset({"launchctl"})
_HOST_REPORT_ENV = "ILK_TEST_GUARD_REPORT"
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


@pytest.fixture(autouse=True)
def _host_guard_active(request: pytest.FixtureRequest, tmp_path_factory):
    """Patch ``subprocess.Popen`` and prepend a deny-shim to PATH.

    Two layers, both needed:

    1. **Popen patch** — catches direct Python ``subprocess`` calls whose
       argv basename is in ``_HOST_DENYLIST``.  Produces the best message
       because it sees the exact argv.
    2. **PATH deny-shim** — a temp dir prepended to ``PATH`` containing a
       fake ``launchctl`` that records its invocation and exits non-zero.
       Catches ``launchctl`` reached through spawned shell scripts (the gap
       that let the real daemon get booted out during
       ``test_bounce_daemons.py``).

    A test that installs its own fake earlier on ``PATH`` (as ``_run_bounce``
    does) still wins — the deny-shim is a backstop, not the primary mechanism.

    Autouse: the leak is defined by what a test *forgot* to guard against.
    An opt-in guard is only consulted by the tests that did not need it.
    """
    if request.node.get_closest_marker("allow_launchctl"):
        yield
        return

    nodeid = request.node.nodeid

    # -- PATH deny-shim layer --
    shim_dir = tmp_path_factory.mktemp("_guard_shim")
    shim_log = shim_dir / "shim_invocations.log"
    shim_log.write_text("", encoding="utf-8")

    shim_path = shim_dir / "launchctl"
    # Report-only is honoured here too, not just in the Popen layer below.
    # A shim that always exits 126 makes report-only mode a lie for anything
    # reached through a spawned shell (measured 2026-08-26: it broke
    # test_host_guard.py::TestReportOnlyMode with rc=126).
    shim_path.write_text(
        f"#!/usr/bin/env bash\n"
        f'echo "$@" >> "{shim_log}"\n'
        f'if [[ -n "${{{_HOST_REPORT_ENV}:-}}" && "${{{_HOST_REPORT_ENV}}}" != "0" ]]; then\n'
        f'  [[ -x /bin/launchctl ]] && exec /bin/launchctl "$@"\n'
        f"  exit 0\n"
        f"fi\n"
        f'echo "HostMutationBlocked: launchctl was reached through a spawned shell: $@" >&2\n'
        f"exit 126\n",
        encoding="utf-8",
    )
    shim_path.chmod(shim_path.stat().st_mode | stat.S_IEXEC)

    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{shim_dir}:{original_path}"

    # -- Popen patch layer --
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
                # Report-only behind an env var; enforce by default.
                # Check at call time so a test's monkeypatch.setenv takes
                # effect before the guard sees the call.
                is_report = os.environ.get(
                    _HOST_REPORT_ENV, "",
                ).strip().lower() not in ("", "0", "false", "no")
                if not is_report:
                    raise HostMutationBlocked(
                        f"{nodeid} shelled out to `{prog}`, a host-mutating binary:\n"
                        f"    {described}\n"
                        f"A unit test must not mutate production infrastructure. "
                        f"If this test genuinely needs the real binary, mark it "
                        f"`@pytest.mark.allow_launchctl` and say why.\n"
                        f"To inventory every such call instead of failing on the "
                        f"first, run with {_HOST_REPORT_ENV}=1."
                    )
            super().__init__(*args, **kwargs)

    try:
        with patch.object(subprocess, "Popen", GuardedPopen):
            yield
    finally:
        # -- Record deny-shim invocations to the session ledger --
        os.environ["PATH"] = original_path
        invocations = [
            line for line in shim_log.read_text().splitlines() if line.strip()
        ]
        if invocations:
            _host_blocked_calls.append(
                (nodeid, f"launchctl via shell: {invocations[0]}")
            )
            # Drop this test's recording if it deliberately trips the guard.
            # (The Popen-layer recording is dropped by exempts_recorded_during;
            #  this PATH-layer recording needs the same courtesy.)
            if request.node.get_closest_marker("expects_blocked_host"):
                del _host_blocked_calls[-1]


@pytest.fixture(autouse=True)
def exempts_recorded_during(request: pytest.FixtureRequest):
    """Drop guard recordings for tests that trip the guard ON PURPOSE.

    Scoped to the ``expects_blocked_host`` marker.  It used to clear
    unconditionally for EVERY test, which emptied ``_host_blocked_calls``
    before ``pytest_sessionfinish`` could ever read it — turning a narrow
    exemption into a blanket amnesty and silently disabling the detection
    half of testing-principles §5.  Measured 2026-08-26: a test doing
    ``try: Popen(["launchctl", ...]) except BaseException: pass`` produced
    exit 0 and zero violations.

    The raise alone is not enough: a ``BaseException`` is still catchable,
    and production code here converts subprocess failure into an
    unanswerable verdict by design.  The ledger is what catches the swallow.

    Only the slice recorded during THIS test is dropped, so a marked test
    cannot clear a neighbour's recordings.
    """
    if not request.node.get_closest_marker("expects_blocked_host"):
        yield
        return
    before = len(_host_blocked_calls)
    yield
    del _host_blocked_calls[before:]


def _enforce_no_host_mutations(session) -> None:
    """Fail the session if any host-mutation call was not accounted for (AC-4)."""
    if not _host_blocked_calls:
        return
    is_report = os.environ.get(
        _HOST_REPORT_ENV, "",
    ).strip().lower() not in ("", "0", "false", "no")
    if is_report:
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
        + "Stub the call, or mark the test:\n"
          "  @pytest.mark.allow_launchctl      — it genuinely needs the real binary\n"
          "  @pytest.mark.expects_blocked_host — it trips the guard on purpose "
          "and catches the raise (guard self-checks)\n",
        file=sys.stderr,
    )


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Enforce the host-mutation guard at session end (AC-4)."""
    _enforce_no_host_mutations(session)
