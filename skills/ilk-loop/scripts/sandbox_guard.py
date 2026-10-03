"""Sandbox write guard.

Sub-plan ``a-sandbox-cannot-write-the-stable-home`` (MASTER-2026-10-02d).

When ``ILK_SANDBOX == "1"``, any write that resolves into the stable data
home is refused.  The stable home is ``ILK_STABLE_DATA_HOME`` when set,
otherwise the real user's ``~/.ilk-data`` (resolved from
``pwd.getpwuid(os.getuid()).pw_dir``).

``stable_write_refusal(path)`` returns a reason string when the write
should be refused, or ``None`` when it is allowed.  ``SandboxWriteRefused``
is the exception raised by callers.
"""
from __future__ import annotations

import os
import pwd
from pathlib import Path


class SandboxWriteRefused(PermissionError):
    """Raised when a sandboxed process attempts to write into the stable home."""


def _stable_data_home() -> Path:
    """Resolve the stable data home from the environment or the real user."""
    env = os.environ.get("ILK_STABLE_DATA_HOME")
    if env:
        return Path(env).resolve()
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return (real_home / ".ilk-data").resolve()


def stable_write_refusal(path: Path | str) -> str | None:
    """Return a reason string if *path* is inside the stable data home.

    Returns ``None`` when the write is allowed (not sandboxed, or the
    target is outside the stable home).
    """
    if os.environ.get("ILK_SANDBOX") != "1":
        return None

    stable = _stable_data_home()
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(stable)
    except ValueError:
        return None
    return (
        f"refusing to write {resolved}: inside the stable data home {stable}"
    )