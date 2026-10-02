#!/usr/bin/env python3
"""Spawn a process in its own process group with a custom argv[0].

Usage: python3 _spawn_detached.py <argv0> <command> [args...]

Prints the child PID to stdout and exits.  The child runs in its own
process group (setpgid) so stop.sh's PGID exclusion does not protect it.
"""
import os
import sys

def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <argv0> <command> [args...]", file=sys.stderr)
        sys.exit(1)

    argv0 = sys.argv[1]
    command = sys.argv[2]
    args = [argv0] + sys.argv[3:]

    pid = os.fork()
    if pid == 0:
        # Child: create own process group, redirect stdout, then exec
        os.setpgid(0, 0)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
        os.execvp(command, args)
    else:
        # Parent: print child PID and exit
        print(pid)

if __name__ == "__main__":
    main()