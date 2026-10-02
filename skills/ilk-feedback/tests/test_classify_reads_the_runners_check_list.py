"""collect.classify reads the runner's real local_checks shape: a LIST.

The runner writes each iteration's ``local_checks`` as a list of check dicts.
The sentinel-classification branch passed that raw list to
``_is_broken_gate_result`` (a per-check reader), which crashed with
``'list' object has no attribute 'get'``. collect.py then produced no report,
and the watchdog fell back to the raw sentinel for every
``local_checks_failed`` run (ilk-skills run 20261002-105103). The older tests
hand-wrote ``local_checks`` as a single dict and so could not see it.

HOME and ILK_DATA_HOME are both pinned to tmp_path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
for _p in (_HERE.parent.parent / "scripts", _HERE.parent.parent.parent / "ilk-loop" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import collect  # noqa: E402
from ilk_paths import external_launcher_dir, project_key  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "home").mkdir()
    (tmp_path / "ilk-data").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "ilk-data"))
    repo = tmp_path / "repo"
    repo.mkdir()
    d = external_launcher_dir(project_key(repo))
    d.mkdir(parents=True, exist_ok=True)
    (d / "last-exit.json").write_text(json.dumps(
        {"state": "local_checks_failed", "pid": 0, "run_id": "20261002-105103", "iterations": 2}))
    return repo


def _iters(failing_check: dict) -> list[dict]:
    # The two records of run 20261002-105103, trimmed to the fields classify reads.
    return [
        {"run_id": "20261002-105103", "iteration": 1, "exit_code": 0, "new_commits_total": 2,
         "local_checks": [{"slug": "s", "step": 0, "outcome": "pass", "exit_code": 0, "command": "pytest a.py"}]},
        {"run_id": "20261002-105103", "iteration": 2, "exit_code": 0, "new_commits_total": 2,
         "local_checks": [failing_check]},
    ]


def test_a_list_shaped_red_assertion_classifies_stuck(project):
    label, _ = collect.classify(_iters(
        {"slug": "s", "step": 1, "outcome": "fail", "exit_code": 1, "command": "pytest a.py",
         "stderr_tail": "AssertionError: expected 3"}), None, project)
    assert label == "local-checks-stuck"


def test_a_list_shaped_unrunnable_gate_classifies_broken(project):
    label, _ = collect.classify(_iters(
        {"slug": "s", "step": 1, "outcome": "fail", "exit_code": 127, "command": "bunx x",
         "stderr_tail": "bunx: command not found"}), None, project)
    assert label == "local-checks-broken"


def test_check_items_accepts_both_shapes():
    c = {"outcome": "fail"}
    assert collect._check_items({"local_checks": c}) == [c]
    assert collect._check_items({"local_checks": [c, "junk"]}) == [c]
    assert collect._check_items({}) == []
