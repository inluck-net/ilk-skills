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

import subprocess
import sys
import tempfile
from pathlib import Path

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


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook signature
    """Put each test directory on sys.path so sibling imports resolve."""
    for directory in _dirs_with_tests():
        entry = str(directory)
        if entry not in sys.path:
            sys.path.insert(0, entry)


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
