"""Sub-plan ``a-re-measure-reuses-what-cannot-change`` step 0 — red-first pins.

Part of MASTER-2026-10-10c (``a-verify-fits-inside-its-gate``), order 2,
rows ``backlog-5e28d081f40944dc`` and ``backlog-6118449a06cc9bcc``
(residual).

A fix commit inside a verify makes the record stale and the driver re-runs
``verification_record.main(..., "--run-suite", ...)``.  Besides the suite,
every attempt recomputed owner resolution from zero — the cost the 10-10
retro measured at 558 of 584 s on the 10b-a re-measure.  The batch base is
fixed, so an owner found at H0 is still that id's owner at a descendant H1:
the commits up to H0 did not change.

What is NEVER reused: test outcomes and counts.  MASTER-2026-10-07m deleted
``_try_remeasure``; a re-measure still measures the current tree in full
(see ``2026-10-07m-a-reverify-carries-only-what-it-can-count.md``).

The pins:

AC-1 (reuse)             — two ``owners_of`` calls for the same 3 ids, the
                           second at a descendant head: the stub runner is
                           called 0 times and the results are equal.
AC-2 (sibling recomputes)— a second call at a head that is NOT a descendant
                           of H0 recomputes (the stub is called again).
AC-3 (budget)            — a ``how: budget`` result is never reused; the
                           next call recomputes it.
AC-4 (07m invariant)     — a re-measure after a fix commit touching a shared
                           module runs the full current-tree suite: the
                           recorded counts equal a fresh full run's and no
                           ``carried_from`` / ``rerun_selection`` line is
                           emitted.  PASSES AT BASE (07m already deleted the
                           carry path) — left unpinned as the row's
                           falsifier; say so here rather than claim a red.
AC-5 (recorded)          — the reusing call's record carries
                           ``owner_reused: 3 of 3``.

Each test imports the module under test in its own body; every red-first
test carries the MASTER's pin shape until step 1 removes every pin.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# The MASTER's literal red-first pin shape.
_XFAIL_NOT_BUILT = dict(
    strict=True,
    raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError),
    reason="not built yet",
)

_IDS = [
    "tests/test_a.py::test_a",
    "tests/test_b.py::test_b",
    "tests/test_c.py::test_c",
]


# ── module-level fixture: keep every write inside the tmp data home ─────────


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME / ILK_DATA_HOME / ILK_DATA_DIR to tmp_path.

    ``suite_ledger.ledger_dir`` resolves through
    ``ilk_paths.external_logs_dir``, so an unpinned data home would write
    ``points.jsonl`` and the owner cache into the real ``~/.ilk-data`` — the
    DATA-ROOT GUARD in the root conftest fails the session on that.
    ``ILK_WORKER_SESSION`` is cleared because ``record_point`` and
    ``verification_record.main`` both refuse in a worker session.

    ``PYTHONUSERBASE`` keeps the subprocess's user site-packages resolving:
    pytest lives in ``~/Library/Python/3.9``, and changing HOME without it
    makes the child interpreter unable to import pytest at all.
    """
    real_home = Path(os.environ.get("HOME", Path.home())).resolve()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_home = tmp_path / "data"
    data_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    user_base = real_home / "Library" / "Python" / "3.9"
    if user_base.is_dir():
        monkeypatch.setenv("PYTHONUSERBASE", str(user_base))


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    """Run a git command in *repo* and return stripped stdout."""
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace", timeout=60,
    ).stdout.strip()


def _write_launch(repo: Path) -> None:
    """Write ``.ilk-launch.json`` with a configured ship.suite."""
    launch = {
        "ship": {
            "suite": {
                "command": sys.executable,
                "flags": ["-m", "pytest", "-q", "-p", "no:cacheprovider"],
            }
        }
    }
    (repo / ".ilk-launch.json").write_text(
        json.dumps(launch, indent=2) + "\n", encoding="utf-8"
    )


