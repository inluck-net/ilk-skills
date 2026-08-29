"""An iteration is recorded before it runs.

Sub-plan `2026-08-29c-an-iteration-is-recorded-before-it-runs`.

**The sub-plan's stated defect was measured and found already fixed.** It reads:

> The runner writes its JSONL summary line when an iteration *completes*
> (`run_ilk_loop_claude.sh:1449`). A `gtimeout`-killed iteration never reaches
> that write.

Four runs of the real runner under a real `gtimeout` kill, 2026-08-29:

    runner            tree at kill    .ilk-loop.log
    HEAD              clean           415 bytes, stop_reason=timeout
    HEAD              dirty           423 bytes, stop_reason=timeout
    f5674c6^          clean           396 bytes, stop_reason=timeout
    f5674c6^          dirty             0 bytes   <-- the rezmac failure

The write *is* reached on a `gtimeout` kill. The 0-byte log had a different
cause, fixed in ``f5674c6`` the same day: the WIP commit's stdout joined
``preserve_dirty_tree_on_timeout``'s return value, ``int()`` raised, and the
python block died before ``print(json.dumps(d))``. It reproduces only with a
dirty tree, which is why it looked like "killed iterations are never recorded".

**The gap that remains is a different one, and it is real.** ``gtimeout`` kills
the agent, not the runner, so the runner survives to write. Kill the *runner*
and nothing is written at all. Measured at HEAD, SIGKILL 10s into an iteration:

    .ilk-loop.log   NO-FILE  (zero records)
    sentinel        state: running   <-- stale-running crash artifact
    orphaned        gtimeout 120s claude -p ...  survived, reparented

That is the state an operator lands in after `stop.sh`, a machine dying, or a
`launchctl bootout` mid-iteration -- and it is exactly what was hit by hand on
2026-08-29. A record written *before* the work cannot be lost this way.

So these tests target the runner-kill gap, and additionally pin ``f5674c6`` so
the child-kill path cannot silently regress.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RUNNER = _REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"

_MASTER = """\
---
master_plan: 2026-08-29z-execution
batch_date: 2026-08-29
status: active
---
# MASTER
## Sub-plan registry
| # | Slug |
|---|---|
| 1 | [2026-08-29z-demo.md](./2026-08-29z-demo.md) |
"""

_SUBPLAN = """\
---
plan: demo
status: pending
current_step: 0
estimated_steps: 2
last_updated: 2026-08-29
---
# Sub-plan: demo
## Steps
### Step 0 — do a thing
- Commit: `chore: x [plan:demo#step-0]`
"""


class RunnerSandbox:
    """A real project the real runner can drive, with a stubbed agent.

    The agent is stubbed via PATH, not the runner's internals: the runner
    still spawns a child, still bounds it with the same `gtimeout` call, and
    still takes the same branches. Only the agent's *content* is fake, so a
    kill here is a real kill of a real iteration.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.proj = tmp_path / "proj"
        (self.proj / "docs" / "plans").mkdir(parents=True)
        self._git("init", "-q")
        self._git("-c", "user.email=t@e.com", "-c", "user.name=t",
                  "commit", "-q", "--allow-empty", "-m", "init")
        (self.proj / "docs" / "plans" / "MASTER-2026-08-29z-execution-plan.md").write_text(_MASTER)
        (self.proj / "docs" / "plans" / "2026-08-29z-demo.md").write_text(_SUBPLAN)
        self._git("add", "-A")
        self._git("-c", "user.email=t@e.com", "-c", "user.name=t",
                  "commit", "-q", "-m", "plans")
        self.bin = tmp_path / "bin"
        self.bin.mkdir()

    def _git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.proj, check=True, capture_output=True)

    def stub_agent(self, body: str) -> None:
        agent = self.bin / "claude"
        agent.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body), encoding="utf-8")
        agent.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "HOME": str(self.root),
            "ILK_DOTSOURCE_ONLY": "",
            **extra,
        }

    def argv(self, *extra: str) -> list[str]:
        return [
            "bash", str(RUNNER),
            "--project-path", str(self.proj),
            "--max-iterations", "1",
            "--iteration-timeout-min", "1",
            "--model", "test-model",
            *extra,
        ]

    # -- artifacts ---------------------------------------------------------

    def _find(self, name: str) -> Path | None:
        data = self.root / ".ilk-data"
        if not data.exists():
            return None
        hits = sorted(data.rglob(name))
        return hits[0] if hits else None

    def records(self) -> list[dict]:
        log = self._find(".ilk-loop.log")
        if log is None or not log.exists():
            return []
        out = []
        for line in log.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def sentinel(self) -> dict:
        s = self._find("last-exit.json")
        return json.loads(s.read_text(encoding="utf-8-sig")) if s else {}

    def ship_proof(self) -> list[dict]:
        led = self._find("ship-proof.jsonl")
        if led is None or not led.exists():
            return []
        return [json.loads(x) for x in led.read_text(encoding="utf-8-sig").splitlines() if x.strip()]


