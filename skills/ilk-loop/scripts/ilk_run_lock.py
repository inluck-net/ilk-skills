#!/usr/bin/env python3
"""Portable single-instance lock held across exec.

Usage:
    python3 ilk_run_lock.py --lock <lockfile> -- <command> [args...]

Acquires an exclusive flock on <lockfile>, writes holder metadata, then
execvp's the command.  The lock lives on the open file description and
survives exec because FD_CLOEXEC is cleared — the kernel releases it when
the holder dies by any means, including SIGKILL.

Exit codes:
    0   (never reached here — the command is exec'd)
    1   usage error
    3   another process holds the lock (metadata printed to stderr)
"""

import argparse
import fcntl
import json
import os
import sys
import time


def main():
    parser = argparse.ArgumentParser(
        description="Acquire an exclusive lock and exec a command under it."
    )
    parser.add_argument(
        "--lock", required=True, help="Path to the lock file."
    )
    parser.add_argument(
        "command", nargs=argparse.REMAINDER,
        help="Command to exec (after -- separator)."
    )
    args = parser.parse_args()

    if not args.command or args.command[0] != "--":
        print("usage: ilk_run_lock.py --lock <file> -- <cmd> [args...]",
              file=sys.stderr)
        sys.exit(1)

    # Strip the -- separator.
    cmd = args.command[1:]
    if not cmd:
        print("usage: ilk_run_lock.py --lock <file> -- <cmd> [args...]",
              file=sys.stderr)
        sys.exit(1)

    lockfile = args.lock
    os.makedirs(os.path.dirname(lockfile) or ".", exist_ok=True)

    fd = os.open(lockfile, os.O_CREAT | os.O_RDWR)

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Lock held by another process — read its metadata and report.
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            raw = os.read(fd, 65536).decode("utf-8", errors="replace")
            holder = json.loads(raw) if raw.strip() else {}
        except Exception:
            holder = {}

        pid = holder.get("pid", "?")
        started = holder.get("started_at", "?")
        print(
            f"ilk_run_lock: another runner holds this lock "
            f"(pid={pid}, started={started})",
            file=sys.stderr,
        )
        os.close(fd)
        sys.exit(3)

    # Lock acquired — write holder metadata.
    metadata = json.dumps({
        "pid": os.getpid(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, metadata.encode("utf-8"))
    os.fsync(fd)

    # Clear FD_CLOEXEC so the lock survives execvp.
    import struct
    # fcntl(fd, F_GETFD) returns the flags; FD_CLOEXEC is bit 0.
    old_flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    fcntl.fcntl(fd, fcntl.F_SETFD, old_flags & ~fcntl.FD_CLOEXEC)

    # execvp replaces the process — the lock is held by the new process.
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
