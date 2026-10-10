"""Sub-plan ``an-owner-search-fits-its-budget`` step 0 — red-first pins.

Part of MASTER-2026-10-10c (``a-verify-fits-inside-its-gate``), order 0,
row ``backlog-7e5fcc2172db8101``.

``suite_ledger.owner_of`` resolves a failing id by cloning the repo and
checking out EVERY first-parent commit in ``base..head``, running the
suite's full xdist invocation at each one for up to 120 s, with no phase
budget.  On gh-resolve 09c that was 21 ids x 19 commits; the 10b-a
re-measure spent 558 of 584 s inside owner resolution, and the 12:34
re-gate was killed at its 1860 s cap still inside the phase.

These pins encode the fix as six acceptance criteria:

AC-1 (names the breaker)   — a 19-commit range resolves to the FIRST red
                             commit by binary search, not by walking them.
AC-2 (bounded calls)       — 21 ids x 19 commits costs at most 21*5 runner
                             calls and finishes in well under a minute.
AC-3 (no xdist)            — no probe carries ``-n``/``--dist``; plus the
                             ``strip_xdist`` unit cases.
AC-4 (budget)              — a wall-clock budget returns ``how: budget``
                             for the ids it did not finish, never a raise.
AC-5 (one clone)           — ONE ``git clone`` per ``owners_of`` call.
AC-6 (reliably red only)   — the owner phase runs after the head reruns
                             and only over ids that were red K of K; the
                             record's ``phase_seconds`` line carries
                             ``owner``.

Each test imports the module under test in its own body and is pinned
with the MASTER's red-first shape until step 1 removes every pin.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ── module-level fixture: keep every write inside the tmp data home ─────────


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME / ILK_DATA_HOME / ILK_DATA_DIR to tmp_path.

    ``suite_ledger.ledger_dir`` resolves through ``ilk_paths.external_logs_dir``,
    so an unpinned data home would write points into the real ``~/.ilk-data``.
    ``ILK_WORKER_SESSION`` is cleared because ``record_point`` and
    ``verification_record.main`` both refuse in a worker session.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_home = tmp_path / "data"
    original_home = os.environ.get("HOME", "")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("ILK_DATA_DIR", str(data_home))
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    try:
        import suite_ledger
        suite_ledger._ORIGINAL_HOME = original_home
    except (ImportError, AttributeError, TypeError):
        pass


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    """Run a git command in *repo* and return stripped stdout."""
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace", timeout=60,
    ).stdout.strip()


def _write_launch(repo: Path, flags: list[str] | None = None) -> None:
    """Write ``.ilk-launch.json`` with a configured ship.suite."""
    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": flags if flags is not None
                else ["-q", "-p", "no:cacheprovider"],
            }
        }
    }
    (repo / ".ilk-launch.json").write_text(
        json.dumps(launch, indent=2) + "\n", encoding="utf-8"
    )


def _make_repo(tmp_path: Path, flags: list[str] | None = None) -> Path:
    """A tmp git repo with one green test, the launch config, and base HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _write_launch(repo, flags)
    (repo / "test_foo.py").write_text(
        "def test_foo(): assert True\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base: green")
    return repo


def _add_commits(repo: Path, n: int) -> list[str]:
    """*n* first-parent commits after HEAD; returns their shas oldest-first."""
    shas: list[str] = []
    for i in range(1, n + 1):
        (repo / f"file_{i}.txt").write_text(f"{i}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", f"feat(step): change {i} [plan:p{i}#step-0]")
        shas.append(_git(repo, "rev-parse", "HEAD"))
    return shas


def _write_points(repo: Path, base: str, shas: list[str]) -> list[str]:
    """One ``points.jsonl`` row per commit, each with its own slug.

    Deliberately writes NO ledger entries: with nothing ledgered the point
    path cannot name an owner, so only a probe (step 4) can.
    """
    import suite_ledger  # noqa: PLC0415 - imported in-body by design

    slugs: list[str] = []
    before = base
    for i, after in enumerate(shas, start=1):
        slug = f"p{i:02d}"
        suite_ledger.record_point(
            repo, run_id="r1", iteration=i, slug=slug, master="m1",
            before=before, after=after, shipped=[],
        )
        slugs.append(slug)
        before = after
    return slugs


def _head_at(cwd) -> str:
    """The sha checked out in *cwd* (the probe's snapshot clone)."""
    return subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace", timeout=30,
    ).stdout.strip()


def _make_runner(order: list[str], red_from):
    """A runner stub: red iff the checked-out HEAD is at/after its cut.

    *order* is ``[base_sha, commit_1, ..., commit_n]`` — the index a probe
    lands on is its position there.  *red_from* is either one cut for every
    id or ``{node_id: cut}``.  Returns ``(runner, calls)``.
    """
    calls: list[str] = []

    def runner(cmd, *, shell, cwd, timeout):
        calls.append(cmd)
        idx = order.index(_head_at(cwd))
        if isinstance(red_from, dict):
            node_id = cmd.rsplit(" ", 1)[-1]
            cut = red_from[node_id]
        else:
            cut = red_from
        return (1 if idx >= cut else 0, "", "", False)

    return runner, calls