def _reap(sandbox: RunnerSandbox) -> None:
    """Kill anything the sandbox left running.

    Killing the runner orphans its `gtimeout ... claude` child -- reproduced
    2026-08-29 and the reason a stray worker outlived `stop.sh`. A test that
    creates that state must clean it up or it leaks a sleeping process per run.
    """
    subprocess.run(["pkill", "-9", "-f", str(sandbox.bin / "claude")],
                   capture_output=True)


@pytest.fixture()
def sandbox(tmp_path: Path):
    sb = RunnerSandbox(tmp_path)
    yield sb
    _reap(sb)


# ---------------------------------------------------------------------------
# AC-1
# ---------------------------------------------------------------------------

@pytest.mark.timeout(120)
def test_a_started_record_exists_before_the_agent_runs(sandbox: RunnerSandbox) -> None:
    """AC-1: ordering, not merely presence.

    The stub agent reads the JSONL summary *while it is running* and copies it
    aside. Asserting on that copy proves the record was written BEFORE the
    work began -- checking the log after the run would pass just as well if
    the record were written at the end.
    """
    probe = sandbox.root / "seen-by-agent.jsonl"
    sandbox.stub_agent(f"""
        log=$(find "{sandbox.root}/.ilk-data" -name .ilk-loop.log 2>/dev/null | head -1)
        [ -n "$log" ] && cp "$log" "{probe}"
        exit 0
    """)
    subprocess.run(sandbox.argv(), env=sandbox.env(), cwd=str(sandbox.proj),
                   capture_output=True, text=True, timeout=110)

    assert probe.exists(), (
        "the agent saw no .ilk-loop.log at all: no record precedes the work"
    )
    seen = [json.loads(x) for x in probe.read_text().splitlines() if x.strip()]
    started = [r for r in seen if r.get("status") == "started"]
    assert started, (
        "no record with status='started' existed when the agent ran; "
        f"records visible to the agent: {seen}"
    )
    rec = started[0]
    for field in ("run_id", "iteration", "timestamp", "project", "model"):
        assert field in rec, f"started record missing {field!r}: {rec}"


# ---------------------------------------------------------------------------
# AC-2
# ---------------------------------------------------------------------------

@pytest.mark.timeout(120)
def test_start_and_completion_records_pair_by_run_id_and_iteration(
    sandbox: RunnerSandbox,
) -> None:
    """AC-2: a clean run still writes the full record, pairable with the start."""
    sandbox.stub_agent("exit 0\n")
    subprocess.run(sandbox.argv(), env=sandbox.env(), cwd=str(sandbox.proj),
                   capture_output=True, text=True, timeout=110)

    recs = sandbox.records()
    assert recs, "a clean run wrote no records at all"

    started = [r for r in recs if r.get("status") == "started"]
    completed = [r for r in recs if r.get("status") != "started"]
    assert started, f"no start record in a clean run; got {recs}"
    assert completed, f"the completion record disappeared; got {recs}"

    key = lambda r: (r.get("run_id"), r.get("iteration"))  # noqa: E731
    assert key(started[0]) in {key(c) for c in completed}, (
        "start and completion records do not pair on (run_id, iteration): "
        f"start={key(started[0])} completions={[key(c) for c in completed]}"
    )


# ---------------------------------------------------------------------------
# AC-3 — the gap that is actually still open
# ---------------------------------------------------------------------------