def _make_repo(tmp_path: Path) -> Path:
    """A tmp git repo with one green test, the launch config, and base HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _write_launch(repo)
    (repo / "test_foo.py").write_text(
        "def test_foo(): assert True\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base: green")
    return repo


def _add_commits(repo: Path, n: int, prefix: str = "file") -> list[str]:
    """*n* first-parent commits after HEAD; returns their shas oldest-first."""
    shas: list[str] = []
    for i in range(1, n + 1):
        (repo / f"{prefix}_{i}.txt").write_text(f"{i}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m",
             f"feat(step): change {i} [plan:{prefix}{i}#step-0]")
        shas.append(_git(repo, "rev-parse", "HEAD"))
    return shas


def _write_points(repo: Path, base: str, shas: list[str],
                  run_id: str = "r1") -> list[str]:
    """One ``points.jsonl`` row per commit, each with its own slug.

    Deliberately writes NO ledger entries: with nothing ledgered the point
    path cannot name an owner, so only a probe (step 4) can — which is what
    makes the runner stub observable.
    """
    import suite_ledger  # noqa: PLC0415 - imported in-body by design

    slugs: list[str] = []
    before = base
    for i, after in enumerate(shas, start=1):
        slug = f"{run_id}-{i:02d}"
        suite_ledger.record_point(
            repo, run_id=run_id, iteration=i, slug=slug, master="m1",
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

    def runner(cmd, *, shell=False, cwd=None, timeout=None, **kwargs):
        calls.append(cmd)
        idx = order.index(_head_at(cwd))
        if isinstance(red_from, dict):
            node_id = cmd.rsplit(" ", 1)[-1]
            cut = red_from[node_id]
        else:
            cut = red_from
        return (1 if idx >= cut else 0, "", "", False)

    return runner, calls


def _owner_rows(text: str) -> list[str]:
    """The ``## Owners`` table's data rows."""
    if "## Owners" not in text:
        return []
    section = text.split("## Owners", 1)[1]
    rows = [l for l in section.splitlines() if l.startswith("| tests/")]
    return rows


# ── AC-1: a descendant head reuses the owners, so no probe runs ─────────────


@pytest.mark.xfail(**_XFAIL_NOT_BUILT)
def test_ac1_descendant_head_reuses_owners(tmp_path: Path) -> None:
    """3 ids, first call at H0, second at H1 (a child of H0).

    The second call must not touch the runner at all and must hand back the
    same verdicts — the commits that produced the owners did not change.
    """
    import suite_ledger  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    shas = _add_commits(repo, 7)
    slugs = _write_points(repo, base, shas)
    order = [base, *shas]

    runner1, calls1 = _make_runner(order, red_from=3)
    first = suite_ledger.owners_of(
        repo, list(_IDS), base_sha=base, head_sha=shas[-1], runner=runner1,
    )
    assert calls1, "the first call must have probed — the pins need work"
    assert first[_IDS[0]]["how"] == "bisect", first
    assert first[_IDS[0]]["sha"] == shas[2], first

    # H1: a descendant of H0 that carries no new point row.
    (repo / "fix.txt").write_text("fix\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fix: descendant head")
    h1 = _git(repo, "rev-parse", "HEAD")

    runner2, calls2 = _make_runner([*order, h1], red_from=3)
    second = suite_ledger.owners_of(
        repo, list(_IDS), base_sha=base, head_sha=h1, runner=runner2,
    )

    assert not calls2, (
        f"the second call re-measured owners at a descendant head: "
        f"{len(calls2)} probe(s) — {calls2}"
    )
    assert second == first, f"reuse changed the verdicts: {second} != {first}"
    # The slugs the first call resolved are still nameable from the cache.
    assert second[_IDS[0]]["slug"] == slugs[2], second


# ── AC-2: a sibling head is not covered, so it recomputes ────────────────────


