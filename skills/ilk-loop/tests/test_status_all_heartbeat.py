"""Red-first: status_all --json must carry per-project run-dir liveness.

Sub-plan `the-panel-shows-a-heartbeat` (SP4 of
MASTER-2026-08-27-a-harness-reads-only-its-own-sandbox).

Why this exists: every layer of the panel stack bottoms out at the git
commit — the sub-plan's `current_step` is bumped *in a commit*, `status_all`
reports that field, and `status_progress` derives pace from commit
timestamps.  Inside a step the panel is blind by construction.  Measured
2026-08-27: the row sat on `... 0/6` for 12 minutes while the loop was
healthy (54 tool calls, log written 2 seconds earlier).

The cheap signal that fixes it without asking the worker for anything: the
current iteration log's **mtime** is a liveness heartbeat.

Covers:
  AC-1 — an alive project's entry carries run_id (str), iteration (int,
         1-based), iteration_elapsed_s (int), heartbeat_s (int).
  AC-2 — a dead project's entry carries all four as null.  A stale
         heartbeat from a dead run is worse than none: it reads as liveness.
  AC-3 — the computation never opens a `*.jsonl` file.  Those are the
         transcript files: measured 981198 / 844913 / 634238 bytes for three
         iterations of one run.  19 projects x a 10-second refresh makes
         reading them a real I/O load for a cosmetic feature.

All fixtures are synthetic run dirs under `tmp_path` — this suite never
depends on a live loop (the same hermeticity discipline SP1 enforced for the
scheduler harnesses).
"""
from __future__ import annotations

import builtins
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

# Repo root — tests/ → ilk-loop/ → skills/ → root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "ilk-loop" / "scripts"))

import status_all  # noqa: E402

RUN_ID = "20260827-130050"

# The four fields this sub-plan adds, as one list so a partial
# implementation cannot pass by shipping three of them.
HEARTBEAT_FIELDS = ("run_id", "iteration", "iteration_elapsed_s", "heartbeat_s")


# ── fixture builders ────────────────────────────────────────────────

def _make_project(
    root: Path,
    key: str = "proj",
    *,
    state: str = "running",
    iterations: int = 3,
    run_id: str = RUN_ID,
    heartbeat_age_s: int = 4,
    with_jsonl: bool = True,
) -> Path:
    """Build a synthetic project data dir and return it.

    Mirrors the real layout observed at
    ``~/.ilk-data/projects/<key>/`` — plans/, runtime/launcher/last-exit.json,
    logs/runs/<run_id>/iter-NN.log (+ the large iter-NN.log.jsonl transcript
    beside it, which AC-3 forbids opening).
    """
    proj = root / "projects" / key
    (proj / "plans").mkdir(parents=True, exist_ok=True)

    launcher = proj / "runtime" / "launcher"
    launcher.mkdir(parents=True, exist_ok=True)
    (launcher / "last-exit.json").write_text(
        json.dumps({"pid": os.getpid(), "state": state}), encoding="utf-8"
    )

    run_dir = proj / "logs" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for n in range(1, iterations + 1):
        log = run_dir / f"iter-{n:02d}.log"
        log.write_text(f"iteration {n}\n", encoding="utf-8")
        if with_jsonl:
            # Deliberately large-ish and clearly identifiable: if the
            # implementation reads this, AC-3's recorder catches it.
            (run_dir / f"iter-{n:02d}.log.jsonl").write_text(
                '{"type":"assistant"}\n' * 200, encoding="utf-8"
            )
        # Newest iteration carries the heartbeat age; older ones are older.
        age = heartbeat_age_s if n == iterations else heartbeat_age_s + 600 * (iterations - n)
        os.utime(log, (now - age, now - age))
    return proj


def _resolve(root: Path, proj: Path, monkeypatch, *, alive: bool) -> dict:
    """Resolve one project's status with the data root pinned to ``root``."""
    monkeypatch.setenv("ILK_DATA_HOME", str(root))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)
    # `alive` is decided by the sentinel state x PID check; pin the PID side
    # so the test does not depend on a real ilk process existing.
    monkeypatch.setattr(status_all, "ilk_pid_alive", lambda pid: alive)
    return status_all.resolve_project_status(proj)


