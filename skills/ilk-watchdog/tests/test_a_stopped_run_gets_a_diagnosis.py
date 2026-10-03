"""Red-first tests for ilk_triage.py — a stopped run gets a diagnosis.

Covers AC-1..AC-5 from sub-plan a-stopped-run-gets-a-diagnosis:

AC-1: build_evidence on the replay fixture contains the failed check's
      command, the [DBG] before TERM: PGID= lines, and
      local_checks_failed_no_commits. Red-first.
AC-2: with a stub claude whose init model is claude-opus-test and whose
      result is a valid ack-and-relaunch JSON, decide returns it unchanged
      and the audit row records model: claude-opus-test. Red-first.
AC-3: each of these makes decide return park-and-escalate with a named
      reason: result "I think we should relaunch" (no JSON); two JSON
      objects; action: "rm -rf"; empty finding; init model mimo-v2.5-pro;
      the stub exits 1; the stub sleeps past timeout_s=2. One parametrised
      test. Red-first.
AC-4: triage.disabled present: exit 3, a triage-refused row, and the stub
      claude never ran (it touches a marker file; assert absent). Red-first.
AC-5 (control): a missing postmortem is listed under the evidence's missing
      sources, and decide still runs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure the scripts dirs are importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ilk-loop" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "triage" / "run-20261003-125807"


# ── AC-1: build_evidence on the replay fixture ──────────────────────────────


def test_build_evidence_contains_failed_check_command():
    """build_evidence must surface the failed check's command."""
    from ilk_triage import build_evidence

    evidence = build_evidence(FIXTURE_DIR, "20261003-125807")
    last_exit = evidence["last_exit"]
    assert last_exit["state"] == "local_checks_failed_no_commits"
    assert "pytest" in last_exit["failed_check"]["command"]
    assert "test_a_layout_switch_restarts_the_daemon" in last_exit["failed_check"]["command"]


def test_build_evidence_contains_dbg_lines():
    """build_evidence must surface [DBG] before TERM lines from the driver log."""
    from ilk_triage import build_evidence

    evidence = build_evidence(FIXTURE_DIR, "20261003-125807")
    driver_log = evidence["driver_log"]
    assert any("DBG" in line and "before TERM" in line for line in driver_log), \
        "driver_log must contain [DBG] before TERM lines"


def test_build_evidence_contains_local_checks_failed():
    """build_evidence must surface local_checks_failed_no_commits."""
    from ilk_triage import build_evidence

    evidence = build_evidence(FIXTURE_DIR, "20261003-125807")
    assert evidence["last_exit"]["state"] == "local_checks_failed_no_commits"


# ── AC-2: decide with valid ack-and-relaunch ────────────────────────────────


def test_decide_returns_valid_decision_and_records_model(tmp_path: Path, monkeypatch):
    """decide must return the manager's decision and record the init model."""
    from ilk_triage import decide, build_evidence

    # Create a stub claude that outputs a valid ack-and-relaunch decision
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub_claude = stub_dir / "claude"
    stub_claude.write_text(
        '#!/bin/bash\n'
        '# Stub claude for testing\n'
        'echo \'{"type":"system","subtype":"init","model":"claude-opus-test"}\'\n'
        'echo \'{"type":"result","result":"{\\\"action\\\":\\\"ack-and-relaunch\\\",'
        '\\\"slug\\\":null,\\\"step\\\":null,'
        '\\\"finding\\\":\\\"test finding\\\",'
        '\\\"basis\\\":\\\"test basis\\\",'
        '\\\"falsifier\\\":\\\"test falsifier\\\"}"}\'\n',
        encoding="utf-8"
    )
    stub_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir) + ":" + os.environ.get("PATH", ""))

    evidence = build_evidence(FIXTURE_DIR, "20261003-125807")
    decision = decide(evidence, home=tmp_path / "home", timeout_s=10)

    assert decision["action"] == "ack-and-relaunch"
    assert decision["finding"] == "test finding"
    assert decision["basis"] == "test basis"
    assert decision["falsifier"] == "test falsifier"
    assert decision["model"] == "claude-opus-test"