def test_ac2_sibling_head_recomputes(tmp_path: Path) -> None:
    """The second call's head is NOT a descendant of H0: the stub runs again.

    Unpinned control: with no cache at base this already recomputes, so the
    assertion holds before and after the build.
    """
    import suite_ledger  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    main_shas = _add_commits(repo, 5)
    _write_points(repo, base, main_shas, run_id="main")
    h0 = main_shas[-1]

    # Sibling line: branch off base, so its head is no descendant of h0.
    _git(repo, "checkout", "-b", "side", base)
    side_shas = _add_commits(repo, 3, prefix="side")
    _write_points(repo, base, side_shas, run_id="side")
    h1 = side_shas[-1]

    runner1, calls1 = _make_runner([base, *main_shas], red_from=3)
    suite_ledger.owners_of(
        repo, list(_IDS), base_sha=base, head_sha=h0, runner=runner1,
    )
    assert calls1, "the first call must have probed"

    runner2, calls2 = _make_runner([base, *side_shas], red_from=2)
    second = suite_ledger.owners_of(
        repo, list(_IDS), base_sha=base, head_sha=h1, runner=runner2,
    )

    assert calls2, (
        "a head that is not a descendant of the cached head must recompute "
        "— the cached rows describe a different branch"
    )
    assert set(second) == set(_IDS), second
    assert second[_IDS[0]]["sha"] == side_shas[1], second


# ── AC-3: a budget verdict is never written to the cache ─────────────────────


def test_ac3_budget_results_are_never_reused(tmp_path: Path) -> None:
    """``budget_s=0`` yields ``how: budget`` for every id; the next call
    recomputes them instead of handing the unfinished verdicts back.

    Unpinned control: at base nothing is cached at all, so the recompute
    already happens.
    """
    import suite_ledger  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    shas = _add_commits(repo, 7)
    _write_points(repo, base, shas)
    order = [base, *shas]

    runner1, calls1 = _make_runner(order, red_from=3)
    first = suite_ledger.owners_of(
        repo, list(_IDS), base_sha=base, head_sha=shas[-1],
        budget_s=0, runner=runner1,
    )
    assert not calls1, "a zero budget must not probe"
    assert all(v["how"] == "budget" for v in first.values()), first
    assert all(v["sha"] is None for v in first.values()), first

    runner2, calls2 = _make_runner(order, red_from=3)
    second = suite_ledger.owners_of(
        repo, list(_IDS), base_sha=base, head_sha=shas[-1], runner=runner2,
    )

    assert calls2, (
        "an unfinished (budget) verdict must never be reused — the next call "
        "has to search again"
    )
    assert second != first, second
    assert all(v["how"] in ("point", "bisect") for v in second.values()), (
        second
    )


# ── AC-4: the suite itself is still measured fresh (07m) ─────────────────────