# ── AC-1 ────────────────────────────────────────────────────────────

def test_alive_entry_carries_all_four_fields(tmp_path, monkeypatch):
    """AC-1: an alive project's entry carries all four liveness fields."""
    proj = _make_project(tmp_path, iterations=3, heartbeat_age_s=4)
    entry = _resolve(tmp_path, proj, monkeypatch, alive=True)

    assert entry["sentinel"]["alive"] is True, "fixture must be alive"
    missing = [f for f in HEARTBEAT_FIELDS if f not in entry]
    assert not missing, f"entry is missing liveness fields: {missing}"


def test_alive_entry_field_types_and_values(tmp_path, monkeypatch):
    """AC-1: run_id is the run dir name; iteration is 1-based; ages are ints."""
    proj = _make_project(tmp_path, iterations=3, heartbeat_age_s=4)
    entry = _resolve(tmp_path, proj, monkeypatch, alive=True)

    assert entry["run_id"] == RUN_ID
    assert isinstance(entry["iteration"], int)
    assert entry["iteration"] == 3, "iteration is the count of iter-*.log files"
    assert isinstance(entry["heartbeat_s"], int)
    # The newest log was stamped 4s ago; allow slack for slow hosts but the
    # value must clearly track *that* log, not the older ones (600s+ apart).
    assert 0 <= entry["heartbeat_s"] < 60, entry["heartbeat_s"]
    assert isinstance(entry["iteration_elapsed_s"], int)
    assert entry["iteration_elapsed_s"] >= 0


def test_heartbeat_tracks_the_newest_iteration_log(tmp_path, monkeypatch):
    """AC-1: an older iteration's mtime must not be reported as the heartbeat."""
    proj = _make_project(tmp_path, iterations=4, heartbeat_age_s=2)
    entry = _resolve(tmp_path, proj, monkeypatch, alive=True)
    assert entry["iteration"] == 4
    assert entry["heartbeat_s"] < 60, (
        "heartbeat_s must come from iter-04.log (2s old), not an older log "
        f"(>=600s old); got {entry['heartbeat_s']}"
    )


def test_newest_run_dir_wins(tmp_path, monkeypatch):
    """AC-1: with several run dirs, the newest run_id is reported."""
    _make_project(tmp_path, iterations=2, run_id="20260826-113822")
    proj = _make_project(tmp_path, iterations=1, run_id="20260827-130050")
    entry = _resolve(tmp_path, proj, monkeypatch, alive=True)
    assert entry["run_id"] == "20260827-130050"
    assert entry["iteration"] == 1


def test_jsonl_transcripts_are_not_counted_as_iterations(tmp_path, monkeypatch):
    """AC-1: `iter-NN.log.jsonl` files must not inflate the iteration count."""
    proj = _make_project(tmp_path, iterations=2, with_jsonl=True)
    entry = _resolve(tmp_path, proj, monkeypatch, alive=True)
    assert entry["iteration"] == 2, (
        "2 iter-*.log files + 2 iter-*.log.jsonl transcripts must read as "
        f"iteration 2, not 4; got {entry['iteration']}"
    )


# ── AC-2 ────────────────────────────────────────────────────────────

def test_dead_project_reports_nulls(tmp_path, monkeypatch):
    """AC-2: a dead run's stale heartbeat must be suppressed, not reported."""
    proj = _make_project(tmp_path, state="running", iterations=3)
    entry = _resolve(tmp_path, proj, monkeypatch, alive=False)

    assert entry["sentinel"]["alive"] is False, "fixture must be dead"
    for f in HEARTBEAT_FIELDS:
        assert f in entry, f"field {f} must be present (as null) even when dead"
        assert entry[f] is None, (
            f"{f} must be None for a dead project — a stale heartbeat reads "
            f"as liveness; got {entry[f]!r}"
        )


def test_terminal_sentinel_reports_nulls(tmp_path, monkeypatch):
    """AC-2: a terminal sentinel state is dead regardless of PID liveness."""
    proj = _make_project(tmp_path, state="shipped", iterations=3)
    # PID pinned alive: the *state* must be what suppresses the fields.
    entry = _resolve(tmp_path, proj, monkeypatch, alive=True)
    assert entry["sentinel"]["alive"] is False
    assert all(entry[f] is None for f in HEARTBEAT_FIELDS)