def test_decide_passes_the_prompt_before_any_variadic_flag(tmp_path: Path, monkeypatch):
    """The prompt must not follow ``--allowedTools``: the real CLI treats that
    option as variadic and swallows the prompt as a tool name, then exits 1
    with "Input must be provided" (measured live on v0.9.142, 2026-10-03:
    every manager-home diagnose ended park-and-escalate in 0.7 s)."""
    from ilk_triage import decide, build_evidence

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    argv_file = tmp_path / "argv.json"
    stub = stub_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(argv_file)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init', 'model': 'claude-opus-test'}))\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir) + ":" + os.environ.get("PATH", ""))

    evidence = build_evidence(FIXTURE_DIR, "20261003-125807")
    decide(evidence, home=tmp_path / "home", timeout_s=10)

    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv[0] == "-p"
    prompt_idx = 1
    assert not argv[prompt_idx].startswith("-"), argv[:3]
    assert "--allowedTools" in argv
    assert prompt_idx < argv.index("--allowedTools")


def test_triage_home_resolution_order(tmp_path: Path, monkeypatch):
    """Explicit --home wins, then ILK_TRIAGE_HOME, then ~/.claude-triage.
    Never the manager home: it runs glm-5.3 by design (model-worker-framework.md:54)
    and triage refuses worker-pattern models (2026-10-03)."""
    from ilk_triage import resolve_triage_home

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ILK_TRIAGE_HOME", raising=False)
    assert resolve_triage_home(None) == tmp_path / ".claude-triage"
    monkeypatch.setenv("ILK_TRIAGE_HOME", str(tmp_path / "env-home"))
    assert resolve_triage_home(None) == tmp_path / "env-home"
    assert resolve_triage_home(tmp_path / "explicit") == tmp_path / "explicit"


def test_run_triage_escalates_when_the_triage_home_is_missing(tmp_path: Path, monkeypatch):
    """A missing triage home is an escalation, never a fallback to another home."""
    import ilk_triage

    monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ILK_TRIAGE_HOME", str(tmp_path / "no-such-home"))
    (tmp_path / "data" / "projects" / "k" / "plans").mkdir(parents=True)
    import triage_apply
    monkeypatch.setattr(triage_apply, "ilk_notify", lambda **k: None)
    calls = []
    monkeypatch.setattr(ilk_triage, "decide", lambda *a, **k: calls.append(k) or {})
    result = ilk_triage.run_triage(project_key="k", run_id="r")
    assert calls == []
    assert result.get("action") == "park-and-escalate"
    assert result.get("reason") == "no_triage_home"


# ── AC-3: decide returns park-and-escalate for various failures ──────────────


