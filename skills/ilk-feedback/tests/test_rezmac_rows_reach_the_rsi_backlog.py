"""Rezmac's backlog rows reach the RSI host's backlog.

Sub-plan rezmac-rows-reach-the-rsi-backlog.

The backlog is per host. chad-mbp's autoplan reads only its own
`candidates.json`. This module pulls rezmac's rows over ssh, merges
them tagged with `origin_host`, and writes the local file only when
both sides parsed.

AC-1..AC-8 test the merge and pull logic.  AC-9 tests the scheduler hook
(stays xfail until step 2).

HOME and ILK_DATA_HOME are both pinned to tmp_path (§23 half-pinned trap).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
for _p in (
    _HERE.parent.parent / "scripts",
    _HERE.parent.parent.parent / "ilk-watchdog" / "scripts",
    _HERE.parent.parent.parent / "ilk-self-improve" / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def backlog_dir(tmp_path):
    """Isolated backlog directory with an empty candidates.json."""
    d = tmp_path / "backlog"
    d.mkdir()
    return d


@pytest.fixture()
def state_dir(tmp_path):
    """Isolated throttle-state directory."""
    d = tmp_path / "state"
    d.mkdir()
    return d


def _write_candidates(backlog_dir: Path, entries: list[dict]) -> None:
    p = backlog_dir / "candidates.json"
    p.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")


def _read_candidates(backlog_dir: Path) -> list[dict]:
    p = backlog_dir / "candidates.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


# ── AC-1: merge adds ────────────────────────────────────────────────────────


def test_ac1_merge_adds_remote_rows(backlog_dir):
    """With local [a] and remote [a', b] (a' has same id as a but different
    status and seen_count 9), merge_remote returns added == [b.id].
    b carries relations.origin_host == "rezmac". a keeps its local status
    and seen_count (no origin_host), and a.relations.seen_on_hosts == ["rezmac"]."""
    import backlog_sync

    local = [
        {
            "id": "aaa",
            "title": "local title",
            "kind": "bug",
            "gap": "local gap",
            "evidence": {},
            "proposed_fix": "",
            "leverage": "low",
            "severity": "low",
            "status": "open",
            "first_seen": "2026-10-01T00:00:00+00:00",
            "last_seen": "2026-10-01T00:00:00+00:00",
            "seen_count": 1,
            "source": "",
            "source_id": "",
            "relations": {},
        },
    ]
    remote = [
        {
            "id": "aaa",
            "title": "remote title",
            "kind": "bug",
            "gap": "remote gap",
            "evidence": {"host": "rezmac"},
            "proposed_fix": "",
            "leverage": "low",
            "severity": "low",
            "status": "shipped",
            "first_seen": "2026-10-01T00:00:00+00:00",
            "last_seen": "2026-10-05T00:00:00+00:00",
            "seen_count": 9,
            "source": "",
            "source_id": "",
            "relations": {},
        },
        {
            "id": "bbb",
            "title": "remote only",
            "kind": "bug",
            "gap": "remote only gap",
            "evidence": {"host": "rezmac"},
            "proposed_fix": "",
            "leverage": "low",
            "severity": "low",
            "status": "open",
            "first_seen": "2026-10-02T00:00:00+00:00",
            "last_seen": "2026-10-02T00:00:00+00:00",
            "seen_count": 1,
            "source": "",
            "source_id": "",
            "relations": {},
        },
    ]

    merged, added, refreshed = backlog_sync.merge_remote(
        local, remote, host="rezmac",
    )
    assert added == ["bbb"], f"added: {added}"

    # b is appended with origin_host
    b_entry = next(e for e in merged if e["id"] == "bbb")
    assert b_entry["relations"]["origin_host"] == "rezmac"

    # a keeps its local status and seen_count
    a_entry = next(e for e in merged if e["id"] == "aaa")
    assert a_entry["status"] == "open", f"a status: {a_entry['status']}"
    assert a_entry["seen_count"] == 1, f"a seen_count: {a_entry['seen_count']}"
    assert "rezmac" in a_entry["relations"].get("seen_on_hosts", [])


# ── AC-2: merge refreshes rezmac rows only ──────────────────────────────────


def test_ac2_merge_refreshes_rezmac_rows(backlog_dir):
    """A local row with relations.origin_host == "rezmac" and seen_count 1,
    merged with a remote row of the same id with seen_count 3 and
    status "shipped", ends with seen_count == 3 and local status unchanged."""
    import backlog_sync

    local = [
        {
            "id": "aaa",
            "title": "old title",
            "kind": "bug",
            "gap": "old gap",
            "evidence": {},
            "proposed_fix": "",
            "leverage": "low",
            "severity": "low",
            "status": "open",
            "first_seen": "2026-10-01T00:00:00+00:00",
            "last_seen": "2026-10-01T00:00:00+00:00",
            "seen_count": 1,
            "source": "",
            "source_id": "",
            "relations": {"origin_host": "rezmac"},
        },
    ]
    remote = [
        {
            "id": "aaa",
            "title": "new title",
            "kind": "bug",
            "gap": "new gap",
            "evidence": {"host": "rezmac"},
            "proposed_fix": "",
            "leverage": "low",
            "severity": "low",
            "status": "shipped",
            "first_seen": "2026-10-01T00:00:00+00:00",
            "last_seen": "2026-10-05T00:00:00+00:00",
            "seen_count": 3,
            "source": "",
            "source_id": "",
            "relations": {},
        },
    ]

    merged, added, refreshed = backlog_sync.merge_remote(
        local, remote, host="rezmac",
    )
    assert added == [], f"added: {added}"
    a_entry = merged[0]
    assert a_entry["seen_count"] == 3, f"seen_count: {a_entry['seen_count']}"
    assert a_entry["status"] == "open", f"status changed: {a_entry['status']}"
    assert a_entry["title"] == "new title", f"title not refreshed: {a_entry['title']}"


# ── AC-3: idempotent ────────────────────────────────────────────────────────


def test_ac3_merge_idempotent(backlog_dir):
    """Merging the same remote twice gives an equal list and an empty
    added the second time."""
    import backlog_sync

    local = [
        {
            "id": "aaa", "title": "t", "kind": "bug", "gap": "g",
            "evidence": {}, "proposed_fix": "", "leverage": "low",
            "severity": "low", "status": "open",
            "first_seen": "2026-10-01T00:00:00+00:00",
            "last_seen": "2026-10-01T00:00:00+00:00",
            "seen_count": 1, "source": "", "source_id": "",
            "relations": {},
        },
    ]
    remote = [
        {
            "id": "bbb", "title": "r", "kind": "bug", "gap": "rg",
            "evidence": {}, "proposed_fix": "", "leverage": "low",
            "severity": "low", "status": "open",
            "first_seen": "2026-10-02T00:00:00+00:00",
            "last_seen": "2026-10-02T00:00:00+00:00",
            "seen_count": 1, "source": "", "source_id": "",
            "relations": {},
        },
    ]

    m1, a1, _ = backlog_sync.merge_remote(local, remote, host="rezmac")
    m2, a2, _ = backlog_sync.merge_remote(m1, remote, host="rezmac")
    assert a1 == ["bbb"]
    assert a2 == [], f"second merge added: {a2}"
    assert len(m1) == len(m2)
    for e1, e2 in zip(m1, m2):
        assert e1 == e2


# ── AC-4: unreachable ≠ 0 rows ──────────────────────────────────────────────


def test_ac4_unreachable_returns_status_unreachable(backlog_dir, state_dir):
    """With an injected runner that returns exit 255, pull() returns
    status == "unreachable" with no remote_rows key. The local file is
    byte-equal before and after."""
    import backlog_sync

    _write_candidates(backlog_dir, [{"id": "local1", "kind": "bug"}])
    before = (backlog_dir / "candidates.json").read_bytes()

    def fake_runner(argv, **kw):
        class R:
            returncode = 255
            stdout = ""
            stderr = "Connection timed out"
        return R()

    result = backlog_sync.pull(
        host="rezmac",
        backlog_dir=backlog_dir,
        state_dir=state_dir,
        runner=fake_runner,
    )
    assert result["status"] == "unreachable", f"status: {result['status']}"
    assert "remote_rows" not in result

    after = (backlog_dir / "candidates.json").read_bytes()
    assert before == after, "file changed after unreachable"


# ── AC-5: unreadable ────────────────────────────────────────────────────────


def test_ac5_unreadable_remote_body(backlog_dir, state_dir):
    """A remote body '{oops' gives status == 'unreadable'. The local file
    is byte-equal before and after."""
    import backlog_sync

    _write_candidates(backlog_dir, [{"id": "local1", "kind": "bug"}])
    before = (backlog_dir / "candidates.json").read_bytes()

    def fake_runner(argv, **kw):
        class R:
            returncode = 0
            stdout = "rezmac\n{oops"
            stderr = ""
        return R()

    result = backlog_sync.pull(
        host="rezmac",
        backlog_dir=backlog_dir,
        state_dir=state_dir,
        runner=fake_runner,
    )
    assert result["status"] == "unreadable", f"status: {result['status']}"

    after = (backlog_dir / "candidates.json").read_bytes()
    assert before == after, "file changed after unreadable"


def test_ac5_unreadable_local_file(backlog_dir, state_dir):
    """A local candidates.json that is not a JSON list gives
    status == 'unreadable'."""
    import backlog_sync

    (backlog_dir / "candidates.json").write_text("{not a list}\n",
                                                 encoding="utf-8")

    def fake_runner(argv, **kw):
        class R:
            returncode = 0
            stdout = "rezmac\n[]"
            stderr = ""
        return R()

    result = backlog_sync.pull(
        host="rezmac",
        backlog_dir=backlog_dir,
        state_dir=state_dir,
        runner=fake_runner,
    )
    assert result["status"] == "unreadable", f"status: {result['status']}"


# ── AC-6: self ──────────────────────────────────────────────────────────────


def test_ac6_self_hostname_skips(backlog_dir, state_dir):
    """A runner whose first output line equals socket.gethostname() gives
    status == 'self' and no write."""
    import backlog_sync
    import socket

    _write_candidates(backlog_dir, [{"id": "local1", "kind": "bug"}])
    before = (backlog_dir / "candidates.json").read_bytes()

    def fake_runner(argv, **kw):
        class R:
            returncode = 0
            stdout = f"{socket.gethostname()}\n[]"
            stderr = ""
        return R()

    result = backlog_sync.pull(
        host="rezmac",
        backlog_dir=backlog_dir,
        state_dir=state_dir,
        runner=fake_runner,
    )
    assert result["status"] == "self", f"status: {result['status']}"

    after = (backlog_dir / "candidates.json").read_bytes()
    assert before == after, "file changed after self"


# ── AC-7: throttle ──────────────────────────────────────────────────────────


def test_ac7_throttle_prevents_second_pull(backlog_dir, state_dir):
    """A second pull() within 30 minutes of an ok returns throttled without
    invoking the runner. --force pulls."""
    import backlog_sync
    import time

    _write_candidates(backlog_dir, [])
    call_count = [0]

    def fake_runner(argv, **kw):
        call_count[0] += 1
        class R:
            returncode = 0
            stdout = 'rezmac\n[{"id":"r1","kind":"bug","title":"t","gap":"g","evidence":{},"proposed_fix":"","leverage":"low","severity":"low","status":"open","first_seen":"2026-10-01T00:00:00+00:00","last_seen":"2026-10-01T00:00:00+00:00","seen_count":1,"source":"","source_id":"","relations":{}}]'
            stderr = ""
        return R()

    now = time.time()
    r1 = backlog_sync.pull(
        host="rezmac", backlog_dir=backlog_dir, state_dir=state_dir,
        runner=fake_runner, now=now,
    )
    assert r1["status"] == "ok", f"first pull: {r1['status']}"
    assert call_count[0] == 1

    # Second pull within 30 min: throttled
    r2 = backlog_sync.pull(
        host="rezmac", backlog_dir=backlog_dir, state_dir=state_dir,
        runner=fake_runner, now=now + 60,
    )
    assert r2["status"] == "throttled", f"second pull: {r2['status']}"
    assert call_count[0] == 1, f"runner called {call_count[0]} times"

    # Force pull
    r3 = backlog_sync.pull(
        host="rezmac", backlog_dir=backlog_dir, state_dir=state_dir,
        runner=fake_runner, now=now + 60, force=True,
    )
    assert r3["status"] == "ok", f"force pull: {r3['status']}"
    assert call_count[0] == 2


# ── AC-8: argv ──────────────────────────────────────────────────────────────


def test_ac8_correct_ssh_argv(backlog_dir, state_dir):
    """The runner receives exactly the expected ssh argv."""
    import backlog_sync

    _write_candidates(backlog_dir, [])
    captured_argv = []

    def fake_runner(argv, **kw):
        captured_argv.extend(argv)
        class R:
            returncode = 0
            stdout = "rezmac\n[]"
            stderr = ""
        return R()

    backlog_sync.pull(
        host="rezmac", backlog_dir=backlog_dir, state_dir=state_dir,
        runner=fake_runner,
    )
    expected = [
        "ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
        "rezmac",
        "hostname; cat ~/.ilk-data/ilk-skills-improvements/candidates.json",
    ]
    assert captured_argv == expected, f"argv: {captured_argv}"


# ── AC-9: scheduler hook ────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="backlog_sync not built")
def test_ac9_scheduler_hook_returns_0_on_stub_exit_4(tmp_path):
    """The sync_remote_backlogs shell function, extracted from
    scheduler.sh, returns 0 when BACKLOG_SYNC_PY points at a stub that
    exits 4. It writes a backlog-sync scheduler log line containing
    'unreachable'."""
    import subprocess

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    scheduler_sh = repo_root / "skills" / "ilk-watchdog" / "scripts" / "scheduler.sh"

    # Write a stub that prints a JSON result with unreachable status and exits 4
    stub = tmp_path / "stub_backlog_sync.py"
    stub.write_text(
        'import json, sys\n'
        'print(json.dumps([{"host": "rezmac", "status": "unreachable"}]))\n'
        'sys.exit(4)\n',
        encoding="utf-8",
    )

    body = (
        f'BACKLOG_SYNC_PY="{stub}"\n'
        f'ilk_data_dir() {{ echo "{tmp_path}"; }}\n'
        f'write_scheduler_log() {{ echo "LOG: $@"; }}\n'
        f'sync_remote_backlogs\n'
        f'echo "exit=$?"\n'
    )
    prelude = f'SCHEDULER_SH="{scheduler_sh}"\n'
    prelude += "eval \"$(sed -n '/^sync_remote_backlogs()/,/^}/p' \"$SCHEDULER_SH\")\"\n"

    proc = subprocess.run(
        ["/bin/bash", "-c", prelude + body],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"shell function returned {proc.returncode}: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "exit=0" in proc.stdout, f"expected exit=0: {proc.stdout!r}"
    assert "backlog-sync" in proc.stdout, (
        f"expected backlog-sync log line: {proc.stdout!r}"
    )
    assert "unreachable" in proc.stdout, (
        f"expected unreachable in log: {proc.stdout!r}"
    )