def test_alive_but_empty_run_dir_reports_nulls(tmp_path, monkeypatch):
    """A run dir with no iteration log yet yields nulls rather than raising.

    `status_all` feeds a menu-bar plugin refreshing every 10s; an exception
    here blanks the panel for every project, not just the broken one.
    """
    # iterations=0 creates logs/runs/<run_id>/ but with no iter-*.log inside.
    proj = _make_project(tmp_path, iterations=0)
    entry = _resolve(tmp_path, proj, monkeypatch, alive=True)
    assert entry["sentinel"]["alive"] is True
    assert all(entry[f] is None for f in HEARTBEAT_FIELDS), (
        "a run dir with no iter-*.log has no heartbeat to report"
    )


def test_missing_runs_tree_reports_nulls(tmp_path, monkeypatch):
    """No logs/runs/ at all → nulls, no exception."""
    proj = tmp_path / "projects" / "bare"
    (proj / "plans").mkdir(parents=True)
    launcher = proj / "runtime" / "launcher"
    launcher.mkdir(parents=True)
    (launcher / "last-exit.json").write_text(
        json.dumps({"pid": os.getpid(), "state": "running"}), encoding="utf-8"
    )
    entry = _resolve(tmp_path, proj, monkeypatch, alive=True)
    assert all(entry[f] is None for f in HEARTBEAT_FIELDS)


# ── AC-3 ────────────────────────────────────────────────────────────

class _OpenRecorder:
    """Record every path passed to any open() flavour, then forward it.

    Three flavours are patched because the code under test reaches the
    filesystem three ways: `builtins.open` (plain), `io.open` (what
    `pathlib` calls), and `Path.open` / `Path.read_text`.
    """

    def __init__(self):
        self.paths: list[str] = []

    def install(self, monkeypatch):
        real_builtin = builtins.open
        real_io = io.open
        real_path_open = Path.open
        rec = self.paths

        def wrapped_builtin(file, *a, **kw):
            rec.append(str(file))
            return real_builtin(file, *a, **kw)

        def wrapped_io(file, *a, **kw):
            rec.append(str(file))
            return real_io(file, *a, **kw)

        def wrapped_path_open(self_p, *a, **kw):
            rec.append(str(self_p))
            return real_path_open(self_p, *a, **kw)

        monkeypatch.setattr(builtins, "open", wrapped_builtin)
        monkeypatch.setattr(io, "open", wrapped_io)
        monkeypatch.setattr(Path, "open", wrapped_path_open)
        return self

    @property
    def jsonl_paths(self) -> list[str]:
        return [p for p in self.paths if p.endswith(".jsonl")]


def test_never_opens_a_jsonl_transcript(tmp_path, monkeypatch):
    """AC-3 (cost bound): no `*.jsonl` file is opened while resolving status."""
    proj = _make_project(tmp_path, iterations=3, with_jsonl=True)
    rec = _OpenRecorder().install(monkeypatch)
    entry = _resolve(tmp_path, proj, monkeypatch, alive=True)

    # Positive control: the fields were actually computed, so this is not a
    # vacuous pass from an unimplemented code path.
    assert entry["heartbeat_s"] is not None
    assert rec.jsonl_paths == [], (
        f"opened {len(rec.jsonl_paths)} transcript file(s): {rec.jsonl_paths}"
    )
    # Positive control on the recorder itself: it must have seen *something*
    # (last-exit.json at minimum), else the patch silently missed every call
    # and the negative above is meaningless.
    assert rec.paths, "open-recorder captured 0 calls — the patch did not take"


def test_liveness_helper_opens_nothing_at_all(tmp_path, monkeypatch):
    """AC-3: the liveness computation itself is stat+listdir only, no reads."""
    proj = _make_project(tmp_path, iterations=3, with_jsonl=True)
    logs_dir = proj / "logs"
    rec = _OpenRecorder().install(monkeypatch)
    fields = status_all._run_dir_liveness(logs_dir)

    assert fields["heartbeat_s"] is not None, "fixture must produce a heartbeat"
    assert rec.paths == [], (
        f"the liveness helper opened {len(rec.paths)} file(s): {rec.paths}"
    )