@pytest.mark.parametrize("scenario,expected_reason", [
    ("no_json", "no_json"),           # result "I think we should relaunch"
    ("two_json", "two_json"),         # two JSON objects
    ("bad_action", "unknown_action"), # action: "rm -rf"
    ("empty_finding", "empty_finding"),  # empty finding
    ("mimo_model", "mimo_model"),     # init model mimo-v2.5-pro
    ("exit_1", "non_zero_exit"),      # stub exits 1
    ("timeout", "timeout"),           # stub sleeps past timeout_s=2
], ids=[
    "no_json", "two_json", "bad_action", "empty_finding",
    "mimo_model", "exit_1", "timeout"
])
def test_decide_returns_park_and_escalate_for_failures(
    tmp_path: Path, monkeypatch, scenario: str, expected_reason: str
):
    """decide must return park-and-escalate with a named reason for each failure mode."""
    from ilk_triage import decide, build_evidence

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub_claude = stub_dir / "claude"

    if scenario == "no_json":
        stub_claude.write_text(
            '#!/bin/bash\n'
            'echo \'{"type":"system","subtype":"init","model":"claude-opus-test"}\'\n'
            'echo \'{"type":"result","result":"I think we should relaunch"}\'\n',
            encoding="utf-8"
        )
    elif scenario == "two_json":
        stub_claude.write_text(
            '#!/bin/bash\n'
            'echo \'{"type":"system","subtype":"init","model":"claude-opus-test"}\'\n'
            'echo \'{"type":"result","result":"{\\"action\\":\\"reopen\\"} {\\"action\\":\\"amend\\"}"}\'\n',
            encoding="utf-8"
        )
    elif scenario == "bad_action":
        stub_claude.write_text(
            '#!/bin/bash\n'
            'echo \'{"type":"system","subtype":"init","model":"claude-opus-test"}\'\n'
            'echo \'{"type":"result","result":"{\\\"action\\\":\\\"rm -rf\\\",'
            '\\\"slug\\\":null,\\\"step\\\":null,'
            '\\\"finding\\\":\\\"f\\\",\\\"basis\\\":\\\"b\\\",\\\"falsifier\\\":\\\"fl\\\"}"}\'\n',
            encoding="utf-8"
        )
    elif scenario == "empty_finding":
        stub_claude.write_text(
            '#!/bin/bash\n'
            'echo \'{"type":"system","subtype":"init","model":"claude-opus-test"}\'\n'
            'echo \'{"type":"result","result":"{\\\"action\\\":\\\"ack-and-relaunch\\\",'
            '\\\"slug\\\":null,\\\"step\\\":null,'
            '\\\"finding\\\":\\\"\\\",\\\"basis\\\":\\\"b\\\",\\\"falsifier\\\":\\\"fl\\\"}"}\'\n',
            encoding="utf-8"
        )
    elif scenario == "mimo_model":
        stub_claude.write_text(
            '#!/bin/bash\n'
            'echo \'{"type":"system","subtype":"init","model":"mimo-v2.5-pro"}\'\n'
            'echo \'{"type":"result","result":"{\\\"action\\\":\\\"ack-and-relaunch\\\",'
            '\\\"slug\\\":null,\\\"step\\\":null,'
            '\\\"finding\\\":\\\"f\\\",\\\"basis\\\":\\\"b\\\",\\\"falsifier\\\":\\\"fl\\\"}"}\'\n',
            encoding="utf-8"
        )
    elif scenario == "exit_1":
        stub_claude.write_text(
            '#!/bin/bash\n'
            'echo \'{"type":"system","subtype":"init","model":"claude-opus-test"}\'\n'
            'exit 1\n',
            encoding="utf-8"
        )
    elif scenario == "timeout":
        stub_claude.write_text(
            '#!/bin/bash\n'
            'echo \'{"type":"system","subtype":"init","model":"claude-opus-test"}\'\n'
            'sleep 10\n',
            encoding="utf-8"
        )

    stub_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir) + ":" + os.environ.get("PATH", ""))

    evidence = build_evidence(FIXTURE_DIR, "20261003-125807")
    timeout = 2 if scenario == "timeout" else 600
    decision = decide(evidence, home=tmp_path / "home", timeout_s=timeout)

    assert decision["action"] == "park-and-escalate", \
        f"scenario {scenario} must return park-and-escalate"
    assert decision["reason"] == expected_reason, \
        f"scenario {scenario} must name reason {expected_reason}"


# ── AC-4: triage.disabled kill switch ────────────────────────────────────────


def test_triage_disabled_exits_with_refused_row(tmp_path: Path, monkeypatch):
    """triage.disabled must exit 3, write a triage-refused row, and not run claude."""
    from ilk_triage import main

    # Set up ILK_DATA_HOME so the kill switch is found
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    (data_home / "triage.disabled").write_text("", encoding="utf-8")

    # Create a marker file that the stub claude would touch
    marker = tmp_path / "claude_ran"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub_claude = stub_dir / "claude"
    stub_claude.write_text(
        f'#!/bin/bash\ntouch {marker}\n',
        encoding="utf-8"
    )
    stub_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir) + ":" + os.environ.get("PATH", ""))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))

    with pytest.raises(SystemExit) as exc_info:
        main(["diagnose", "--project-key", "test", "--run-id", "20261003-125807",
              "--home", str(tmp_path / "home")])

    assert exc_info.value.code == 3, "must exit 3 when triage.disabled exists"
    assert not marker.exists(), "stub claude must not have run"


