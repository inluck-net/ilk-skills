"""A postmortem from ilk2's JSON, and findings through ilk2 improvement add.

docs/07-retiring-v1.md §7.4.3 rebuilds ``ilk-feedback`` on ilk2's versioned
JSON so a postmortem never needs V1's run files.  ``postmortem.py`` reads
exactly three commands' worth of that JSON — ``report``, ``status`` and
``events`` — and writes Markdown, or files one improvement per non-clean label
through ``ilk2 improvement add``.

The tests drive it through a stub ``ilk2`` executable written on a temp path:
it prints fixture JSON per subcommand and records its own argv, so "collect
calls exactly the three commands" is measured, not asserted by inspection.
Nothing here reads V1's data home; one test sets ``HOME`` to a temp directory
and checks it stays empty apart from the postmortems root it was given.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTMORTEM_PY = REPO_ROOT / "skills" / "ilk2-feedback" / "scripts" / "postmortem.py"
CLASSIFY_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ilk2_feedback"

BATCH_ID = "b-synth-timeout-bound"
PROJECT = "synth-project"


def _load_postmortem():
    spec = importlib.util.spec_from_file_location(
        "ilk2_feedback_postmortem", POSTMORTEM_PY
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


postmortem_mod = _load_postmortem()
collect = postmortem_mod.collect
render = postmortem_mod.render
write = postmortem_mod.write
findings = postmortem_mod.findings


# ── the stub ilk2 ────────────────────────────────────────────────────────────

_STUB_SOURCE = r'''#!{interpreter}
"""Stub ilk2: print fixture JSON per subcommand, record argv, exit 0."""
import json
import os
import sys

fixtures = os.environ["ILK2_STUB_FIXTURES"]
record = os.environ["ILK2_STUB_RECORD"]

with open(record, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "improvement":
    sys.exit(0)
files = {{"report": "report.json", "status": "status.json", "events": "events.json"}}
if cmd not in files:
    sys.stderr.write("stub ilk2: unknown command %r\n" % (cmd,))
    sys.exit(2)
with open(os.path.join(fixtures, files[cmd]), encoding="utf-8") as fh:
    sys.stdout.write(fh.read())
'''


STATUS = {
    "batches": [
        {
            "id": BATCH_ID,
            "project": PROJECT,
            "status": "shipped",
            "terminal_reason": None,
            "blocked_reason": None,
            "consumer_ref": None,
            "plans": [
                {
                    "slug": "synth-timeout-bound",
                    "status": "red",
                    "repair_of": None,
                    "steps": [],
                }
            ],
            "refusals": [],
            "shipped_at": "2026-10-18T00:10:00Z",
        }
    ],
    "needs_human": [],
}

EVENTS = {
    "events": [
        {
            "cursor": 1,
            "kind": "submitted",
            "payload": {"by": "synth"},
            "at": "2026-10-18T00:00:00Z",
        },
        {
            "cursor": 2,
            "kind": "submit.warning",
            "payload": {"warning": "files-per-step", "detail": "7.0 files per step"},
            "at": "2026-10-18T00:00:01Z",
        },
        {
            "cursor": 3,
            "kind": "batch.shipped",
            "payload": {},
            "at": "2026-10-18T00:10:00Z",
        },
    ]
}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def stub_env(tmp_path, monkeypatch):
    """A stub ``ilk2`` on a temp path, plus the dirs it reads and records to.

    Fixture JSON and the argv record live beside ``HOME``, not under it, so a
    test can assert that ``HOME`` itself stays untouched.
    """
    home = tmp_path / "home"
    fixtures = tmp_path / "stub-fixtures"
    bin_dir = tmp_path / "bin"
    for directory in (home, fixtures, bin_dir):
        directory.mkdir()

    _write_json(fixtures / "status.json", STATUS)
    _write_json(fixtures / "events.json", EVENTS)

    stub = bin_dir / "ilk2"
    interpreter = sys.executable or "/usr/bin/env python3"
    stub.write_text(_STUB_SOURCE.format(interpreter=interpreter), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    record = tmp_path / "argv.jsonl"
    record.write_text("", encoding="utf-8")
    monkeypatch.setenv("ILK2_STUB_FIXTURES", str(fixtures))
    monkeypatch.setenv("ILK2_STUB_RECORD", str(record))
    monkeypatch.setenv("HOME", str(home))
    # Apple's CPython writes a .pyc compile cache under
    # ~/Library/Caches/com.apple.python/ on interpreter *startup* (measured:
    # `python3 -c pass` with HOME set creates it).  Suppress bytecode so the
    # HOME assertions measure this code, not CPython's.
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return {
        "stub": str(stub),
        "record": record,
        "fixtures": fixtures,
        "home": home,
        "tmp_path": tmp_path,
    }


def _use_report(stub_env, name: str) -> None:
    """Point the stub's report subcommand at one of the classify fixtures."""
    text = (CLASSIFY_FIXTURES / name).read_text(encoding="utf-8")
    # keep the batch id the test asserts on
    payload = json.loads(text)
    payload["batch"] = BATCH_ID
    _write_json(Path(stub_env["fixtures"]) / "report.json", payload)


