"""A killed iteration is classifiable.

Sub-plan `2026-08-29c-an-iteration-is-recorded-before-it-runs`, classifier half.

Once the runner writes a `status: started` record before the work begins
(companion file `skills/ilk-loop/tests/test_iteration_record_precedes_work.py`),
a run killed part-way leaves records that are *real but incomplete*. The
classifier has to accept them.

Why this matters beyond tidiness: `scheduler.sh:510`'s
`read_blacklist_from_postmortems` builds its blacklist **from postmortem files
on disk**. No postmortem means no blacklist entry means the project stays
dispatchable forever. That is how three relaunches ran on rezmac on 2026-08-29
with nothing declining to dispatch. A classifier that raises, or that silently
produces no report, switches the bound off exactly when it is needed.

The start-only shape must NOT collapse into "never ran": a run that started and
was killed calls for a different action from one that never invoked the model
at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"

_KEY_PUNCT = re.compile(r"[^a-z0-9]+")


def _project_key(project_path: Path) -> str:
    """Mirror ilk_paths.py's key derivation, truncation included."""
    abs_str = str(project_path.resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


@pytest.fixture()
def scratch_env(tmp_path: Path):
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "my-proj"
    project_path.mkdir()
    env = {
        **os.environ,
        "ILK_DATA_HOME": str(data_home),
        "PYTHONIOENCODING": "utf-8",
    }
    return project_path, env, _project_key(project_path), data_home


def _launcher_dir(data_home: Path, key: str) -> Path:
    return data_home / "projects" / key / "runtime" / "launcher"


def _write_sentinel(data_home: Path, key: str, run_id: str, state: str) -> None:
    d = _launcher_dir(data_home, key)
    d.mkdir(parents=True, exist_ok=True)
    (d / "last-exit.json").write_text(
        json.dumps({"state": state, "run_id": run_id, "iterations": 1}),
        encoding="utf-8",
    )


def _write_jsonl(data_home: Path, key: str, project_path: Path, records: list[dict]) -> None:
    logs = data_home / "projects" / key / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / ".ilk-loop.log").open("a", encoding="utf-8") as f:
        for rec in records:
            rec["project"] = str(project_path)
            f.write(json.dumps(rec) + "\n")


def _run_collect(project_path: Path, env: dict, run_id: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_COLLECT_PY), "-ProjectPath", str(project_path),
         "--run-id", run_id, "--quiet"],
        capture_output=True, text=True, env=env, encoding="utf-8",
        errors="replace", timeout=120,
    )


def _started_record(run_id: str) -> dict:
    """Exactly the shape the runner writes before invoking the agent."""
    return {
        "run_id": run_id,
        "cli": "claude",
        "iteration": 1,
        "timestamp": "2026-08-29T12:01:00+0800",
        "model": "test-model",
        "status": "started",
    }


# ---------------------------------------------------------------------------
# AC-4
# ---------------------------------------------------------------------------

def test_a_start_only_run_is_classified_timeout_bound(scratch_env) -> None:
    """AC-4: start record + timeout sentinel → a real taxonomy label.

    `timeout-bound` is already in `CLASSIFICATION_LABELS` and already routes to
    `relaunch` in `watchdog.sh:329`. The point is that the classifier reaches a
    label at all: at HEAD an incomplete run produces no report, which is what
    made the watchdog fall back to the raw sentinel state.
    """
    project_path, env, key, data_home = scratch_env
    run_id = "20260829-120100"

    _write_sentinel(data_home, key, run_id, "timeout")
    _write_jsonl(data_home, key, project_path, [_started_record(run_id)])

    result = _run_collect(project_path, env, run_id)
    assert result.returncode == 0, (
        f"collect.py exited {result.returncode} on a start-only run.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    pm = _launcher_dir(data_home, key) / "postmortems" / f"{run_id}.md"
    assert pm.exists(), (
        f"no postmortem written for a start-only run (looked at {pm})"
    )
    text = pm.read_text(encoding="utf-8")
    assert "timeout-bound" in text, (
        "a start-only run with a timeout sentinel was not classified "
        f"timeout-bound.\nHead:\n{text[:600]}"
    )


# ---------------------------------------------------------------------------
# AC-5
# ---------------------------------------------------------------------------

def test_a_start_only_run_produces_the_artifact_the_scheduler_reads(
    scratch_env,
) -> None:
    """AC-5: the postmortem FILE is the thing that bounds dispatch.

    `read_blacklist_from_postmortems` (`scheduler.sh:510`) reads this directory
    and nothing else. Its absence — not a wrong label in it — is why three
    relaunches were dispatched on 2026-08-29. Asserted as a file on disk, in
    the directory that reader globs.
    """
    project_path, env, key, data_home = scratch_env
    run_id = "20260829-123700"

    _write_sentinel(data_home, key, run_id, "timeout")
    _write_jsonl(data_home, key, project_path, [_started_record(run_id)])

    result = _run_collect(project_path, env, run_id)
    assert result.returncode == 0, f"collect.py failed: {result.stderr}"

    pm_dir = _launcher_dir(data_home, key) / "postmortems"
    assert pm_dir.is_dir(), (
        f"the postmortems directory was never created at {pm_dir}; "
        "scheduler.sh:510 has nothing to read and the project stays "
        "dispatchable forever"
    )
    found = sorted(p.name for p in pm_dir.glob("*.md"))
    assert found, f"postmortems dir exists but is empty: {pm_dir}"

    # stdout is collect.py's return channel: it must be a usable path.
    reported = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert reported and Path(reported).is_file(), (
        "collect.py did not print a valid report path on stdout; "
        "invoke_postmortem_collect treats that as failure. "
        f"stdout={result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# AC-6
# ---------------------------------------------------------------------------

def test_a_run_with_no_records_still_degrades_rather_than_raising(
    scratch_env,
) -> None:
    """AC-6: regression guard — the new path must not become a new crash.

    A genuinely empty run is a different thing from a started-then-killed one,
    and the two must not collapse into a single label: they call for different
    actions (`never-ran` points at an environment fault, a killed run at the
    work itself).
    """
    project_path, env, key, data_home = scratch_env
    run_id = "20260829-131200"

    _write_sentinel(data_home, key, run_id, "timeout")
    # Deliberately no JSONL records at all.

    result = _run_collect(project_path, env, run_id)
    assert "Traceback" not in result.stderr, (
        f"collect.py raised on an empty run instead of degrading:\n{result.stderr}"
    )
    assert result.returncode == 0, (
        f"collect.py exited {result.returncode} on an empty run; it must "
        f"degrade to a no-evidence label.\nstderr: {result.stderr}"
    )

    pm = _launcher_dir(data_home, key) / "postmortems" / f"{run_id}.md"
    assert pm.exists(), f"no postmortem for an empty run (looked at {pm})"
    text = pm.read_text(encoding="utf-8")
    assert ("no-evidence" in text or "never-ran" in text), (
        f"an empty run was not labelled no-evidence/never-ran.\nHead:\n{text[:600]}"
    )
    assert "timeout-bound" not in text, (
        "an empty run was labelled timeout-bound — 'never ran' and 'started "
        "then killed' have collapsed into one label, and they need different "
        f"actions.\nHead:\n{text[:600]}"
    )
