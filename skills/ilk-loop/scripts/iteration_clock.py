#!/usr/bin/env python3
"""A shared monotonic clock for deadlines exchanged between POSIX processes.

Measured on this host: Python 3.9 time.monotonic() starts near zero separately
in every process. CLOCK_MONOTONIC is the shared host clock. Unsupported hosts
must provide an adapter rather than silently resetting a transferred budget.
"""
import os
import time


def monotonic():
    if os.name=='nt':return time.monotonic() # shared QPC on supported Python
    if not hasattr(time,'CLOCK_MONOTONIC') or not hasattr(time,'clock_gettime'):
        raise RuntimeError('shared monotonic clock adapter unavailable')
    return time.clock_gettime(time.CLOCK_MONOTONIC)


if __name__=='__main__':print(monotonic())