# ── AC-1: the bisect names the first red commit, not the last one ────────────


def test_ac1_bisect_names_commit_11(tmp_path: Path) -> None:
    """19 commits, one point row each, no ledger entries.

    The stub is red at HEAD >= commit 11, so commit 11 is the breaker and
    ``owners_of`` must name its sha and its point's slug with ``how: bisect``.
    """
    import suite_ledger  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    shas = _add_commits(repo, 19)
    slugs = _write_points(repo, base, shas)
    order = [base, *shas]

    runner, calls = _make_runner(order, red_from=11)

    result = suite_ledger.owners_of(
        repo, "test_foo.py::test_foo",
        base_sha=base, head_sha=shas[-1], runner=runner,
    )

    assert result["how"] == "bisect", result
    assert result["sha"] == shas[10], (
        f"expected commit 11 ({shas[10][:12]}), got {result}"
    )
    assert result["slug"] == slugs[10], result
    assert calls, "the runner must have probed at least once"


# ── AC-2: 21 ids x 19 commits stays inside the budget ────────────────────────


@pytest.mark.timeout(90)  # measured 12.18 s isolated / 17.16 s kill under full-suite -n auto (2026-10-10)
def test_ac2_twenty_one_ids_fit_the_call_budget(tmp_path: Path) -> None:
    """21 ids over the same 19 commits: <= 21*5 probes, whole call < 60 s.

    ``ceil(log2(19)) == 5`` probes is the bisect's worst case, so the old
    linear walk (19 probes per id) would cost 399 and be caught here.
    21 distinct breaking commits do not exist in a 19-commit range, so ids
    20 and 21 reuse commits 01 and 02 — the bound is what is under test.
    """
    import suite_ledger  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    shas = _add_commits(repo, 19)
    _write_points(repo, base, shas)
    order = [base, *shas]

    node_ids = [f"tests/test_budget.py::test_{i:02d}" for i in range(21)]
    # Each id turns red at a different commit, cycling over the 19 available.
    red_from = {nid: (i % 19) + 1 for i, nid in enumerate(node_ids)}
    runner, calls = _make_runner(order, red_from)

    started = time.monotonic()
    result = suite_ledger.owners_of(
        repo, node_ids, base_sha=base, head_sha=shas[-1], runner=runner,
    )
    elapsed = time.monotonic() - started

    assert set(result) == set(node_ids), (
        f"every id must get a verdict; missing {set(node_ids) - set(result)}"
    )
    assert len(calls) <= 21 * 5, f"{len(calls)} runner calls for 21 ids"
    assert elapsed < 60, f"owners_of took {elapsed:.1f}s"


# ── AC-3: no probe carries the suite's xdist flags ───────────────────────────


def test_ac3_no_xdist_reaches_a_probe(tmp_path: Path) -> None:
    """``-n 8 --dist loadfile`` is resolved, then stripped from every probe."""
    import suite_ledger  # noqa: PLC0415

    flags = ["-q", "-p", "no:cacheprovider", "-n", "8", "--dist", "loadfile"]
    repo = _make_repo(tmp_path, flags=flags)
    resolved = suite_ledger._resolve_invocation(repo)
    assert "-n 8" in resolved and "--dist loadfile" in resolved, resolved

    base = _git(repo, "rev-parse", "HEAD")
    shas = _add_commits(repo, 7)
    _write_points(repo, base, shas)
    order = [base, *shas]

    runner, calls = _make_runner(order, red_from=4)
    suite_ledger.owners_of(
        repo, "test_foo.py::test_foo",
        base_sha=base, head_sha=shas[-1], runner=runner,
    )

    assert calls, "the runner must have been called"
    for cmd in calls:
        tokens = cmd.split()
        assert not any(t == "-n" or t.startswith("-n") for t in tokens), cmd
        assert not any(t == "--dist" or t.startswith("--dist")
                       for t in tokens), cmd

    # strip_xdist unit cases.
    strip = suite_ledger.strip_xdist
    assert strip("python3 -m pytest -q -n 8") == "python3 -m pytest -q"
    assert strip("python3 -m pytest -q -n8") == "python3 -m pytest -q"
    assert strip("python3 -m pytest -n auto -q") == "python3 -m pytest -q"
    assert strip("python3 -m pytest --dist loadfile -q") == (
        "python3 -m pytest -q")
    assert strip("python3 -m pytest --dist=loadfile -q") == (
        "python3 -m pytest -q")
    plain = "python3 -m pytest -q"
    assert strip(plain) == plain


# ── AC-4: the budget yields a classified verdict, never a hang ───────────────


