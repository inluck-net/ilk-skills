"""A ledger row must survive a neighbour that forgot its newline.

Measured 2026-09-18 by a gh-resolve session on run `12cd8693`:
`runtime/launcher/ship-proof.jsonl` was 1131 bytes holding **4 rows with only 3
newline characters**. A worker hand-appended a row without a trailing newline,
so the runner's next append landed on the SAME line:

    {"slug":"…batch-verification…","step_to":2,…}{"run_id":"…","provenance":"loop-executed",…}

`ship_proof_ledger.read_records` skips a line it cannot parse -- deliberately,
per its AC-6: "an unreadable ledger must not turn a proven ship into an unproven
one". So **both** rows vanished, including the `loop-executed` row that was the
only evidence attributing the work sub-plan. The ledger had written the proof
and then eaten it.

That matters more since 027291d made `ship_integrity` read this ledger: a row
lost here is now a false `ship_integrity_violation`, which is the defect that
parked 17 resolver runs in the first place.

Two defects, two fixes:

WRITER (`run_ilk_loop_claude.sh`, `write_ship_proof_records`) -- appends
`printf '%s\n' "$record"`, which terminates its OWN row but never checks that
the file already ended with a newline. A foreign writer that omits one corrupts
the runner's next row too.

READER (`ship_proof_ledger.read_records`) -- a line holding two concatenated
JSON objects is recoverable, not garbage. AC-6 is preserved: genuinely
unparseable input is still skipped rather than raised, but now ANNOUNCED, so
"the ledger does not parse" stops being indistinguishable from "the ledger does
not cover this step".

AC-1  reader recovers both objects from a glued line
AC-2  reader still skips true garbage (AC-6 intact) and announces it
AC-3  reader is unchanged on well-formed input (positive control)
AC-4  writer prepends a newline when the file does not end with one
AC-5  writer adds no blank line when the file is already well-formed
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

RUNNER = (Path(__file__).resolve().parent.parent / "scripts"
          / "run_ilk_loop_claude.sh")

_PATH = os.environ.get("PATH", "/usr/bin:/bin")

#: The seam step 1 adds to the runner.  Named here so every assertion below
#: fails loudly (rather than vacuously passing on an absent function) until it
#: exists — an undefined shell function also writes no ledger, which is
#: exactly what AC-2 asserts.
WRITER_FUNC = "write_ship_proof_records"


# ── fixture helpers ──────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def _subplan(plans: Path, slug: str, current_step: int, steps: int) -> Path:
    sp = plans / f"2026-08-29-{slug}.md"
    sp.write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: in-progress\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: {steps}\n"
        "local_checks: []\n"
        "---\n\n"
        f"# Sub-plan: {slug}\n\n"
        + "".join(f"### Step {n} — work\n\nBody.\n\n" for n in range(steps)),
        encoding="utf-8",
    )
    return sp


def _make_project(root: Path, subplans: dict[str, tuple[int, int]]) -> Path:
    """A git repo with ``docs/plans`` holding a MASTER + the named sub-plans.

    *subplans* maps slug -> (current_step, estimated_steps).
    """
    plans = root / "docs" / "plans"
    plans.mkdir(parents=True)
    registry = "\n".join(
        f"| {n} | [2026-08-29-{slug}.md](./2026-08-29-{slug}.md) | pending |"
        for n, slug in enumerate(subplans, start=1)
    )
    (plans / "MASTER-2026-08-29-ledger.md").write_text(
        "---\n"
        "master_plan: 2026-08-29-ledger\n"
        "batch_date: 2026-08-29\n"
        "status: active\n"
        "---\n\n"
        "# MASTER plan: ledger\n\n"
        "## Sub-plan registry\n\n"
        "| # | Sub-plan | Status |\n|---|---|---|\n"
        f"{registry}\n",
        encoding="utf-8",
    )
    for slug, (cur, steps) in subplans.items():
        _subplan(plans, slug, cur, steps)

    # A shared remote — the condition under which trailers do not exist.
    (root / ".ilk-remote-type").write_text("shared\n", encoding="utf-8")

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _sandbox_env(root: Path) -> dict[str, str]:
    """HOME and ILK_DATA_HOME pinned inside *root* so nothing reads ~/.ilk-data."""
    return {
        "PATH": _PATH,
        "HOME": str(root),
        "ILK_DATA_HOME": str(root / ".ilk-data"),
        "ILK_DOTSOURCE_ONLY": "1",
    }


def _launcher_dir(project: Path, env: dict[str, str]) -> Path:
    """The ledger's directory, resolved the way the runner resolves it."""
    resolver = RUNNER.parent / "ilk_paths.py"
    proc = subprocess.run(
        ["python3", str(resolver), "--start", str(project)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return Path(json.loads(proc.stdout)["external_launcher_dir"])


def _run_writer(
    project: Path,
    env: dict[str, str],
    *,
    pre_iter_target: str,
    before: str,
    after: str,
    run_id: str,
    iteration: int,
) -> subprocess.CompletedProcess:
    """Dot-source the runner and call the ledger writer for one iteration."""
    heads_dir = project.parent / "heads"
    heads_dir.mkdir(exist_ok=True)
    (heads_dir / "before").write_text(f"{project}={before}\n", encoding="utf-8")
    (heads_dir / "after").write_text(f"{project}={after}\n", encoding="utf-8")

    script = f"""
export ILK_DOTSOURCE_ONLY=1
source '{RUNNER}'
PROJECT_PATH='{project}'
REPOS=('{project}')
RUN_ID='{run_id}'
LOOP_STATUS_SCRIPT='{RUNNER.parent / "loop_status.py"}'
PRE_ITER_TARGET=$'{pre_iter_target}'
set +e
declare -F {WRITER_FUNC} >/dev/null || {{ echo "WRITER_MISSING"; exit 90; }}
{WRITER_FUNC} '{heads_dir / "before"}' '{heads_dir / "after"}' {iteration}
echo "RC=$?"
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(project),
    )


def _read_ledger(project: Path, env: dict[str, str]) -> list[dict]:
    ledger = _launcher_dir(project, env) / "ship-proof.jsonl"
    if not ledger.exists():
        return []
    out = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out



import pytest
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from ship_proof_ledger import read_records  # noqa: E402





def _productive_iteration(project: Path) -> tuple[str, str]:
    """Make two trailerless commits; return (before, after)."""
    before = _git(project, "rev-parse", "HEAD")
    for n in (1, 2):
        (project / f"file{n}.txt").write_text(f"change {n}\n", encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m", f"fix(app): change {n}")
    return before, _git(project, "rev-parse", "HEAD")

# ── AC-1 / AC-2 / AC-3: the reader ──────────────────────────────────────────

class TestReaderRecoversGluedRows:
    def test_two_objects_on_one_line_are_both_recovered(
        self, tmp_path: Path
    ) -> None:
        a = {"slug": "work", "step_from": 0, "step_to": 1}
        b = {"slug": "work", "step_from": 1, "step_to": 1,
             "provenance": "loop-executed"}
        led = tmp_path / "ship-proof.jsonl"
        led.write_text(json.dumps(a) + json.dumps(b) + "\n", encoding="utf-8")

        recs = read_records(led)

        assert len(recs) == 2, (
            "a glued line is two recoverable records, not garbage; losing "
            f"them is how #2665 lost its only loop-executed row. got: {recs}")
        assert any(r.get("provenance") == "loop-executed" for r in recs)

    def test_true_garbage_is_still_skipped_and_announced(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        led = tmp_path / "ship-proof.jsonl"
        led.write_text(
            json.dumps({"slug": "ok"}) + "\nnot json at all\n", encoding="utf-8")

        recs = read_records(led)

        assert [r["slug"] for r in recs] == ["ok"], "AC-6: skip, never raise"
        err = capsys.readouterr().err
        assert "ship-proof" in err or "unparseable" in err.lower(), (
            "a silently dropped row is indistinguishable from a row that was "
            f"never written. stderr was: {err!r}")

    def test_wellformed_ledger_is_unchanged(self, tmp_path: Path) -> None:
        led = tmp_path / "ship-proof.jsonl"
        led.write_text(
            json.dumps({"slug": "a"}) + "\n" + json.dumps({"slug": "b"}) + "\n",
            encoding="utf-8")
        assert [r["slug"] for r in read_records(led)] == ["a", "b"]


# ── AC-4 / AC-5: the writer ─────────────────────────────────────────────────

class TestWriterTerminatesTheLine:
    def test_append_after_a_missing_newline_does_not_glue(
        self, tmp_path: Path
    ) -> None:
        project = _make_project(tmp_path, {"work": (1, 2)})
        env = _sandbox_env(tmp_path)
        led_dir = _launcher_dir(project, env)
        led_dir.mkdir(parents=True, exist_ok=True)
        led = led_dir / "ship-proof.jsonl"
        # A foreign writer's row WITHOUT a trailing newline — the real shape.
        led.write_text(json.dumps({"slug": "work", "step_from": 0,
                                   "step_to": 1}), encoding="utf-8")

        before, after = _productive_iteration(project)
        _run_writer(
            project, env,
            pre_iter_target="work", before=before, after=after,
            run_id="20260918-000000", iteration=1,
        )

        text = led.read_text(encoding="utf-8")
        lines = [l for l in text.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            json.loads(line)  # every line must parse on its own
        assert len(lines) >= 2, (
            f"the runner's row must land on its own line. file was:\n{text}")

    def test_no_blank_line_when_already_terminated(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path, {"work": (1, 2)})
        env = _sandbox_env(tmp_path)
        led_dir = _launcher_dir(project, env)
        led_dir.mkdir(parents=True, exist_ok=True)
        led = led_dir / "ship-proof.jsonl"
        led.write_text(json.dumps({"slug": "work"}) + "\n", encoding="utf-8")

        before, after = _productive_iteration(project)
        _run_writer(
            project, env,
            pre_iter_target="work", before=before, after=after,
            run_id="20260918-000000", iteration=1,
        )

        text = led.read_text(encoding="utf-8")
        assert "\n\n" not in text, f"blank line introduced:\n{text!r}"
