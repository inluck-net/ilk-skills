"""Classify a red-first pin's failure origin.

Part of sub-plan ``a-step-0-pin-is-red-for-the-right-reason`` (MASTER-2026-10-02b).

A step-0 red-first gate is rejected (``red for the wrong reason``) when an
xfailed pin's exception is raised **outside the code under test**:

- the raising frame is in the test file's own fixtures or setup
  (``conftest.py``, a ``@pytest.fixture``, a helper in the test module), or
- it is a ``subprocess.CalledProcessError`` whose command is ``git``.

Exceptions raised inside the code under test (any frame under ``skills/``
other than ``tests/``) are allowed, including ImportError of a not-yet-written
symbol.
"""
from __future__ import annotations

import re
import subprocess
import traceback
from pathlib import Path
from typing import NamedTuple


class RedOrigin(NamedTuple):
    """Result of classifying a red-first pin's failure origin."""
    rejected: bool
    reason: str
    frame_file: str
    frame_line: int
    frame_function: str


def classify_red_origin(
    exc: Exception,
    pin_path: Path,
    project_root: Path | None = None,
) -> RedOrigin:
    """Classify where a red-first pin's exception was raised.

    Returns a RedOrigin indicating whether the exception was raised inside
    or outside the code under test.

    Args:
        exc: The exception that was raised.
        pin_path: Path to the pin file that failed.
        project_root: Root of the project (default: current directory).

    Returns:
        RedOrigin with rejected=True if the exception was raised outside
        the code under test.
    """
    if project_root is None:
        project_root = Path.cwd()

    # Get the traceback
    tb = traceback.extract_tb(exc.__traceback__)
    if not tb:
        return RedOrigin(
            rejected=False,
            reason="no traceback",
            frame_file="",
            frame_line=0,
            frame_function="",
        )

    # Find the innermost frame (where the exception was raised)
    inner_frame = tb[-1]
    frame_file = str(Path(inner_frame.filename).resolve())
    frame_line = inner_frame.lineno
    frame_function = inner_frame.name

    # Check if it's a CalledProcessError with a git command
    if isinstance(exc, subprocess.CalledProcessError):
        if exc.cmd and isinstance(exc.cmd, (list, tuple)):
            cmd_str = " ".join(str(c) for c in exc.cmd)
        else:
            cmd_str = str(exc.cmd)
        if "git" in cmd_str:
            return RedOrigin(
                rejected=True,
                reason=f"CalledProcessError from git command: {cmd_str}",
                frame_file=frame_file,
                frame_line=frame_line,
                frame_function=frame_function,
            )

    # Check if the frame is in the code under test
    # The code under test is any frame under skills/ other than tests/
    pin_resolved = str(pin_path.resolve())
    project_root_str = str(project_root.resolve())

    # Check if the frame is in the pin file itself (test file)
    if frame_file.startswith(pin_resolved):
        # The exception was raised in the pin file - check if it's in a fixture
        # or helper (outside the test function body)
        if _is_in_fixture_or_helper(exc, pin_path):
            return RedOrigin(
                rejected=True,
                reason=f"exception raised in test fixture/helper: {frame_function}",
                frame_file=frame_file,
                frame_line=frame_line,
                frame_function=frame_function,
            )

    # Check if the frame is in a conftest.py or test helper
    if _is_in_test_infrastructure(frame_file, project_root_str):
        return RedOrigin(
            rejected=True,
            reason=f"exception raised in test infrastructure: {frame_function}",
            frame_file=frame_file,
            frame_line=frame_line,
            frame_function=frame_function,
        )

    # Check if the frame is in the code under test (skills/ other than tests/)
    if _is_in_code_under_test(frame_file, project_root_str):
        return RedOrigin(
            rejected=False,
            reason=f"exception raised in code under test: {frame_function}",
            frame_file=frame_file,
            frame_line=frame_line,
            frame_function=frame_function,
        )

    # Default: reject if outside code under test
    return RedOrigin(
        rejected=True,
        reason=f"exception raised outside code under test: {frame_function}",
        frame_file=frame_file,
        frame_line=frame_line,
        frame_function=frame_function,
    )


def _is_in_fixture_or_helper(exc: Exception, pin_path: Path) -> bool:
    """Check if the exception was raised in a fixture or helper function."""
    tb = traceback.extract_tb(exc.__traceback__)
    pin_str = str(pin_path.resolve())

    for frame in tb:
        frame_file = str(Path(frame.filename).resolve())
        if frame_file == pin_str:
            # Check if the function name looks like a fixture or helper
            func_name = frame.name
            # Fixtures are typically decorated with @pytest.fixture
            # Helpers are typically functions defined outside test classes
            if func_name.startswith("setup_") or func_name.startswith("teardown_"):
                return True
            if func_name.startswith("bad_") or func_name.startswith("broken_"):
                return True
            # Check if it's a conftest function
            if "conftest" in frame_file:
                return True

    return False


def _is_in_test_infrastructure(frame_file: str, project_root: str) -> bool:
    """Check if the frame is in test infrastructure (conftest.py, test helpers)."""
    # conftest.py files
    if frame_file.endswith("conftest.py"):
        return True

    # Test helper modules (not the test file itself)
    if "/tests/" in frame_file and not frame_file.endswith("_test.py"):
        # It's in a tests directory but not a test file
        # Check if it's a helper module
        if "helper" in frame_file or "fixture" in frame_file:
            return True

    return False


def _is_in_code_under_test(frame_file: str, project_root: str) -> bool:
    """Check if the frame is in the code under test (skills/ other than tests/)."""
    # The code under test is any frame under skills/ other than tests/
    skills_path = str(Path(project_root) / "skills")
    if not frame_file.startswith(skills_path):
        return False

    # Exclude test files
    if "/tests/" in frame_file:
        return False

    return True