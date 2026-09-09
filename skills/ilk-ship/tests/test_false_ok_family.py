"""Regression tests for the false-ok family.

Three releases shipped a state meaning "checked and current" on a path
that did not check the thing that matters:

  | release | what returned `ok`          | what it had not checked          |
  |---------|-----------------------------|----------------------------------|
  | v0.9.74 | a host whose bounce failed  | that the daemon came back        |
  | v0.9.87 | a host never contacted      | that ssh happened at all         |
  | v0.9.94 | a host at an older tag      | that the host is on the release  |
  | current | a host with a DIRTY TREE    | that the files are the tag       |

Each test asserts the resolver does NOT return `ok`.  The fourth is red
at this step by construction — that is the point.

The fourth member is the one the v0.9.93 tag body predicted: "Phase 4's
`--require-tag` checks the daemon's recorded sha but never whether the
working tree is clean, so a host running uncommitted code reports `ok`."
It is not hypothetical — rezmac reported `ok` at v0.9.92 while carrying the
uncommitted modification that became v0.9.93, and the only reason both hosts
were trustworthy at v0.9.94 is that two sessions hand-asserted
`git status --porcelain` three times each.
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


# ---------------------------------------------------------------------------
# Family member 4: host whose working tree is dirty (current)
# ---------------------------------------------------------------------------

class TestFalseOkDirtyTree:
    """A recorded sha that resolves to the tag says nothing about the FILES.

    `--require-tag` answers "is this host running the code at <tag>?" by
    resolving the daemon's recorded `toolkit_head`. A dirty working tree means
    the files on disk are not that commit — the daemon is running edits that
    exist in no commit anywhere. The sha check passes and the answer is still
    no.

    Same shape as the other three: a state meaning "checked and current"
    returned on a path that did not check the thing that matters.
    """

    def test_dirty_tree_is_not_ok(self, tmp_path: Path) -> None:
        """The reproducible case: rezmac at v0.9.92 over uncommitted code."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "recorded_sha: 800efd5cab82fae3ac18b7267c7734548a678244",
            "tree_state: dirty",
            "fresh: scheduler — fresh (toolkit_head matches HEAD)",
        ], exit_code=0)
        result = resolve_host(
            fake, tmp_path,
            require_tag="v0.9.93",
            tag_resolver=lambda _sha: "v0.9.93",   # the sha DOES match
        )
        assert result != "ok", (
            "a host whose recorded sha resolves to the required tag but whose "
            "working tree is dirty must not report 'ok' — the daemon is "
            "running code that exists in no commit"
        )
        assert result == "dirty-tree", (
            f"got {result!r}; the state must NAME the problem, because "
            "'tag-mismatch' would send an operator looking at the wrong thing "
            "— the tag is right and the tree is not"
        )

    def test_clean_tree_at_the_right_tag_is_still_ok(self, tmp_path: Path) -> None:
        """The guard must not make every conformant host red."""
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "recorded_sha: da20e6cee03e31ec5123d64e1ad81c71cdd36f8b",
            "tree_state: clean",
            "fresh: scheduler — fresh (toolkit_head matches HEAD)",
        ], exit_code=0)
        result = resolve_host(
            fake, tmp_path,
            require_tag="v0.9.94",
            tag_resolver=lambda _sha: "v0.9.94",
        )
        assert result == "ok", f"got {result!r}"

    def test_absent_tree_state_does_not_fail_closed_into_dirty(
        self, tmp_path: Path,
    ) -> None:
        """An older bouncer emits no tree_state line.

        Judgment call: absence is treated as UNKNOWN and does not by itself
        produce `dirty-tree`, because a host mid-upgrade runs the old bouncer
        and would otherwise report dirty forever with nothing an operator
        could do about it. The sha check still applies. Wrong if a host is
        ever left indefinitely on a bouncer too old to report — which the
        release path makes visible, since upgrading the bouncer IS the deploy.
        """
        fake = _write_fake_bouncer(tmp_path, output_lines=[
            "recorded_sha: da20e6cee03e31ec5123d64e1ad81c71cdd36f8b",
            "fresh: scheduler — fresh (toolkit_head matches HEAD)",
        ], exit_code=0)
        result = resolve_host(
            fake, tmp_path,
            require_tag="v0.9.94",
            tag_resolver=lambda _sha: "v0.9.94",
        )
        assert result == "ok", (
            f"got {result!r} — an absent tree_state must not be read as dirty"
        )


    def test_dirty_tree_exits_1_not_2(self) -> None:
        """The CLI exit code, which resolve_host() cannot exercise.

        `_STATE_EXIT_CODES` defaults to 2 for unknown states, so a new state
        added without an entry reports a REACHED, answering host as if it
        could not be contacted — 2 means "could not tell" and belongs to
        unreachable. Every unit test above calls resolve_host() directly and
        would have stayed green; this was caught by running the CLI.
        """
        from host_deploy_status import _STATE_EXIT_CODES
        assert _STATE_EXIT_CODES.get("dirty-tree") == 1, (
            "dirty-tree must exit 1 (non-conformant host, reached), not fall "
            "through to the default 2 (could not tell)"
        )
        assert set(_STATE_EXIT_CODES) == {
            "ok", "stale-daemon", "tag-mismatch", "dirty-tree", "unreachable",
        }, (
            "every documented state needs an explicit exit code; the .get() "
            "default hides a missing one as 'unreachable'"
        )