@pytest.mark.timeout(120)
def test_a_killed_runner_still_leaves_the_started_record(sandbox: RunnerSandbox) -> None:
    """AC-3: SIGKILL the RUNNER, not its child.

    `gtimeout` kills the agent and the runner survives to write -- that path
    works at HEAD and is pinned separately below. The open gap is the runner
    itself dying: measured 2026-08-29, that leaves zero records and a sentinel
    stuck at `state: running`.

    A real kill of a real run, per the sub-plan: nothing here hand-writes a
    truncated log.
    """
    sandbox.stub_agent("sleep 300\n")
    proc = subprocess.Popen(
        sandbox.argv("--iteration-timeout-min", "2"),
        env=sandbox.env(ILK_ITERATION_TIMEOUT_SEC="120"),
        cwd=str(sandbox.proj),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if sandbox._find(".ilk-loop.log") is not None or sandbox.records():
            break
        if sandbox.sentinel().get("state") == "running":
            time.sleep(3)
            break
        time.sleep(0.5)

    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=30)
    time.sleep(1)

    recs = sandbox.records()
    started = [r for r in recs if r.get("status") == "started"]
    assert started, (
        "a SIGKILLed runner left no started record, so the run is "
        "unclassifiable: collect.py has no input and the scheduler's "
        f"postmortem-based blacklist can never fire. records={recs}"
    )


# ---------------------------------------------------------------------------
# Regression pin for f5674c6 — green today, must stay green
# ---------------------------------------------------------------------------

@pytest.mark.timeout(120)
def test_a_gtimeout_child_kill_still_yields_a_complete_record(
    sandbox: RunnerSandbox,
) -> None:
    """Pins f5674c6 with the dirty tree that made it fail.

    Green at HEAD. It is here because the 0-byte log it fixed is the evidence
    this whole batch was planned from, and nothing else in the suite exercises
    the dirty-tree timeout path end to end through the real runner.
    """
    sandbox.stub_agent(f"""
        echo "uncommitted work" > "{sandbox.proj}/newfile.txt"
        sleep 300
    """)
    subprocess.run(
        sandbox.argv(), env=sandbox.env(ILK_ITERATION_TIMEOUT_SEC="5"),
        cwd=str(sandbox.proj), capture_output=True, text=True, timeout=110,
    )

    recs = sandbox.records()
    assert recs, (
        "a gtimeout-killed iteration with a dirty tree wrote NO records -- "
        "this is the f5674c6 regression (git commit's stdout joining "
        "preserve_dirty_tree_on_timeout's return value, int() raising, the "
        "python block dying before print(json.dumps(d)))"
    )
    assert any(r.get("stop_reason") == "timeout" for r in recs), (
        f"no record carries stop_reason=timeout; got {recs}"
    )


# ---------------------------------------------------------------------------
# AC-7
# ---------------------------------------------------------------------------

@pytest.mark.timeout(120)
def test_ship_proof_ledger_is_written_by_a_real_run(sandbox: RunnerSandbox) -> None:
    """AC-7: the ledger's first exercise by an actual driver run.

    Contract 5 shipped in the previous batch and no driver run had occurred
    since 2026-08-27, so it had never executed outside its own unit tests.
    The agent here makes a real commit, which is the writer's trigger.
    """
    sandbox.stub_agent(f"""
        cd "{sandbox.proj}"
        echo "work" > done.txt
        git add -A
        git -c user.email=t@e.com -c user.name=t commit -q -m "chore: work [plan:demo#step-0]"
        exit 0
    """)
    subprocess.run(sandbox.argv(), env=sandbox.env(), cwd=str(sandbox.proj),
                   capture_output=True, text=True, timeout=110)

    ledger = sandbox.ship_proof()
    assert ledger, (
        "no ship-proof.jsonl was written by a real driver run that produced a "
        "commit; Contract 5's writer has never executed in production"
    )
    rec = ledger[0]
    for field in ("run_id", "iteration", "slug", "repo", "step_from", "step_to", "commits"):
        assert field in rec, f"ship-proof record missing {field!r}: {rec}"
    assert rec["commits"], f"ship-proof record has an empty commit list: {rec}"
