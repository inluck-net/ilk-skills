"""Sub-plan ``triage-reads-the-newest-record`` step 0 — red-first pins.

Part of MASTER-2026-10-10c (``a-verify-fits-inside-its-gate``), order 4,
row ``backlog-1f31971e202a9f00`` part 2.  Retro root cause 5, third bullet:
"triage escalated on a stale finding instead of the newest record".

Today ``build_evidence`` (``ilk_triage.py``) reads the sub-plan's
``## Findings`` and nothing else — a Findings entry written three attempts
ago reads exactly as fresh evidence.  The contract:

1. ``build_evidence`` adds ``evidence["verification_record"]`` when the
   stopped sub-plan is a ``batch_verification: true`` sub-plan:
   ``{"path", "mtime" (ISO), "attempts" (history row count), "attributed"
   (count of At-base rows whose attributed cell is yes)}``.
   Missing record -> ``missing_sources`` gains ``"verification record"``.
2. The prompt carries the line
   ``Newest verification record: attempt <n>, <mtime>, <a> attributed row(s).
   If it is newer than the Findings, it governs.``
3. ``decide`` post-processes a ``park-and-escalate`` result: when a record is
   present, append ``" [newest verification record: attempt <n>, <a>
   attributed]"`` to ``finding`` (and ``reason`` if present).  Deterministic,
   so it holds whatever the model wrote.

Records live at ``<data_dir>/logs/verification/batch-<batch>-batch.md`` with
attempt history in ``batch-<batch>-batch.history.jsonl`` (one JSON row per
attempt).  ``<batch>`` is the MASTER filename stem minus ``MASTER-`` and
``-execution-plan``.  The fixture record is deliberately **unsigned** (no
``record_writer:`` line) so the parse path is the one the design names —
``verify_attribution.attributed_rows``, the final ``attributed`` cell — and
not the signed ``derive_attributed`` re-derivation.

The pins (step 0 red-first; the xfails were removed when step 1 built the
behaviour):

AC-1 (read)    — Findings say "none fixable in scope" (written earlier) and a
                 newer record has 2 attributed rows at attempt 2:
                 ``verification_record.attributed == 2`` and ``attempts == 2``.
AC-2 (quote)   — the prompt passed to the stub carries the designed line, i.e.
                 ``attempt 2`` and ``2 attributed``.
AC-3 (append)  — a stubbed ``park-and-escalate`` decision's ``finding`` ends
                 with ``[newest verification record: attempt 2, 2 attributed]``.
AC-4 (scope)   — a non-verification sub-plan has no ``verification_record``
                 key and no new missing-source entry, even with a decoy record
                 on disk.  Never red at base (the key did not exist, so its
                 absence was vacuous) — unpinned as the design's scoping
                 falsifier, per the a-slow-verify-row-outranks-features
                 convention: say so here rather than claim a red.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

RUN_ID = "R"

# MASTER stem minus `MASTER-` and `-execution-plan`.
BATCH = "2026-01-15c-fixture-batch"
MASTER_NAME = f"MASTER-{BATCH}-execution-plan.md"
SUBPLAN_NAME = f"{BATCH}-verify.md"
SUBPLAN_SLUG = "fixture-batch-verify"
RECORD_NAME = f"batch-{BATCH}-batch.md"
HISTORY_NAME = f"batch-{BATCH}-batch.history.jsonl"

PLAIN_BATCH = "2026-01-16c-fixture-plain"
PLAIN_MASTER_NAME = f"MASTER-{PLAIN_BATCH}-execution-plan.md"
PLAIN_SUBPLAN_NAME = f"{PLAIN_BATCH}.md"
PLAIN_SUBPLAN_SLUG = "fixture-plain"
PLAIN_RECORD_NAME = f"batch-{PLAIN_BATCH}-batch.md"


# ── env pin: keep every write inside the tmp data home ───────────────────────


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME / ILK_DATA_HOME / ILK_DATA_DIR to tmp_path.

    Cross-cutting rule for this MASTER: tests are hermetic and never read or
    write the real ``~/.ilk-data``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "ilk-data"))
    monkeypatch.setenv("ILK_DATA_DIR", str(tmp_path / "ilk-data"))


# -- helpers ------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: dict) -> None:
    _write(path, json.dumps(obj))


def _write_master(plans_dir: Path, name: str, batch: str, subplan_name: str) -> None:
    _write(
        plans_dir / name,
        "---\n"
        f"master_plan: {batch}\n"
        "batch_date: 2026-01-15\n"
        "status: active\n"
        f"current_subplan: {Path(subplan_name).stem}\n"
        "---\n\n"
        f"# MASTER plan: {batch}\n\n"
        "## Sub-plan registry\n\n"
        "| # | Order | Slug | Items | Steps (est.) | Status |\n"
        "|---|---|---|---|---|---|\n"
        f"| 1 | 0 | [{subplan_name}](./{subplan_name}) | x | 2 | in-progress |\n",
    )


def _write_batch_subplan(plans_dir: Path) -> None:
    """The stopped sub-plan is itself the ``batch_verification: true`` one."""
    _write(
        plans_dir / SUBPLAN_NAME,
        "---\n"
        f"plan: {SUBPLAN_SLUG}\n"
        "batch_verification: true\n"
        "status: in-progress\n"
        "---\n\n"
        "# Sub-plan: fixture batch verify\n\n"
        f"Part of [{MASTER_NAME}](./{MASTER_NAME}).\n\n"
        "## Findings\n\n"
        "none fixable in scope\n",
    )


def _write_plain_subplan(plans_dir: Path) -> None:
    """A work sub-plan — no ``batch_verification`` key at all."""
    _write(
        plans_dir / PLAIN_SUBPLAN_NAME,
        "---\n"
        f"plan: {PLAIN_SUBPLAN_SLUG}\n"
        "status: in-progress\n"
        "---\n\n"
        "# Sub-plan: fixture plain work\n\n"
        f"Part of [{PLAIN_MASTER_NAME}](./{PLAIN_MASTER_NAME}).\n\n"
        "## Findings\n\n"
        "none fixable in scope\n",
    )


def _write_record(vdir: Path, name: str, *, attributed_yes: int = 2) -> None:
    """An UNSIGNED batch record: no ``record_writer:`` line.

    The table is the four-column shape whose final cell is ``attributed``.
    Three data rows: ``attributed_yes`` marked yes, the rest no — so a
    counter that returns ``len(rows)`` rather than the yes-count is caught.
    """
    yes_rows = [
        f"| tests/test_alpha.py::test_{i} | passed | no | yes |"
        for i in range(attributed_yes)
    ]
    no_rows = [
        "| tests/test_gamma.py::test_three | failed | yes | no |",
    ]
    table = "\n".join(yes_rows + no_rows)
    _write(
        vdir / name,
        f"# Batch verify: {name[:-len('-batch.md')]}\n\n"
        "suite_failed: 3\n\n"
        "## At-base rerun\n\n"
        "Base: 0000000 · worktree: detached · command: python3 -m pytest "
        "<failing node ids>\n\n"
        "| node id | at base | in baseline_red | attributed |\n"
        "|---|---|---|---|\n"
        f"{table}\n\n"
        "## Findings\n\n"
        "n/a\n",
    )


def _write_history(vdir: Path, name: str, attempts: int = 2) -> None:
    rows = [
        json.dumps({"attempt": n, "digest": f"d{n}", "head": f"{n:07d}"})
        for n in range(1, attempts + 1)
    ]
    _write(vdir / name, "\n".join(rows) + "\n")


def _stamp_record_newer_than_findings(data_dir: Path) -> None:
    """Keep the fixture's narrative honest: the record is newer than Findings.

    Not asserted by any AC — design 2 asks the *model* to compare recency —
    but a fixture that lies about the ordering is not the fixture the AC
    describes.
    """
    import os as _os
    import time as _time

    plan = data_dir / "plans" / SUBPLAN_NAME
    record = data_dir / "logs" / "verification" / RECORD_NAME
    older = _time.time() - 3600
    _os.utime(plan, (older, older))


def _build_batch_fixture(data_dir: Path) -> Path:
    """A tmp data dir whose stopped sub-plan is ``batch_verification: true``.

    Lays down everything a reasonable derivation needs to find the record:
    the MASTER filename (the design's batch stem), the sub-plan's ``Part of``
    link, the MASTER registry row, and ``current_subplan``.
    """
    plans_dir = data_dir / "plans"
    vdir = data_dir / "logs" / "verification"
    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": "local_checks_failed",
        "run_id": RUN_ID,
        "failed_check": {"slug": SUBPLAN_SLUG, "step": 1, "command": "pytest"},
        "project_path": str(data_dir),
    })
    _write_master(plans_dir, MASTER_NAME, BATCH, SUBPLAN_NAME)
    _write_batch_subplan(plans_dir)
    _write_record(vdir, RECORD_NAME, attributed_yes=2)
    _write_history(vdir, HISTORY_NAME, attempts=2)
    _stamp_record_newer_than_findings(data_dir)
    return data_dir


def _build_plain_fixture(data_dir: Path) -> Path:
    """A tmp data dir whose stopped sub-plan is NOT a verification sub-plan.

    A decoy record sits on disk anyway: the scoping rule has to be the thing
    that keeps it out of the evidence, not the record's absence.
    """
    plans_dir = data_dir / "plans"
    vdir = data_dir / "logs" / "verification"
    _write_json(data_dir / "runtime" / "launcher" / "last-exit.json", {
        "state": "local_checks_failed",
        "run_id": RUN_ID,
        "failed_check": {"slug": PLAIN_SUBPLAN_SLUG, "step": 0, "command": "pytest"},
        "project_path": str(data_dir),
    })
    _write_master(plans_dir, PLAIN_MASTER_NAME, PLAIN_BATCH, PLAIN_SUBPLAN_NAME)
    _write_plain_subplan(plans_dir)
    _write_record(vdir, PLAIN_RECORD_NAME, attributed_yes=2)
    _write_history(vdir, f"batch-{PLAIN_BATCH}-batch.history.jsonl", attempts=2)
    return data_dir


def _stub_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    decision: dict,
    argv_file: Path,
) -> None:
    """A stub ``claude`` that records argv and returns *decision* as stream-json.

    The decision travels through a sidecar file so nothing has to survive a
    Python ``repr`` round-trip into the generated stub source.
    """
    decision_file = tmp_path / "decision.json"
    decision_file.write_text(json.dumps(decision), encoding="utf-8")
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    stub = stub_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(argv_file)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init', "
        "'model': 'claude-opus-test'}))\n"
        f"print(json.dumps({{'type': 'result', 'result': "
        f"open({str(decision_file)!r}).read()}}))\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir) + ":" + os.environ.get("PATH", ""))


def _prompt_from_argv(argv_file: Path) -> str:
    """The prompt is the token right after ``-p`` (see decide's argv order)."""
    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv[0] == "-p", argv[:3]
    return argv[1]


# ── AC-1: build_evidence reads the newest record ─────────────────────────────


def test_ac1_build_evidence_reads_the_newest_record(tmp_path: Path):
    """Findings say "none fixable in scope"; the newer record is attempt 2
    with 2 attributed rows.  build_evidence must report both counts."""
    from ilk_triage import build_evidence

    data_dir = _build_batch_fixture(tmp_path / "fixture")
    evidence = build_evidence(data_dir, RUN_ID)

    assert "none fixable in scope" in evidence["subplan_findings"]
    assert evidence["verification_record"]["attributed"] == 2
    assert evidence["verification_record"]["attempts"] == 2


# ── AC-2: the prompt quotes the record ───────────────────────────────────────


def test_ac2_prompt_quotes_the_newest_record(tmp_path: Path, monkeypatch):
    """The prompt passed to claude carries the designed line, so the model is
    told which evidence is newest rather than left with the stale Findings."""
    from ilk_triage import build_evidence, decide

    data_dir = _build_batch_fixture(tmp_path / "fixture")
    evidence = build_evidence(data_dir, RUN_ID)

    argv_file = tmp_path / "argv.json"
    _stub_claude(
        tmp_path,
        monkeypatch,
        decision={
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "stale findings say none fixable in scope",
            "basis": "test basis",
            "falsifier": "test falsifier",
        },
        argv_file=argv_file,
    )
    decide(evidence, home=tmp_path / "home", timeout_s=10)

    prompt = _prompt_from_argv(argv_file)
    assert "attempt 2" in prompt
    assert "2 attributed" in prompt
    # The design binds the line's identity, not just two loose substrings.
    assert "Newest verification record: attempt 2" in prompt


# ── AC-3: a park-and-escalate finding carries the record ─────────────────────


def test_ac3_park_and_escalate_finding_carries_the_record(tmp_path: Path, monkeypatch):
    """decide appends the record suffix to a park-and-escalate finding
    deterministically, whatever the model wrote."""
    from ilk_triage import build_evidence, decide

    data_dir = _build_batch_fixture(tmp_path / "fixture")
    evidence = build_evidence(data_dir, RUN_ID)

    argv_file = tmp_path / "argv.json"
    _stub_claude(
        tmp_path,
        monkeypatch,
        decision={
            "action": "park-and-escalate",
            "slug": None,
            "step": None,
            "finding": "stale findings say none fixable in scope",
            "basis": "test basis",
            "falsifier": "test falsifier",
        },
        argv_file=argv_file,
    )
    decision = decide(evidence, home=tmp_path / "home", timeout_s=10)

    assert decision["action"] == "park-and-escalate"
    assert decision["finding"].endswith(
        "[newest verification record: attempt 2, 2 attributed]"
    )


# ── AC-4: a non-verification sub-plan is never given a record ────────────────
# PASSES AT BASE — unpinned on purpose.  See the module docstring.


def test_ac4_non_verification_subplan_has_no_record(tmp_path: Path):
    """The key is scoped to ``batch_verification: true`` sub-plans.  A decoy
    record on disk must not leak into a work sub-plan's evidence, and the
    missing-record entry must not be filed for one either."""
    from ilk_triage import build_evidence

    data_dir = _build_plain_fixture(tmp_path / "fixture")
    evidence = build_evidence(data_dir, RUN_ID)

    assert "verification_record" not in evidence
    assert not any(
        "verification record" in m for m in evidence["missing_sources"]
    ), evidence["missing_sources"]
