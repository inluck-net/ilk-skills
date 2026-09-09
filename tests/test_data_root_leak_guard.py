"""The data-root leak guard must be seen to FIRE, not merely to stay quiet.

A guard that has only ever been observed silent is indistinguishable from a
guard that cannot fire — the same shape as ``test_ship_proof_ledger_writer``'s
AC-5, which asserted an invariant production could not reach and passed for
weeks (see the v0.9.94 tag body).  So these tests drive it positively.

Two layers, both needed:

* **Unit** — the threshold itself: files present, or a key that is not
  pytest-tmp-derived, fails; an empty tmp-derived directory is reported only;
  ``ILK_DATA_LEAK_STRICT`` escalates.
* **Wiring** — a real subprocess ``pytest`` whose ambient root is a temp dir,
  running a test that writes into it.  A unit test of the predicate alone
  would not catch a snapshot that was never taken or a hook never called,
  which is the failure mode that made the ledger writer's AC-5 vacuous.

Sub-plan: unassigned — landed directly, 2026-09-09.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CONFTEST = _REPO / "conftest.py"


def _load_conftest():
    """Load the root conftest as a module, without importing it as `conftest`."""
    spec = importlib.util.spec_from_file_location("ilk_root_conftest", _CONFTEST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ilk_root_conftest"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeSession:
    def __init__(self) -> None:
        self.exitstatus = 0


def _run_guard(monkeypatch, tmp_path: Path, *, entries, prefix="private-var-tmp",
               strict=False, baseline=None, session_start=0.0) -> _FakeSession:
    """Drive the guard against a fabricated root.

    *entries* maps name -> file count (what the root holds NOW).
    *baseline* is the pre-session snapshot, name -> (file count, newest mtime);
    absent means the root started empty, so every entry reads as new.
    """
    mod = _load_conftest()
    projects = tmp_path / "projects"
    projects.mkdir(parents=True)
    for name, n_files in entries.items():
        d = projects / name / "runtime"
        d.mkdir(parents=True)
        for i in range(n_files):
            (d / f"f{i}.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(mod, "_data_projects_dir", projects)
    monkeypatch.setattr(mod, "_data_snapshot", dict(baseline or {}))
    monkeypatch.setattr(mod, "_data_session_start", session_start)
    monkeypatch.setattr(mod, "_data_tmp_prefix", prefix)
    monkeypatch.setattr(mod, "_data_probe_error", None)
    monkeypatch.setenv("ILK_DATA_LEAK_STRICT", "1" if strict else "")

    session = _FakeSession()
    mod._enforce_no_data_root_leak(session)
    return session


# ── threshold ────────────────────────────────────────────────────────────────

def test_entry_with_files_fails(monkeypatch, tmp_path, capsys):
    """A directory carrying files is the case that can reach a publisher.

    gh-resolve's reap proves a sub-plan on the PRESENCE of a ship-proof row and
    builds its proven set from the whole ledger unfiltered by run_id, so a row
    a test wrote into a real project's ledger is accepted as proof.
    """
    s = _run_guard(monkeypatch, tmp_path,
                   entries={"private-var-tmp-pytest-of-x-abc123": 1})
    assert s.exitstatus == 1, (
        "an entry holding files must fail the session; it is the only shape "
        "that can put a row where a publisher will read it"
    )
    err = capsys.readouterr().err
    assert "private-var-tmp-pytest-of-x-abc123" in err, (
        "the report must NAME the directory — 'a leak occurred' is not "
        "actionable at session end, where the test that did it is long gone"
    )


def test_real_shaped_key_fails_even_when_empty(monkeypatch, tmp_path):
    """A non-tmp-derived key means a test resolved a REAL project."""
    s = _run_guard(monkeypatch, tmp_path,
                   entries={"users-chad-projects-github-inluck-net-ilk-skills": 0})
    assert s.exitstatus == 1, (
        "an empty directory under a REAL project key still means a test "
        "resolved a real project root, which is the precondition for writing "
        "into evidence — emptiness here is timing, not safety"
    )


def test_empty_tmp_derived_entry_is_reported_but_does_not_fail(
    monkeypatch, tmp_path, capsys,
):
    """The documented threshold, and the judgment call behind it.

    24 such directories accumulated over two weeks holding 0 files between
    them (measured 2026-09-09).  Failing on them paints the suite permanently
    red for a condition that has never carried data, and a guard that is
    always red is a guard nobody reads.
    """
    s = _run_guard(monkeypatch, tmp_path,
                   entries={"private-var-tmp-pytest-of-x-def456": 0})
    assert s.exitstatus == 0, (
        "an empty tmp-derived directory must NOT fail the session — that is "
        "the steady state, and a permanently red guard is an ignored one"
    )
    err = capsys.readouterr().err
    assert "DATA-ROOT LEAK" in err and "reported only" in err, (
        "not failing is not the same as staying silent; the entry must still "
        "be reported or the leak stays invisible"
    )


def test_strict_env_fails_on_an_empty_tmp_entry(monkeypatch, tmp_path):
    """ILK_DATA_LEAK_STRICT is the setting for once isolation lands."""
    s = _run_guard(monkeypatch, tmp_path,
                   entries={"private-var-tmp-pytest-of-x-ghi789": 0}, strict=True)
    assert s.exitstatus == 1


def test_no_new_entries_is_silent(monkeypatch, tmp_path, capsys):
    s = _run_guard(monkeypatch, tmp_path, entries={})
    assert s.exitstatus == 0
    assert "DATA-ROOT" not in capsys.readouterr().err


def test_a_probe_that_could_not_run_is_not_a_pass(monkeypatch, tmp_path, capsys):
    """A guard that could not look must not read as 'no leaks'."""
    mod = _load_conftest()
    monkeypatch.setattr(mod, "_data_probe_error", "ilk_paths import failed")
    session = _FakeSession()
    mod._enforce_no_data_root_leak(session)
    assert session.exitstatus == 1
    assert "DID NOT RUN" in capsys.readouterr().err


# ── wiring ───────────────────────────────────────────────────────────────────

@pytest.mark.timeout(120)
def test_guard_is_actually_wired_into_a_real_pytest_run(tmp_path):
    """End to end: a real subprocess pytest whose test writes to the root.

    This is the half a unit test cannot cover.  The predicate could be perfect
    while the snapshot is never taken or the hook never registered, and the
    session would end clean — which is exactly how AC-5 in the ledger writer
    passed while production could not reach the state it asserted.
    """
    ambient = tmp_path / "ambient-ilk-data"
    (ambient / "projects").mkdir(parents=True)

    shim = tmp_path / "conftest.py"
    shim.write_text(textwrap.dedent(f"""
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "ilk_root_conftest", r"{_CONFTEST}")
        m = importlib.util.module_from_spec(spec)
        sys.modules["ilk_root_conftest"] = m
        spec.loader.exec_module(m)
        pytest_configure = m.pytest_configure
        pytest_sessionfinish = m.pytest_sessionfinish
    """), encoding="utf-8")

    (tmp_path / "test_leaker.py").write_text(textwrap.dedent("""
        import os
        from pathlib import Path

        def test_writes_into_the_ambient_root():
            root = Path(os.environ["ILK_DATA_HOME"]) / "projects"
            d = root / "users-someone-a-real-looking-project" / "runtime"
            d.mkdir(parents=True, exist_ok=True)
            (d / "ship-proof.jsonl").write_text('{"slug":"x"}\\n')
            assert d.is_dir()
    """), encoding="utf-8")

    env = {
        **os.environ,
        "ILK_DATA_HOME": str(ambient),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    env.pop("ILK_DATA_LEAK_STRICT", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(tmp_path / "test_leaker.py")],
        capture_output=True, text=True, timeout=110, env=env, cwd=str(tmp_path),
    )
    combined = proc.stdout + proc.stderr
    assert "DATA-ROOT LEAK" in combined, (
        "the guard did not fire in a real run even though a test wrote into "
        f"the ambient root — snapshot or hook not wired.\n{combined}"
    )
    assert "users-someone-a-real-looking-project" in combined
    assert proc.returncode != 0, (
        "the inner test passed, so a non-zero exit can only come from the "
        f"guard; a clean exit means it reported without enforcing.\n{combined}"
    )


def test_a_write_into_an_EXISTING_entry_is_caught(monkeypatch, tmp_path, capsys):
    """The case a name-diff misses, and the only one that can reach evidence.

    The first version of this guard compared the SET OF NAMES before and after.
    A full suite run then added zero new names while still leaking — pytest
    rotates a small set of numbered basetemps, so the derived key usually
    already exists. And the dangerous write, into a REAL project's runtime,
    goes to a key that certainly already exists. A name diff was blind to
    precisely the event the guard is for; measured on chad-mbp 2026-09-09,
    24 entries before a full suite and 24 after.
    """
    s = _run_guard(
        monkeypatch, tmp_path,
        entries={"users-chad-projects-github-inluck-net-ilk-skills": 3},
        baseline={"users-chad-projects-github-inluck-net-ilk-skills": (1, 0.0)},
    )
    assert s.exitstatus == 1, (
        "a real project key that GAINED files during the session must fail — "
        "this is the accidentally-forged ship-proof row, and reap would "
        "accept it as proof on presence alone"
    )
    err = capsys.readouterr().err
    assert "GAINED FILES" in err and "was 1" in err, (
        "the report must show it grew, and from what, or the reader cannot "
        f"tell a leak from pre-existing state.\n{err}"
    )


def test_an_untouched_existing_entry_is_silent(monkeypatch, tmp_path, capsys):
    """Pre-existing content must not be reported as this run's doing."""
    s = _run_guard(
        monkeypatch, tmp_path,
        entries={"users-chad-projects-github-inluck-net-ilk-skills": 2},
        baseline={"users-chad-projects-github-inluck-net-ilk-skills": (2, 0.0)},
        session_start=time.time() + 3600,   # nothing can post-date this
    )
    assert s.exitstatus == 0, (
        "an entry whose file count and mtimes predate the session is not a "
        "leak; failing on it would make every run red for existing data"
    )
    assert "DATA-ROOT" not in capsys.readouterr().err