def test_triage_disabled_writes_refused_audit_row(tmp_path: Path, monkeypatch):
    """triage.disabled must write a triage-refused audit row."""
    from ilk_triage import main
    from ilk_audit import read_audit

    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    (data_home / "triage.disabled").write_text("", encoding="utf-8")

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub_claude = stub_dir / "claude"
    stub_claude.write_text('#!/bin/bash\nexit 0\n', encoding="utf-8")
    stub_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir) + ":" + os.environ.get("PATH", ""))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))

    with pytest.raises(SystemExit):
        main(["diagnose", "--project-key", "test", "--run-id", "20261003-125807",
              "--home", str(tmp_path / "home")])

    rows = read_audit(root=data_home)
    refused_rows = [r for r in rows if r["kind"] == "triage-refused"]
    assert len(refused_rows) == 1, "must write exactly one triage-refused row"


# ── AC-5 (control): missing postmortem is listed and decide still runs ───────


def test_missing_postmortem_is_listed_in_missing_sources(tmp_path: Path):
    """A missing postmortem must appear in evidence['missing_sources']."""
    from ilk_triage import build_evidence

    # Create a minimal fixture without the postmortem
    minimal_dir = tmp_path / "fixture"
    minimal_dir.mkdir()
    runtime_dir = minimal_dir / "runtime" / "launcher"
    runtime_dir.mkdir(parents=True)
    logs_dir = minimal_dir / "logs" / "runs" / "20261003-125807"
    logs_dir.mkdir(parents=True)

    # Copy last-exit.json and gate-history.jsonl but NOT the postmortem
    import shutil
    shutil.copy(FIXTURE_DIR / "runtime" / "launcher" / "last-exit.json", runtime_dir)
    shutil.copy(FIXTURE_DIR / "runtime" / "launcher" / "gate-history.jsonl", runtime_dir)

    evidence = build_evidence(minimal_dir, "20261003-125807")
    missing = evidence.get("missing_sources", [])
    assert any("postmortem" in m.lower() for m in missing), \
        "missing postmortem must be listed in missing_sources"


def test_missing_postmortem_does_not_block_decide(tmp_path: Path, monkeypatch):
    """decide must still run when the postmortem is missing."""
    from ilk_triage import decide, build_evidence

    # Create a minimal fixture without the postmortem
    minimal_dir = tmp_path / "fixture"
    minimal_dir.mkdir()
    runtime_dir = minimal_dir / "runtime" / "launcher"
    runtime_dir.mkdir(parents=True)

    import shutil
    shutil.copy(FIXTURE_DIR / "runtime" / "launcher" / "last-exit.json", runtime_dir)
    shutil.copy(FIXTURE_DIR / "runtime" / "launcher" / "gate-history.jsonl", runtime_dir)

    # Stub claude that returns a valid decision
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub_claude = stub_dir / "claude"
    stub_claude.write_text(
        '#!/bin/bash\n'
        'echo \'{"type":"system","subtype":"init","model":"claude-opus-test"}\'\n'
        'echo \'{"type":"result","result":"{\\\"action\\\":\\\"park-and-relaunch\\\",'
        '\\\"slug\\\":null,\\\"step\\\":null,'
        '\\\"finding\\\":\\\"missing postmortem\\\",'
        '\\\"basis\\\":\\\"no postmortem file\\\",'
        '\\\"falsifier\\\":\\\"postmortem appears\\\"}"}\'\n',
        encoding="utf-8"
    )
    stub_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub_dir) + ":" + os.environ.get("PATH", ""))

    evidence = build_evidence(minimal_dir, "20261003-125807")
    decision = decide(evidence, home=tmp_path / "home", timeout_s=10)

    assert decision["action"] == "park-and-escalate", \
        "decide must return a valid action even with missing postmortem"