def _recorded(stub_env) -> list:
    lines = Path(stub_env["record"]).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ── collect ──────────────────────────────────────────────────────────────────


def test_collect_calls_exactly_the_three_commands(stub_env) -> None:
    _use_report(stub_env, "timeout_bound.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    assert _recorded(stub_env) == [
        ["report", "--batch", BATCH_ID, "--json"],
        ["status", "--batch", BATCH_ID, "--json"],
        ["events", "--batch", BATCH_ID, "--json"],
    ]
    assert collected["batch_id"] == BATCH_ID
    assert collected["report"]["report_version"] == 2
    assert collected["status"]["batches"][0]["project"] == PROJECT
    assert len(collected["events"]["events"]) == 3


def test_collect_runs_nothing_when_the_ilk2_is_not_called(stub_env) -> None:
    """The record is the search space: 0 commands before collect is called."""
    assert _recorded(stub_env) == []


# ── render ───────────────────────────────────────────────────────────────────


def test_render_carries_the_frontmatter_and_labels(stub_env) -> None:
    _use_report(stub_env, "timeout_bound.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    text = render(collected)
    assert text.startswith("---\n")
    front = text.split("---\n")[1]
    for expected in (
        "batch: %s" % BATCH_ID,
        "project: %s" % PROJECT,
        "status: shipped",
        "labels:",
        "  - timeout-bound",
        "minutes_per_accepted_step: null",
        "first_attempt_share: null",
        "report_version: 2",
    ):
        assert expected in front, front
    assert "## Classification" in text
    assert "**timeout-bound**" in text
    assert "synth-timeout-bound step 1 attempt 1" in text


def test_render_measures_each_step_and_keeps_unknown_unknown(stub_env) -> None:
    _use_report(stub_env, "timeout_bound.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    text = render(collected)
    # step 1 of the fixture: 60 min, 1 attempt, 1 red, first edit at 3 min,
    # 2 long requests, longest request 11 min.
    assert (
        "| synth-timeout-bound step 1 | 60.0 | 1 | 1 | 3.0 | 2 | 11.0 |" in text
    ), text


def test_null_timing_renders_unknown_not_zero(stub_env) -> None:
    """Pre-report_version 2 attempts carry null timing: unknown, never 0."""
    _use_report(stub_env, "null_timing.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    text = render(collected)
    rows = [line for line in text.splitlines() if line.startswith("| ")]
    step_rows = [r for r in rows if "step 1" in r]
    assert step_rows, text
    cells = [cell.strip() for cell in step_rows[0].split("|")]
    # | name | minutes | attempts | red | first edit | long requests | longest |
    assert cells[6] == "unknown", step_rows[0]
    assert cells[7] == "unknown", step_rows[0]
    assert "unknown" in text
    assert "0.0" not in step_rows[0]


def test_render_reports_phase_minutes_refusals_and_submit_warnings(stub_env) -> None:
    _use_report(stub_env, "timeout_bound.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    text = render(collected)
    assert "## Phase minutes" in text
    assert "| dispatch | 1.0 |" in text
    assert "## Refusals" in text
    assert "0 refusals recorded in this report." in text
    assert "## Submit warnings" in text
    assert "files-per-step" in text
    assert "7.0 files per step" in text
    # 3 events, 1 of them a submit warning: no zero-warning line
    assert "0 submit warnings in 3 event(s) for this batch." not in text


def test_a_batch_with_no_refusal_or_warning_says_zero_with_its_denominator(
    stub_env,
) -> None:
    _use_report(stub_env, "clean.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    events = collected["events"]
    events["events"] = [e for e in events["events"] if e["kind"] != "submit.warning"]
    text = render(collected)
    assert "0 refusals recorded in this report." in text
    assert "0 submit warnings in 2 event(s) for this batch." in text


# ── write ────────────────────────────────────────────────────────────────────


def test_write_lands_under_the_root_and_is_idempotent(stub_env) -> None:
    _use_report(stub_env, "timeout_bound.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    root = Path(stub_env["tmp_path"]) / "out"

    path = write(collected, root=root)
    assert path == root / PROJECT / ("%s.md" % BATCH_ID)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == render(collected)

    again = write(collected, root=root)
    assert again == path
    on_disk = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert on_disk == ["%s/%s.md" % (PROJECT, BATCH_ID)], on_disk


# ── findings ─────────────────────────────────────────────────────────────────


def test_findings_builds_the_argv_and_runs_nothing_without_apply(stub_env) -> None:
    _use_report(stub_env, "timeout_bound.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    argvs = findings(collected, ilk2=stub_env["stub"], apply=False)

    assert len(argvs) == 1
    argv = argvs[0]
    assert argv[0] == stub_env["stub"]
    assert argv[1:5] == ["improvement", "add", "--project", PROJECT]
    assert argv[5:9] == ["--source", "postmortem", "--fingerprint", "timeout-bound:%s" % PROJECT]
    assert argv[9] == "--title"
    assert argv[10] == "timeout-bound on batch %s" % BATCH_ID
    assert argv[11] == "--evidence"
    payload = json.loads(argv[12])
    assert payload["label"] == "timeout-bound"
    assert payload["project"] == PROJECT
    assert payload["batch"] == BATCH_ID
    assert payload["evidence"] == ["synth-timeout-bound step 1 attempt 1"]

    # apply=False: the stub has only seen the three collect commands
    assert [args[0] for args in _recorded(stub_env)] == ["report", "status", "events"]


def test_findings_apply_runs_the_commands(stub_env) -> None:
    _use_report(stub_env, "timeout_bound.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    findings(collected, ilk2=stub_env["stub"], apply=True)

    commands = [args[0] for args in _recorded(stub_env)]
    assert commands == ["report", "status", "events", "improvement"]
    improvement = _recorded(stub_env)[3]
    assert improvement[:3] == ["improvement", "add", "--project"]


def test_findings_skip_the_clean_label(stub_env) -> None:
    _use_report(stub_env, "clean.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    assert findings(collected, ilk2=stub_env["stub"], apply=True) == []
    assert [args[0] for args in _recorded(stub_env)] == ["report", "status", "events"]


def test_findings_file_one_row_per_failure_label(stub_env) -> None:
    """A batch with several labels produces one argv each, minus clean."""
    _use_report(stub_env, "timeout_bound.json")
    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    report = collected["report"]
    step = report["steps"][0]
    step["attempt_records"].append(
        {
            "n": 2,
            "outcome": "api",
            "reason": "upstream 503",
            "termination": "api",
            "gates_first": False,
            "minutes": 1.0,
            "edits": {},
            "requests": 1,
            "long_requests": None,
            "long_request_min": None,
            "longest_request_min": None,
            "model_min": None,
            "tool_min": None,
            "first_edit_start_min": None,
            "first_edit_end_min": None,
            "gates": {},
        }
    )
    argvs = findings(collected, ilk2=stub_env["stub"], apply=False)
    labels = [
        json.loads(argv[12])["label"]
        for argv in argvs
    ]
    assert labels == ["timeout-bound", "api-flaky"]


# ── nothing reaches V1's data home ───────────────────────────────────────────


def test_nothing_touches_the_v1_data_home(stub_env) -> None:
    """HOME is a temp dir; apart from the postmortems root we pass, it stays empty."""
    _use_report(stub_env, "timeout_bound.json")
    home = Path(stub_env["home"])
    assert list(home.iterdir()) == [], [p.name for p in home.iterdir()]

    collected = collect(BATCH_ID, ilk2=stub_env["stub"])
    render(collected)
    root = home / "postmortems"
    path = write(collected, root=root)
    findings(collected, ilk2=stub_env["stub"], apply=True)

    assert path == root / PROJECT / ("%s.md" % BATCH_ID)
    assert path.is_file()
    entries = sorted(p.name for p in home.iterdir())
    assert entries == ["postmortems"], entries
    assert not (home / ".ilk-data").exists()
    assert not (home / ".ilk2").exists()
    assert not (home / "Library").exists()


def test_postmortem_source_reads_no_v1_home() -> None:
    """Same guard as the classifier: code (not prose) names no V1/ilk2 run path."""
    source = POSTMORTEM_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or ""
    code = source.replace(module_doc, "", 1)
    code = "\n".join(
        line for line in code.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in (".ilk-data", "ilk2/data/runs", "ILK_DATA_HOME", "ILK_DATA_DIR"):
        assert forbidden not in code, "code mentions %s" % forbidden


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_main_prints_the_postmortem_and_only_writes_when_asked(stub_env, capsys) -> None:
    _use_report(stub_env, "timeout_bound.json")
    home = Path(stub_env["home"])

    rc = postmortem_mod.main([BATCH_ID, "--ilk2", stub_env["stub"]])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.startswith("---\n")
    assert "timeout-bound" in out.out
    assert out.err == ""
    # --write was not passed: HOME still holds nothing at all
    assert list(home.iterdir()) == [], [p.name for p in home.iterdir()]

    rc = postmortem_mod.main(
        [BATCH_ID, "--ilk2", stub_env["stub"], "--write", "--findings"]
    )
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.startswith("---\n")
    # --write uses the default root, which is HOME/.ilk2/postmortems
    default_root = home / ".ilk2" / "postmortems"
    assert (default_root / PROJECT / ("%s.md" % BATCH_ID)).is_file()
    assert sorted(p.name for p in home.iterdir()) == [".ilk2"]
    assert "wrote " in out.err
    # --findings without --apply prints the plan and runs nothing
    assert "findings: would run" in out.err
    assert "--fingerprint timeout-bound:%s" % PROJECT in out.err
    # two main() calls, so two rounds of collect; never an improvement add
    commands = [args[0] for args in _recorded(stub_env)]
    assert commands == ["report", "status", "events"] * 2, commands
    assert "improvement" not in commands
