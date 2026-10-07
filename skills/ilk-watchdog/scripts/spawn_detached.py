"""Spawn a child process in its own session and exec into it.

The scheduler's release train must not share the scheduler job's process group.
When the train's own local bounce runs ``launchctl bootout`` on the scheduler,
launchd kills every process in the job's process group (see ``launchd.plist(5)``,
key ``AbandonProcessGroup``).  A train in that group dies with the job.

This helper calls ``os.setsid()`` to place the child in a new session and
process group, then ``os.execvp()`` to replace itself with the target program.
The exec keeps the pid, so the caller's ``$!`` remains the train's pid.

With ``--log PATH``, stdout and stderr are redirected to the log file.
Without ``--log``, both are redirected to /dev/null.  A detached child never
keeps its caller's stdout.  stdin is always redirected from /dev/null.

All file descriptors >= 3 are closed before exec so no caller lock fd survives.

Backlog: 806f0c3cecabcb64.
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    args = sys.argv[1:]

    # Parse --log PATH before positional args.
    log_path: str | None = None
    if len(args) >= 2 and args[0] == "--log":
        log_path = args[1]
        args = args[2:]

    if not args:
        print("Usage: spawn_detached.py [--log PATH] <program> [args...]",
              file=sys.stderr)
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
    # scheduler's stdin.
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    # Redirect stdout and stderr.  With --log, append to the log file;
    # without --log, send to /dev/null.  A detached child never keeps its
    # caller's stdout.
    if log_path is not None:
        log_fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                         0o644)
    else:
        log_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)

    # Close all file descriptors >= 3 so no caller lock fd survives.
    try:
        import resource
        max_fd = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except (ImportError, OSError):
        max_fd = 256
    os.closerange(3, max_fd)

    # Replace this process with the target program.  ``exec`` keeps the pid.
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()