def test_ac4_remeasure_runs_the_full_current_tree(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fix commit touching a shared module re-measures the CURRENT tree.

    Fixture: ``skills/x/scripts/shared.py`` is imported by two test files;
    ``tests/test_c.py`` reads ``data.txt``.  The prior measured attempt (H0)
    has ``data.txt`` broken, so test_c is red.  The fix commit touches the
    shared module AND fixes ``data.txt``.

    A carry-forward would select the shared module's two importers and carry
    test_c's red: ``suite_failed: 1`` plus a ``carried_from`` line.  The
    canonical path runs everything: the recorded counts must equal a fresh
    full run's and no carry metadata may appear.

    PASSES AT BASE — MASTER-2026-10-07m already deleted ``_try_remeasure``.
    Left unpinned deliberately: this is the row's falsifier, the assertion
    that fails if the owner cache ever grows into an outcome cache.
    """
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    import suite_ledger  # noqa: PLC0415
    import verification_record as vr  # noqa: PLC0415

    def _write(rel: str, body: str) -> None:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init")
    _write(".ilk-launch.json", json.dumps({
        "ship": {"suite": {"command": sys.executable,
                           "flags": ["-m", "pytest", "-q",
                                     "-p", "no:cacheprovider"]}},
    }, indent=2) + "\n")

    _PATH_BOOTSTRAP = (
        "import pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
    )
    _write("skills/x/scripts/shared.py", "def value():\n    return 1\n")
    _write("tests/test_a.py",
           _PATH_BOOTSTRAP + "from skills.x.scripts.shared import value\n\n"
           "def test_a():\n    assert value() == 1\n")
    _write("tests/test_b.py",
           _PATH_BOOTSTRAP + "from skills.x.scripts.shared import value\n\n"
           "def test_b():\n    assert value() == 1\n")
    _write("tests/test_c.py",
           "import pathlib\n\n"
           "def test_c():\n"
           "    data = (pathlib.Path(__file__).resolve().parents[1]\n"
           "            / 'data.txt').read_text()\n"
           "    assert data.strip() == 'ok'\n")
    _write("data.txt", "bad\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base: three tests")
    base = _git(repo, "rev-parse", "HEAD")

    # H0: the prior measured attempt — data.txt still broken.
    _write("doc.txt", "documentation\n")
    _git(repo, "add", "doc.txt")
    _git(repo, "commit", "-m", "docs: h0")
    h0 = _git(repo, "rev-parse", "HEAD")
    h0_tree = _git(repo, "rev-parse", f"{h0}^{{tree}}")

    # The fix: touches the shared module AND fixes data.txt.
    _write("skills/x/scripts/shared.py", "def value():\n    return 1  # fix\n")
    _write("data.txt", "ok\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fix: shared module + data")

    invocation = suite_ledger._resolve_invocation(repo)
    failing = ["tests/test_c.py::test_c"]
    prior_counts = {"passed": 2, "failed": 1, "errors": 0, "skipped": 0,
                    "xfailed": 0, "xpassed": 0, "total": 3}

    # Ledger entry for H0's tree — what the deleted carry path read.
    ld = suite_ledger.ledger_dir(repo)
    ld.mkdir(parents=True, exist_ok=True)
    prior_entry = {
        "tree": h0_tree, "invocation": invocation, "counts": prior_counts,
        "failing_nodes": failing, "suite_duration_sec": 1,
        "scope_mode": "full", "selected_files": [], "digest": "",
    }
    prior_entry["digest"] = suite_ledger._compute_digest(prior_entry)
    (ld / f"{h0_tree}.json").write_text(
        json.dumps(prior_entry, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # The prior measured record + its history row.
    record = tmp_path / "record.md"
    prior_text = vr.render_record(
        batch="ac4", head=h0, tree=h0_tree, base_sha=base,
        invocation=invocation,
        scope={"mode": "full", "count": 3, "reason": "full suite"},
        results={"counts": prior_counts, "failing_nodes": failing},
        at_base={failing[0]: "failed"}, base_red=[], head_red=[],
        suite_duration_sec=1,
    )
    record.write_text(prior_text, encoding="utf-8")
    vr._append_history_entry(
        record, attempt=1, digest=vr._compute_record_digest(prior_text),
        failing_nodes=failing, suite_duration_sec=1, head=h0, tree=h0_tree,
    )

    # A fresh full run of the CURRENT tree — the measurement of record.
    truth = vr.run_suite(repo, invocation, 60, selection=None)
    assert truth["counts"]["failed"] == 0, truth["counts"]
    assert truth["counts"]["passed"] == 3, truth["counts"]

    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verification_record.py"),
         "--project", str(repo), "--record", str(record),
         "--run-suite", "--base-sha", base,
         "--scope", "full", "--ledger", "off"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )
    assert result.returncode == 0, (
        f"verify should succeed:\n{result.stdout}\n{result.stderr}"
    )

    text = record.read_text(encoding="utf-8")
    passed = re.search(r"^suite_passed:\s*(\d+)", text, re.MULTILINE)
    failed = re.search(r"^suite_failed:\s*(\d+)", text, re.MULTILINE)
    assert passed and failed, f"no suite counts in:\n{text}"
    assert int(passed.group(1)) == truth["counts"]["passed"], (
        f"suite_passed {passed.group(1)} != fresh full run "
        f"{truth['counts']['passed']}"
    )
    assert int(failed.group(1)) == (
        truth["counts"]["failed"] + truth["counts"]["errors"]
    ), (
        f"suite_failed {failed.group(1)} != fresh full run "
        f"{truth['counts']['failed'] + truth['counts']['errors']}"
    )
    assert "carried_from:" not in text, "the record carried prior outcomes"
    assert "rerun_selection:" not in text, "the record carried a selection"


# ── AC-5: the reuse is recorded, not silent ──────────────────────────────────


@pytest.mark.xfail(**_XFAIL_NOT_BUILT)
def test_ac5_record_carries_owner_reused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two verifies at the same base; the second is the re-measure.

    The first computes the owners (probes run), a fix commit lands, the
    second verify reuses them — and its record says so with
    ``owner_reused: 3 of 3``.  The assertion that is red at base is the
    record line: everything before it holds at base too.
    """
    import suite_ledger  # noqa: PLC0415
    import verification_record as vr  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    # Three ids that exist and are untouched by the batch, so every one of
    # them reaches the head reruns.
    (repo / "tests").mkdir(exist_ok=True)
    for name in ("a", "b", "c"):
        (repo / "tests" / f"test_{name}.py").write_text(
            f"def test_{name}(): assert True\n", encoding="utf-8"
        )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "test: three files")
    base = _git(repo, "rev-parse", "HEAD")
    shas = _add_commits(repo, 4, prefix="point")
    _write_points(repo, base, shas, run_id="ac5")

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

    monkeypatch.setattr(vr, "run_suite", lambda *a, **kw: {
        "counts": {"passed": 0, "failed": 3, "errors": 0, "skipped": 0,
                   "xfailed": 0, "xpassed": 0, "total": 3},
        "failing_nodes": list(_IDS), "exit_code": 1,
        "suite_duration_sec": 1, "suite_output_text": "FAILED\n",
    })
    monkeypatch.setattr(vr, "run_at_base",
                        lambda *a, **kw: {nid: "passed" for nid in _IDS})
    monkeypatch.setattr(vr, "run_at_adding_commit",
                        lambda *a, **kw: ({}, {}))
    monkeypatch.setattr(vr, "run_head_reruns",
                        lambda *a, **kw: ({nid: (3, 3) for nid in _IDS},
                                          False))

    # An always-red probe: any commit it checks out is red, so the bisect
    # always lands on the range's first commit and the verdict carries a sha.
    # What is under test is whether the probe RUNS, not where it lands.
    probes: list[str] = []

    def always_red(cmd, *, shell=False, cwd=None, timeout=None, **kwargs):
        probes.append(cmd)
        return (1, "", "", False)

    monkeypatch.setattr(suite_ledger, "_bounded_run", always_red)

    record = tmp_path / "record.md"
    args = [
        "--project", str(repo),
        "--record", str(record),
        "--base-sha", base,
        "--run-suite",
        "--ledger", "off",
        "--scope", "full",
        "--master", str(master),
    ]

    ret1 = vr.main(list(args))
    assert ret1 in (0, 1), ret1
    assert probes, "the first verify must have probed for owners"
    first_text = record.read_text(encoding="utf-8")
    assert len(_owner_rows(first_text)) == 3, (
        f"first record names {len(_owner_rows(first_text))} owners:\n"
        f"{first_text}"
    )
    probes.clear()

    # The fix commit: makes the record stale, so attempt 2 is the re-measure.
    (repo / "fix.txt").write_text("fix\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fix: stale record")

    ret2 = vr.main(list(args))
    assert ret2 in (0, 1), ret2
    text = record.read_text(encoding="utf-8")

    # Red at base: the reuse is never announced, because it never happens.
    assert "owner_reused: 3 of 3" in text, (
        f"the reusing record does not say how much it reused:\n{text}"
    )
    # After the line: the re-measure paid for no probes either.
    assert not probes, f"the re-measure probed again: {probes}"
    assert len(_owner_rows(text)) == 3, text
