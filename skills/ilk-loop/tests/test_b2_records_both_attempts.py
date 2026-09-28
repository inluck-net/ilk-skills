"""B2's confirm re-run is recorded in the results file every reader uses.

Run 20260928-202541 (ilk-skills): sub-plan a-changelog-commit-keeps-the-proof's
step-1 gate failed on its first run (a timing race in
test_doctor::test_growing_file_is_progressing) and passed on B2's confirm
re-run ("B2 transient cleared").  The re-run file was deleted and the
first-pass red stayed in the results file, so ship_integrity -- which reads
``outcome == "pass"`` (ship_integrity.py) -- reverted correct work and parked
the master.  Design D5 (docs/architecture/loop-state-and-ownership-design.md
section 6): a verdict comes from all recorded attempts.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "blocking_checks.py"


def _write(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _confirm(first: Path, rerun: Path) -> dict:
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), str(first), "--confirm", str(rerun)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    return json.loads(out)


def _any_blocking(first: Path) -> bool:
    rc = subprocess.run([sys.executable, str(_SCRIPT), str(first), "--any"]).returncode
    return rc == 0


def test_a_cleared_check_is_rewritten_with_both_attempts(tmp_path: Path) -> None:
    first = _write(tmp_path / "first.jsonl", [
        {"slug": "a", "step": 1, "outcome": "fail", "command": "c1"},
        {"slug": "b", "step": 0, "outcome": "pass", "command": "c2"},
    ])
    rerun = _write(tmp_path / "rerun.jsonl", [
        {"slug": "a", "step": 1, "outcome": "pass", "command": "c1"},
    ])
    result = _confirm(first, rerun)
    assert result["blocked"] is False
    recs = {r["slug"]: r for r in _read(first)}
    assert recs["a"]["outcome"] == "pass", "ship_integrity reads outcome == 'pass'"
    assert recs["a"]["attempts"] == ["fail", "pass"]
    assert recs["a"]["flaky"] is True
    assert recs["b"] == {"slug": "b", "step": 0, "outcome": "pass", "command": "c2"}
    assert not _any_blocking(first), "no reader may still see the cleared red"


def test_red_on_both_attempts_stays_red(tmp_path: Path) -> None:
    first = _write(tmp_path / "first.jsonl", [
        {"slug": "a", "step": 1, "outcome": "fail", "command": "c1"},
    ])
    rerun = _write(tmp_path / "rerun.jsonl", [
        {"slug": "a", "step": 1, "outcome": "error", "command": "c1"},
    ])
    result = _confirm(first, rerun)
    assert result["blocked"] is True
    (rec,) = _read(first)
    assert rec["outcome"] == "fail"
    assert rec["attempts"] == ["fail", "error"]
    assert "flaky" not in rec
    assert _any_blocking(first)


def test_a_check_missing_from_the_rerun_is_confirmed_not_cleared(tmp_path: Path) -> None:
    first = _write(tmp_path / "first.jsonl", [
        {"slug": "a", "step": 1, "outcome": "fail", "command": "c1"},
    ])
    rerun = _write(tmp_path / "rerun.jsonl", [])
    result = _confirm(first, rerun)
    assert result["blocked"] is True, "a measurement that did not happen cannot clear a red"
    (rec,) = _read(first)
    assert rec["outcome"] == "fail"
    assert rec["attempts"] == ["fail", "not-rerun"]


def test_the_runner_consumes_the_helper() -> None:
    runner = (_SCRIPT.parent / "run_ilk_loop_claude.sh").read_text(encoding="utf-8")
    assert '--confirm "$rerun_results"' in runner
    assert "rerun_map = {(r['slug']" not in runner, "the old inline reader must be gone"


def test_red_owner_refuses_an_empty_command(tmp_path: Path) -> None:
    # An empty command passes at every commit; a bisect over it is a verdict
    # nobody measured (the runner passed --cmd "" until 2026-09-28).
    r = subprocess.run(
        [sys.executable, str(_SCRIPT.parent / "red_owner.py"), "--repo", str(tmp_path),
         "--base", "HEAD~1", "--head", "HEAD", "--cmd", ""],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 2
    assert "empty" in r.stderr
