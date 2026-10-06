"""Spawn a child process in its own session and exec into it.

The scheduler's release train must not share the scheduler job's process group.
When the train's local bounce runs ``launchctl bootout`` on the scheduler,
launchd kills every process in the job's process group (see ``launchd.plist(5)``,
key ``AbandonProcessGroup``).  A train in that group dies with the job.

This helper calls ``os.setsid()`` to place the child in a new session and
process group, then ``os.execvp()`` to replace itself with the target program.
The exec keeps the pid, so the caller's ``$!`` remains the train's pid.

Backlog: 806f0c3cecabcb64.
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: spawn_detached.py <program> [args...]", file=sys.stderr)
        sys.exit(2)

    # Create a new session (and process group).  If the process is already a
    # session leader (e.g. launched from a non-interactive shell that already
    # called setsid), ``setsid()`` raises ``PermissionError`` — the process is
    # already outside its parent's group, so continue.  Any other ``OSError``
    # is a real failure and propagates.
    try:
        os.setsid()
    except PermissionError:
        pass

    # Redirect stdin from /dev/null so the child doesn't inherit the
    # scheduler's stdin.  Leave stdout and stderr as inherited — the caller's
    # ``>> "$log_file" 2>&1`` still captures the train's output.
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    # Replace this process with the target program.  ``exec`` keeps the pid.
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()