def test_a_touched_real_key_is_reported_not_failed(monkeypatch, tmp_path, capsys):
    """The concurrent-daemon case, and the guard's stated limit.

    chad-mbp runs a launchd-supervised scheduler polling every 5 minutes; a
    5-minute suite always overlaps a poll, and that daemon writes into a real
    project's runtime dir. Failing on "real key, has files, mtime moved" would
    convict the suite of the daemon's writes — the same wall-clock attribution
    defect that made gh-resolve's guard blame this repo. So a TOUCHED entry
    whose file count did not change is reported and not failed, and an append
    to an existing ledger lands here: a line to read, not a silence.
    """
    s = _run_guard(
        monkeypatch, tmp_path,
        entries={"users-chad-projects-github-inluck-net-ilk-skills": 2},
        baseline={"users-chad-projects-github-inluck-net-ilk-skills": (2, 0.0)},
        session_start=0.0,   # every mtime post-dates this -> TOUCHED
    )
    assert s.exitstatus == 0, (
        "a real key whose file COUNT did not change must not fail — on this "
        "host the likeliest writer is the live scheduler, not a test"
    )
    err = capsys.readouterr().err
    assert "TOUCHED" in err and "reported only" in err, (
        f"it must still be reported; the limit is attribution, not detection\n{err}"
    )
