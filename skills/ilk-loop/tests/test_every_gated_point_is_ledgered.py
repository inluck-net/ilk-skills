"""Red-first pins: every gated point is ledgered.

Part of sub-plan ``every-gated-point-is-ledgered`` (MASTER-2026-10-03i).

Tests the runner's ledger integration: spawning a background measurement at
agent return, recording a driver-written point row after ship-integrity, and
the call-order contract in ``run_ilk_loop_claude.sh``.

AC-1: ``ledger_spawn_for_head <tmp repo>`` with a stub ``suite_ledger.py``
      that records its argv calls ``spawn --project <tmp repo> --sha <HEAD sha>
      --run-id <RUN_ID>`` exactly once and returns 0 even when the stub exits 1.
AC-2: ``ledger_record_point`` passes ``--slug``, ``--before``, ``--after``
      (HEAD), ``--shipped a,b`` and ``--iteration``; with a stub that exits 1
      the function still returns 0.
AC-3 (real module): ``suite_ledger.record_point(...)`` appends one line with
      ``writer: "driver"`` and the tree of ``after``; with
      ``ILK_WORKER_SESSION=1`` it raises ``LedgerRefused`` and appends nothing;
      with ``before == after`` and no shipped slugs it appends nothing.
AC-4 (call order, static): in the runner text, the first
      ``ledger_spawn_for_head`` call inside ``main()`` sits after
      ``get_repo_heads "$heads_after_file"`` and before
      ``_should_gate_iteration "$ITER_COMPLETED"``; a ``ledger_record_point``
      call sits after the ``# -- Selfmod merge-back`` comment; and one sits
      inside ``attempt_gate_first_fast_path`` after the ``shipped by the
      driver`` echo.
AC-5: the contracts doc has a heading containing ``suite_ledger.py`` and the
      string ``run_ilk_loop`` appears in that section as the forbidden pattern.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent / "scripts"
_RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"
_CONTRACTS = _TESTS.parent / "references" / "detached-component-contracts.md"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    """Run a git command in *repo* and return stripped stdout."""
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _make_repo(tmp_path: Path) -> tuple[Path, str]:
    """Create a temp git repo with one commit.  Returns (repo, head_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, head


def _make_repo_two_commits(tmp_path: Path) -> tuple[Path, str, str]:
    """Create a repo with two commits.  Returns (repo, sha_before, sha_after)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "commit A")
    sha_a = _git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "commit B")
    sha_b = _git(repo, "rev-parse", "HEAD")
    return repo, sha_a, sha_b


# ── AC-1: ledger_spawn_for_head calls spawn exactly once ─────────────────────


@pytest.mark.xfail(strict=True, reason="AC-1: ledger_spawn_for_head not yet implemented")
def test_ledger_spawn_for_head_calls_spawn_once(tmp_path: Path) -> None:
    """AC-1: ``ledger_spawn_for_head <repo>`` calls ``suite_ledger.py spawn``
    with ``--project``, ``--sha <HEAD>``, ``--run-id <RUN_ID>`` exactly once.
    Returns 0 even when the stub exits 1.
    """
    repo, head = _make_repo(tmp_path)

    # Stub suite_ledger.py that records argv calls and exits 1.
    stub_dir = tmp_path / "stub-scripts"
    stub_dir.mkdir()
    log_file = tmp_path / "spawn-calls.jsonl"
    (stub_dir / "suite_ledger.py").write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"with open({str(log_file)!r}, 'a') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    # Source the runner under ILK_DOTSOURCE_ONLY=1 and call
    # ledger_spawn_for_head in a subshell.
    run_id = "20261003-120000"
    env = {
        **os.environ,
        "ILK_DOTSOURCE_ONLY": "1",
        "_SKILL_ROOT": str(stub_dir.parent.parent),  # Not used for stub
        "RUN_ID": run_id,
        "HOME": str(tmp_path / "home"),
        "ILK_DATA_HOME": str(tmp_path / ".ilk-data"),
    }
    # We need to source the runner and call the function.  The function
    # is defined inside main()'s scope, so we test it by sourcing and
    # overriding the suite_ledger path.
    script = (
        f"source '{_RUNNER}' 2>/dev/null; "
        f"ledger_spawn_for_head '{repo}' 2>/dev/null; echo rc=$?"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=30,
    )
    # The function should return 0 even when the stub exits 1.
    assert "rc=0" in result.stdout, f"expected rc=0, got: {result.stdout!r}"
    # The stub should have been called exactly once with spawn args.
    assert log_file.exists(), "stub was never called"
    calls = [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]
    assert len(calls) == 1, f"expected 1 call, got {len(calls)}"
    args = calls[0]
    assert "spawn" in args, f"expected 'spawn' in args, got {args}"
    assert "--sha" in args and head in args, f"expected --sha {head} in {args}"
    assert "--run-id" in args and run_id in args, f"expected --run-id {run_id} in {args}"


# ── AC-2: ledger_record_point passes all fields ─────────────────────────────


@pytest.mark.xfail(strict=True, reason="AC-2: ledger_record_point not yet implemented")
def test_ledger_record_point_passes_all_fields(tmp_path: Path) -> None:
    """AC-2: ``ledger_record_point`` passes ``--slug``, ``--before``,
    ``--after`` (HEAD), ``--shipped a,b`` and ``--iteration``.
    Returns 0 even when the stub exits 1.
    """
    repo, sha_a, sha_b = _make_repo_two_commits(tmp_path)

    # Stub suite_ledger.py that records argv calls and exits 1.
    stub_dir = tmp_path / "stub-scripts"
    stub_dir.mkdir()
    log_file = tmp_path / "point-calls.jsonl"
    (stub_dir / "suite_ledger.py").write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"with open({str(log_file)!r}, 'a') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    run_id = "20261003-120000"
    iteration = "3"
    slug = "every-gated-point-is-ledgered"
    shipped = "a-tree-is-measured-once,every-gated-point-is-ledgered"
    master = "MASTER-2026-10-03i-verification-is-continuous-execution-plan.md"

    env = {
        **os.environ,
        "ILK_DOTSOURCE_ONLY": "1",
        "_SKILL_ROOT": str(stub_dir.parent.parent),
        "RUN_ID": run_id,
        "HOME": str(tmp_path / "home"),
        "ILK_DATA_HOME": str(tmp_path / ".ilk-data"),
    }
    script = (
        f"source '{_RUNNER}' 2>/dev/null; "
        f"ledger_record_point '{repo}' '{sha_a}' '{shipped}' 2>/dev/null; echo rc=$?"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=30,
    )
    assert "rc=0" in result.stdout, f"expected rc=0, got: {result.stdout!r}"
    assert log_file.exists(), "stub was never called"
    calls = [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]
    assert len(calls) == 1, f"expected 1 call, got {len(calls)}"
    args = calls[0]
    assert "point" in args, f"expected 'point' in args, got {args}"
    # Check all required flags.
    assert "--slug" in args and slug in args, f"missing --slug {slug}"
    assert "--before" in args and sha_a in args, f"missing --before {sha_a}"
    assert "--after" in args and sha_b in args, f"missing --after {sha_b}"
    assert "--shipped" in args and shipped in args, f"missing --shipped"
    assert "--iteration" in args and iteration in args, f"missing --iteration"
    assert "--run-id" in args and run_id in args, f"missing --run-id"


# ── AC-3: suite_ledger.record_point writes one line ──────────────────────────


@pytest.mark.xfail(strict=True, reason="AC-3: record_point not yet implemented")
def test_record_point_appends_one_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-3: ``suite_ledger.record_point(...)`` appends one JSON line with
    ``writer: "driver"`` and the tree of ``after``.
    """
    repo, sha_a, sha_b = _make_repo_two_commits(tmp_path)
    data_home = tmp_path / ".ilk-data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

    import suite_ledger

    # Compute the tree sha of sha_b (the "after" commit).
    tree_b = _git(repo, "rev-parse", f"{sha_b}^{{tree}}")

    # Call record_point.
    suite_ledger.record_point(
        project=Path(repo),
        run_id="20261003-120000",
        iteration=3,
        slug="every-gated-point-is-ledgered",
        master="MASTER-2026-10-03i-verification-is-continuous-execution-plan.md",
        before=sha_a,
        after=sha_b,
        shipped=["a-tree-is-measured-once", "every-gated-point-is-ledgered"],
    )

    # Read the points.jsonl file.
    ld = suite_ledger.ledger_dir(Path(repo))
    points_path = ld / "points.jsonl"
    assert points_path.is_file(), "points.jsonl not created"
    lines = [l for l in points_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1, f"expected 1 line, got {len(lines)}"
    entry = json.loads(lines[0])
    assert entry["writer"] == "driver"
    assert entry["tree"] == tree_b
    assert entry["slug"] == "every-gated-point-is-ledgered"


@pytest.mark.xfail(strict=True, reason="AC-3: record_point worker refusal not yet implemented")
def test_record_point_refuses_in_worker_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3: with ``ILK_WORKER_SESSION=1``, ``record_point`` raises
    ``LedgerRefused`` and appends nothing.
    """
    repo, sha_a, sha_b = _make_repo_two_commits(tmp_path)
    data_home = tmp_path / ".ilk-data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("ILK_WORKER_SESSION", "1")

    import suite_ledger

    with pytest.raises(suite_ledger.LedgerRefused):
        suite_ledger.record_point(
            project=Path(repo),
            run_id="20261003-120000",
            iteration=3,
            slug="every-gated-point-is-ledgered",
            master="MASTER-2026-10-03i.md",
            before=sha_a,
            after=sha_b,
            shipped=["a-tree-is-measured-once"],
        )

    # No points.jsonl should exist.
    ld = suite_ledger.ledger_dir(Path(repo))
    points_path = ld / "points.jsonl"
    assert not points_path.exists(), "points.jsonl created in worker session"


@pytest.mark.xfail(strict=True, reason="AC-3: record_point no-op not yet implemented")
def test_record_point_noop_when_no_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3: with ``before == after`` and no shipped slugs, ``record_point``
    appends nothing.
    """
    repo, head = _make_repo(tmp_path)
    data_home = tmp_path / ".ilk-data"
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)

    import suite_ledger

    suite_ledger.record_point(
        project=Path(repo),
        run_id="20261003-120000",
        iteration=3,
        slug="every-gated-point-is-ledgered",
        master="MASTER-2026-10-03i.md",
        before=head,
        after=head,
        shipped=[],
    )

    # No points.jsonl should exist (no-op).
    ld = suite_ledger.ledger_dir(Path(repo))
    points_path = ld / "points.jsonl"
    assert not points_path.exists(), "points.jsonl created for no-op"


# ── AC-4: call order in the runner is correct (static analysis) ──────────────


@pytest.mark.xfail(strict=True, reason="AC-4: ledger call sites not yet added to runner")
def test_call_order_spawn_after_heads_before_gate() -> None:
    """AC-4: the first ``ledger_spawn_for_head`` call inside ``main()`` sits
    after ``get_repo_heads "$heads_after_file"`` and before
    ``_should_gate_iteration "$ITER_COMPLETED"``.
    """
    text = _RUNNER.read_text(encoding="utf-8")

    # Find the main() function body.
    main_start = text.find("main()")
    assert main_start != -1, "main() not found in runner"
    main_body = text[main_start:]

    # Find the key landmarks.
    heads_idx = main_body.find('get_repo_heads "$heads_after_file"')
    assert heads_idx != -1, "get_repo_heads not found in main()"
    gate_idx = main_body.find('_should_gate_iteration "$ITER_COMPLETED"')
    assert gate_idx != -1, "_should_gate_iteration not found in main()"
    spawn_idx = main_body.find("ledger_spawn_for_head")
    assert spawn_idx != -1, "ledger_spawn_for_head not found in main()"

    # spawn must be between heads and gate.
    assert heads_idx < spawn_idx < gate_idx, (
        f"ledger_spawn_for_head at offset {spawn_idx} is not between "
        f"get_repo_heads ({heads_idx}) and _should_gate_iteration ({gate_idx})"
    )


@pytest.mark.xfail(strict=True, reason="AC-4: ledger_record_point call site not yet added")
def test_call_order_record_point_after_selfmod_mergeback() -> None:
    """AC-4: a ``ledger_record_point`` call sits after the
    ``# -- Selfmod merge-back`` comment.
    """
    text = _RUNNER.read_text(encoding="utf-8")

    main_start = text.find("main()")
    assert main_start != -1, "main() not found"
    main_body = text[main_start:]

    mergeback_idx = main_body.find("# -- Selfmod merge-back")
    assert mergeback_idx != -1, "Selfmod merge-back comment not found"
    record_idx = main_body.find("ledger_record_point")
    assert record_idx != -1, "ledger_record_point not found in main()"

    assert record_idx > mergeback_idx, (
        f"ledger_record_point at offset {record_idx} is before "
        f"Selfmod merge-back ({mergeback_idx})"
    )


@pytest.mark.xfail(strict=True, reason="AC-4: ledger_record_point in gate-first not yet added")
def test_call_order_record_point_in_gate_first_after_shipped_echo() -> None:
    """AC-4: a ``ledger_record_point`` call sits inside
    ``attempt_gate_first_fast_path`` after the ``shipped by the driver`` echo.
    """
    text = _RUNNER.read_text(encoding="utf-8")

    gate_first_start = text.find("attempt_gate_first_fast_path")
    assert gate_first_start != -1, "attempt_gate_first_fast_path not found"
    gate_first_body = text[gate_first_start:]

    shipped_echo = gate_first_body.find("shipped by the driver")
    assert shipped_echo != -1, "'shipped by the driver' echo not found"
    record_idx = gate_first_body.find("ledger_record_point")
    assert record_idx != -1, "ledger_record_point not found in gate-first"

    assert record_idx > shipped_echo, (
        f"ledger_record_point at offset {record_idx} is before "
        f"'shipped by the driver' ({shipped_echo})"
    )


# ── AC-5: contracts doc has suite_ledger.py section with forbidden pattern ───


@pytest.mark.xfail(strict=True, reason="AC-5: contracts doc section not yet added")
def test_contracts_doc_has_suite_ledger_section() -> None:
    """AC-5: the contracts doc has a heading containing ``suite_ledger.py``
    and the string ``run_ilk_loop`` appears in that section as the forbidden
    pattern.
    """
    text = _CONTRACTS.read_text(encoding="utf-8")

    # Find a heading containing suite_ledger.py.
    import re
    heading_pattern = re.compile(r"^##+ .*suite_ledger\.py.*$", re.MULTILINE)
    match = heading_pattern.search(text)
    assert match is not None, (
        "no heading containing 'suite_ledger.py' found in contracts doc"
    )

    # Find the next heading after this one to delimit the section.
    next_heading = re.compile(r"^##+ ", re.MULTILINE)
    next_match = next_heading.search(text, match.end())
    section_end = next_match.start() if next_match else len(text)
    section = text[match.start():section_end]

    assert "run_ilk_loop" in section, (
        "'run_ilk_loop' not found in suite_ledger.py contract section"
    )