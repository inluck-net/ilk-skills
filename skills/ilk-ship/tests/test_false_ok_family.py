"""Regression tests for the false-ok family.

Three releases shipped a state meaning "checked and current" on a path
that did not check the thing that matters:

  | release | what returned `ok`          | what it had not checked          |
  |---------|-----------------------------|----------------------------------|
  | v0.9.74 | a host whose bounce failed  | that the daemon came back        |
  | v0.9.87 | a host never contacted      | that ssh happened at all         |
  | current | a host at an older tag      | that the host is on the release  |

Each test asserts the resolver does NOT return `ok`.  The third is red
at this step by construction — that is the point.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "skills" / "ilk-ship" / "scripts"
_BOUNCE_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from host_deploy_status import resolve_host  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers (mirror test_host_deploy_status.py patterns)
# ---------------------------------------------------------------------------

def _write_fake_bouncer(
    tmp_path: Path,
    *,
    output_lines: list[str] | None = None,
    exit_code: int = 0,
) -> Path:
    """Create a fake bounce_daemons.sh that emits fixed output."""
    fake = tmp_path / "bin" / "bounce_daemons.sh"
    fake.parent.mkdir(parents=True, exist_ok=True)

    lines = ["#!/usr/bin/env bash"]
    for line in (output_lines or []):
        lines.append(f'echo "{line}"')
    lines.append(f"exit {exit_code}")
    lines.append("")

    fake.write_text("\n".join(lines), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


def _write_fake_ssh(
    tmp_path: Path,
    *,
    output_lines: list[str] | None = None,
    exit_code: int = 0,
) -> Path:
    """Create a fake ssh that emits fixed output."""
    fake = tmp_path / "fakessh" / "ssh"
    fake.parent.mkdir(parents=True, exist_ok=True)

    lines = ["#!/usr/bin/env bash"]
    for line in (output_lines or []):
        lines.append(f'echo "{line}"')
    lines.append(f"exit {exit_code}")
    lines.append("")

    fake.write_text("\n".join(lines), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


# ---------------------------------------------------------------------------
# Family member 1: host whose bounce failed (v0.9.74)
# ---------------------------------------------------------------------------

class TestFalseOkBounceFailed:
    """v0.9.74: a host whose bootstrap failed still returned 'ok'.

    The bouncer reports the daemon could not be reached — exit 2,
    'unreachable:' prefix.  The resolver must NOT return 'ok'.
    """

    def test_bounce_failed_is_not_ok(self, tmp_path: Path) -> None:
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "unreachable: scheduler (plist=0 loaded=0)"
        ], exit_code=2)
        result = resolve_host(fake, tmp_path)
        assert result != "ok", (
            "A host whose bounce failed must not report 'ok'. "
            "This is the v0.9.74 false-ok shape."
        )
        assert result == "unreachable"


# ---------------------------------------------------------------------------
# Family member 2: host never contacted (v0.9.87)
# ---------------------------------------------------------------------------

class TestFalseOkNeverContacted:
    """v0.9.87: a host that was never contacted still returned 'ok'.

    The ssh transport fails (exit 255) — the resolver must NOT return 'ok'.
    Uses a remote_host so the code path exercises ssh.
    """

    def test_never_contacted_is_not_ok(self, tmp_path: Path) -> None:
        fake_ssh = _write_fake_ssh(tmp_path, output_lines=[], exit_code=255)
        result = resolve_host(
            Path("/remote/clone/skills/ilk-watchdog/scripts/bounce_daemons.sh"),
            tmp_path,
            remote_host="rezmac",
            ssh_program=str(fake_ssh),
        )
        assert result != "ok", (
            "A host that was never contacted must not report 'ok'. "
            "This is the v0.9.87 false-ok shape."
        )
        assert result == "unreachable"


# ---------------------------------------------------------------------------
# Family member 3: host at an older tag (current defect)
# ---------------------------------------------------------------------------

class TestFalseOkOlderTag:
    """A host whose daemon code does not resolve to the required tag.

    The bouncer says 'fresh' because toolkit_head matches HEAD — but HEAD
    is an older tag, not the one we just shipped.  Without --require-tag,
    this returns 'ok'.  With it, the resolver must check that the recorded
    sha resolves to the expected tag.

    THIS TEST IS RED BY CONSTRUCTION — the --require-tag flag does not
    exist yet.  That is the point of step 0.
    """

    def test_older_tag_is_not_ok(self, tmp_path: Path) -> None:
        """A host at v0.9.86 must not read 'ok' when we require v0.9.87."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "fresh: scheduler — fresh (toolkit_head matches HEAD)"
        ], exit_code=0)
        result = resolve_host(
            fake, tmp_path,
            require_tag="v0.9.87",
        )
        assert result != "ok", (
            "A host at an older tag must not report 'ok'. "
            "This is the current false-ok shape."
        )
