"""Tests for _normalize_path_for_compare, read_jsonl_iters, and external log discovery.

Run with: python3 test_collect_paths.py
Stdlib only, no pytest dependency.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

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

    # Monkeypatch _jsonl_log_candidates to return our temp file.
    def fake_candidates(_proj, _ll=None):
        return [tmp_path]

    with patch.object(collect, "_jsonl_log_candidates", fake_candidates):
        result = collect.read_jsonl_iters(Path(posix_proj))
        _check("POSIX query returns 1 record", len(result) == 1, f"got {len(result)}")
        if result:
            _check("matched record is the POSIX one", result[0]["project"] == posix_proj,
                   f"got {result[0]['project']!r}")
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

    def fake_candidates(_proj, _ll=None):
        return [tmp_path]

    with patch.object(collect, "_jsonl_log_candidates", fake_candidates):
        result = collect.read_jsonl_iters(Path(win_proj))
        _check("Windows query returns 1 record", len(result) == 1, f"got {len(result)}")
        if result:
            _check("matched record is the Windows one", result[0]["project"] == win_proj,
                   f"got {result[0]['project']!r}")
    tmp_path.unlink(missing_ok=True)


# ── external log discovery tests ─────────────────────────────────────────────

def test_jsonl_log_candidates_external_dir() -> None:
    """_jsonl_log_candidates includes external_logs_dir when ilk_paths available."""
    print("test_jsonl_log_candidates_external_dir:")
    with tempfile.TemporaryDirectory() as td:
        ext_dir = Path(td) / "logs"
        ext_dir.mkdir()
        fake_key = "test-key"

        with patch.object(collect, "external_logs_dir", lambda k: ext_dir / k):
            with patch.object(collect, "project_key", lambda _p: fake_key):
                candidates = collect._jsonl_log_candidates(Path("/fake/proj"))
                expected = ext_dir / fake_key / ".ilk-loop.log"
                _check("external dir candidate present",
                       expected in candidates,
                       f"candidates: {[str(c) for c in candidates]}")


def test_jsonl_log_candidates_last_launch_hint() -> None:
    """_jsonl_log_candidates prioritises last-launch.json log_file."""
    print("test_jsonl_log_candidates_last_launch_hint:")
    with tempfile.TemporaryDirectory() as td:
        hint_file = Path(td) / "custom" / ".ilk-loop.log"
        hint_dir = Path(td) / "custom-dir"
        last_launch = {
            "log_file": str(hint_file),
            "log_dir": str(hint_dir),
        }
        candidates = collect._jsonl_log_candidates(Path("/fake/proj"), last_launch)
        _check("log_file candidate first",
               candidates[0] == hint_file,
               f"first: {candidates[0]}")
        _check("log_dir candidate second",
               candidates[1] == hint_dir / ".ilk-loop.log",
               f"second: {candidates[1]}")


def test_iter_log_root_candidates_order() -> None:
    """_iter_log_root_candidates returns last-launch, external, legacy."""
    print("test_iter_log_root_candidates_order:")
    with tempfile.TemporaryDirectory() as td:
        ext_dir = Path(td) / "ext-logs"
        ext_dir.mkdir()
        hint_dir = Path(td) / "hint-dir"
        last_launch = {"log_dir": str(hint_dir)}
        fake_key = "test-key"

        with patch.object(collect, "external_logs_dir", lambda k: ext_dir / k):
            with patch.object(collect, "project_key", lambda _p: fake_key):
                candidates = collect._iter_log_root_candidates(Path("/fake/proj"), last_launch)
                _check("first is hint dir",
                       candidates[0] == hint_dir,
                       f"got {candidates[0]}")
                _check("second is external dir",
                       candidates[1] == ext_dir / fake_key,
                       f"got {candidates[1]}")
                _check("third is legacy",
                       candidates[2] == collect.LOOP_LOG_DIR,
                       f"got {candidates[2]}")


def test_resolve_iter_log_external_root() -> None:
    """resolve_iter_log finds iter logs under external log root."""
    print("test_resolve_iter_log_external_root:")
    with tempfile.TemporaryDirectory() as td:
        ext_dir = Path(td) / "logs"
        iter_dir = ext_dir / "ilk-claude-20260101-120000"
        iter_dir.mkdir(parents=True)
        iter_log = iter_dir / "iter-03.log"
        iter_log.write_text("test log content")

        fake_key = "test-key"

        with patch.object(collect, "external_logs_dir", lambda k: ext_dir):
            with patch.object(collect, "project_key", lambda _p: fake_key):
                result = collect.resolve_iter_log(
                    "20260101-120000", 3, Path("/fake/proj")
                )
                _check("found iter log in external root",
                       result == iter_log,
                       f"got {result}")


def test_read_jsonl_iters_deduplication() -> None:
    """read_jsonl_iters de-duplicates by (run_id, iteration)."""
    print("test_read_jsonl_iters_deduplication:")
    proj = "/Users/x/proj"
    records = [
        {"project": proj, "run_id": "r1", "iteration": 1, "exit_code": 0, "source": "file1"},
        {"project": proj, "run_id": "r1", "iteration": 1, "exit_code": 0, "source": "file2"},
        {"project": proj, "run_id": "r1", "iteration": 2, "exit_code": 0, "source": "file1"},
    ]
    # Write to two separate files
    files = []
    for i in range(2):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(json.dumps(records[i]) + "\n")
            files.append(Path(f.name))
    # Third record in second file
    with files[1].open("a") as f:
        f.write(json.dumps(records[2]) + "\n")

    def fake_candidates(_proj, _ll=None):
        return files

    with patch.object(collect, "_jsonl_log_candidates", fake_candidates):
        result = collect.read_jsonl_iters(Path(proj))
        _check("dedup returns 2 records", len(result) == 2, f"got {len(result)}")
        if len(result) == 2:
            _check("first record from file1", result[0].get("source") == "file1")
            _check("second is iter 2", result[1].get("iteration") == 2)

    for f in files:
        f.unlink(missing_ok=True)


def test_read_jsonl_iters_empty_when_no_files() -> None:
    """read_jsonl_iters returns [] when no candidate files exist."""
    print("test_read_jsonl_iters_empty_when_no_files:")
    def fake_candidates(_proj, _ll=None):
        return [Path("/nonexistent/file.log")]

    with patch.object(collect, "_jsonl_log_candidates", fake_candidates):
        result = collect.read_jsonl_iters(Path("/fake/proj"))
        _check("returns empty list", result == [], f"got {result}")


# ── runner ───────────────────────────────────────────────────────────────────

def main() -> int:
    tests = [
        test_posix_self_equality,
        test_windows_self_equality,
        test_windows_slash_and_case_insensitive,
        test_distinct_paths_do_not_collide,
        test_read_jsonl_iters_posix_query,
        test_read_jsonl_iters_windows_query,
        test_jsonl_log_candidates_external_dir,
        test_jsonl_log_candidates_last_launch_hint,
        test_iter_log_root_candidates_order,
        test_resolve_iter_log_external_root,
        test_read_jsonl_iters_deduplication,
        test_read_jsonl_iters_empty_when_no_files,
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
