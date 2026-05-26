"""Tests for _normalize_path_for_compare and read_jsonl_iters cross-platform matching.

Run with: python3 test_collect_paths.py
Stdlib only, no pytest dependency.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Ensure we import the sibling collect module under test.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402


# ── tiny test harness ────────────────────────────────────────────────────────

_failures: list[str] = []


def _check(label: str, cond: bool, detail: str = "") -> None:
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _failures.append(label + (f": {detail}" if detail else ""))


# ── unit tests for _normalize_path_for_compare ───────────────────────────────

def test_posix_self_equality() -> None:
    print("test_posix_self_equality:")
    a = collect._normalize_path_for_compare("/Users/x/proj")
    b = collect._normalize_path_for_compare("/Users/x/proj")
    _check("POSIX paths equal", a == b, f"{a!r} vs {b!r}")


def test_windows_self_equality() -> None:
    print("test_windows_self_equality:")
    a = collect._normalize_path_for_compare("C:\\Users\\x\\proj")
    b = collect._normalize_path_for_compare("C:\\Users\\x\\proj")
    _check("Windows paths equal", a == b, f"{a!r} vs {b!r}")


def test_windows_slash_and_case_insensitive() -> None:
    print("test_windows_slash_and_case_insensitive:")
    a = collect._normalize_path_for_compare("C:\\Users\\x\\proj")
    b = collect._normalize_path_for_compare("c:/users/x/proj")
    _check("backslash vs forward + case", a == b, f"{a!r} vs {b!r}")


def test_distinct_paths_do_not_collide() -> None:
    print("test_distinct_paths_do_not_collide:")
    a = collect._normalize_path_for_compare("/Users/x/proj")
    b = collect._normalize_path_for_compare("/Users/x/other")
    _check("different paths not equal", a != b, f"{a!r} vs {b!r}")


# ── round-trip test against read_jsonl_iters ─────────────────────────────────

def test_read_jsonl_iters_posix_query() -> None:
    """POSIX query matches only the POSIX record."""
    print("test_read_jsonl_iters_posix_query:")
    posix_proj = "/Users/x/proj"
    win_proj = "C:\\Users\\x\\proj"
    records = [
        {"project": posix_proj, "run_id": "r1", "iteration": 1, "exit_code": 0},
        {"project": win_proj, "run_id": "r2", "iteration": 1, "exit_code": 0},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
        tmp_path = Path(f.name)

    # Monkeypatch JSONL_LOG to point at our temp file.
    orig = collect.JSONL_LOG
    collect.JSONL_LOG = tmp_path
    try:
        result = collect.read_jsonl_iters(Path(posix_proj))
        _check("POSIX query returns 1 record", len(result) == 1, f"got {len(result)}")
        if result:
            _check("matched record is the POSIX one", result[0]["project"] == posix_proj,
                   f"got {result[0]['project']!r}")
    finally:
        collect.JSONL_LOG = orig
        tmp_path.unlink(missing_ok=True)


def test_read_jsonl_iters_windows_query() -> None:
    """Windows query matches only the Windows record."""
    print("test_read_jsonl_iters_windows_query:")
    posix_proj = "/Users/x/proj"
    win_proj = "C:\\Users\\x\\proj"
    records = [
        {"project": posix_proj, "run_id": "r1", "iteration": 1, "exit_code": 0},
        {"project": win_proj, "run_id": "r2", "iteration": 1, "exit_code": 0},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
        tmp_path = Path(f.name)

    orig = collect.JSONL_LOG
    collect.JSONL_LOG = tmp_path
    try:
        result = collect.read_jsonl_iters(Path(win_proj))
        _check("Windows query returns 1 record", len(result) == 1, f"got {len(result)}")
        if result:
            _check("matched record is the Windows one", result[0]["project"] == win_proj,
                   f"got {result[0]['project']!r}")
    finally:
        collect.JSONL_LOG = orig
        tmp_path.unlink(missing_ok=True)


# ── runner ───────────────────────────────────────────────────────────────────

def main() -> int:
    tests = [
        test_posix_self_equality,
        test_windows_self_equality,
        test_windows_slash_and_case_insensitive,
        test_distinct_paths_do_not_collide,
        test_read_jsonl_iters_posix_query,
        test_read_jsonl_iters_windows_query,
    ]
    for t in tests:
        t()
        print()

    if _failures:
        print(f"FAILED: {len(_failures)} check(s)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("All collect_paths tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