def test_ac4_budget_yields_budget_verdicts(tmp_path: Path) -> None:
    """``budget_s=1`` with a 0.5 s-per-call stub: back in < 5 s, leftovers
    carry ``how: budget`` and no slug/sha."""
    import suite_ledger  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    shas = _add_commits(repo, 19)
    _write_points(repo, base, shas)

    node_ids = [f"tests/test_budget.py::test_{i}" for i in range(4)]

    def slow_runner(cmd, *, shell, cwd, timeout):
        time.sleep(0.5)
        return (0, "", "", False)

    started = time.monotonic()
    result = suite_ledger.owners_of(
        repo, node_ids, base_sha=base, head_sha=shas[-1],
        budget_s=1, runner=slow_runner,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"owners_of took {elapsed:.1f}s under a 1 s budget"
    assert set(result) == set(node_ids), result
    unfinished = [nid for nid in node_ids if result[nid]["how"] == "budget"]
    assert unfinished, f"no id hit the budget: {result}"
    for nid in unfinished:
        assert result[nid]["slug"] is None, result[nid]
        assert result[nid]["sha"] is None, result[nid]


# ── AC-5: one clone for the whole call, not one per id ───────────────────────


def test_ac5_clones_once_per_call(tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Three ids sharing one range ⇒ exactly one ``git clone``."""
    import suite_ledger  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    shas = _add_commits(repo, 5)
    _write_points(repo, base, shas)
    order = [base, *shas]

    node_ids = [f"tests/test_clone.py::test_{i}" for i in range(3)]
    runner, _calls = _make_runner(order, red_from=3)

    real_run = subprocess.run
    clones: list[list] = []

    def counting_run(cmd, *args, **kwargs):
        if (isinstance(cmd, (list, tuple)) and len(cmd) >= 2
                and cmd[0] == "git" and "clone" in cmd):
            clones.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(suite_ledger.subprocess, "run", counting_run)

    suite_ledger.owners_of(
        repo, node_ids, base_sha=base, head_sha=shas[-1], runner=runner,
    )

    assert len(clones) == 1, f"expected 1 clone for 3 ids, got {len(clones)}"


# ── AC-6: only reliably-red ids get an owner, and the phase is timed ─────────


def test_ac6_owner_runs_after_reruns_on_reliably_red_only(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Head reruns ``{a: 3/3, b: 1/2, c: 0/1}`` ⇒ ``owners_of([a])`` only,
    and the rendered record's ``phase_seconds:`` line carries ``owner``."""
    import suite_ledger  # noqa: PLC0415
    import verification_record as vr  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    # The three node ids' files exist at base and are untouched by the batch,
    # so batch_touched is False for all of them and every id reaches the
    # head reruns.
    for name in ("a", "b", "c"):
        (repo / "tests").mkdir(exist_ok=True)
        (repo / "tests" / f"test_{name}.py").write_text(
            f"def test_{name}(): assert True\n", encoding="utf-8"
        )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "test: three files")
    base_sha = _git(repo, "rev-parse", "HEAD")

    (repo / "notes.txt").write_text("head\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "docs: head")

    master = tmp_path / "MASTER.md"
    master.write_text(
        "# MASTER\n\n"
        "## Sub-plan registry\n\n"
        "| # | Slug | File |\n"
        "|---:|---|---|\n"
        "| 1 | ours | [2026-10-10-ours.md](./2026-10-10-ours.md) |\n\n"
        "## Notes\n",
        encoding="utf-8",
    )

    ids = ["tests/test_a.py::test_a", "tests/test_b.py::test_b",
           "tests/test_c.py::test_c"]
    head_reruns = {"tests/test_a.py::test_a": (3, 3),
                   "tests/test_b.py::test_b": (1, 2),
                   "tests/test_c.py::test_c": (0, 1)}

    monkeypatch.setattr(vr, "run_suite", lambda *a, **kw: {
        "counts": {"passed": 0, "failed": 3, "errors": 0, "skipped": 0,
                   "total": 3},
        "failing_nodes": list(ids), "exit_code": 1,
        "suite_duration_sec": 1, "suite_output_text": "FAILED\n",
    })
    monkeypatch.setattr(vr, "run_at_base",
                        lambda *a, **kw: {nid: "passed" for nid in ids})
    monkeypatch.setattr(vr, "run_at_adding_commit",
                        lambda *a, **kw: ({}, {}))
    monkeypatch.setattr(vr, "run_head_reruns",
                        lambda *a, **kw: (dict(head_reruns), False))

    owner_calls: list[list[str]] = []

    def fake_owners_of(project, node_ids, *, base_sha, head_sha, **kw):
        owner_calls.append(list(node_ids))
        return {nid: {"slug": None, "sha": None, "how": "unknown"}
                for nid in node_ids}

    monkeypatch.setattr(suite_ledger, "owners_of", fake_owners_of)

    record = tmp_path / "record.md"
    ret = vr.main([
        "--record", str(record),
        "--base-sha", base_sha,
        "--run-suite",
        "--ledger", "off",
        "--scope", "full",
        "--master", str(master),
    ])

    assert owner_calls == [["tests/test_a.py::test_a"]], (
        f"only the reliably-red id may get an owner lookup: {owner_calls}"
    )
    text = record.read_text(encoding="utf-8")
    phase_lines = [l for l in text.splitlines()
                   if l.startswith("phase_seconds:")]
    assert phase_lines, f"no phase_seconds line in:\n{text}"
    assert "owner=" in phase_lines[0], phase_lines[0]
    assert ret in (0, 1), ret
