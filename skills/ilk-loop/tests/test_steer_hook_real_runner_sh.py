"""End-to-end smoke: the REAL run_ilk_loop_claude.sh honors the steer-hook.

Unlike test_steer_hook_runner_sh.py (which replicates the wiring in a minimal
loop), this drives the actual `run_ilk_loop_claude.sh main()` as a subprocess
against a throwaway git project + a mock `claude` on PATH. It closes the
"real detached runner" item (task 2) from the Mac-verify handoff
(docs/handoffs/2026-07-01-sh-steerhook-mac-verify.md):

- the real runner sources steer_hook.sh and injects an inbox.md interjection
  into the actual claude prompt, consuming it exactly once (no re-inject on a
  second run);
- pause.flag makes the real runner idle — claude is NEVER invoked and the
  inbox is NOT consumed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = REPO_ROOT / "skills" / "ilk-loop" / "scripts"
RUNNER = SCRIPTS / "run_ilk_loop_claude.sh"
ILK_PATHS = SCRIPTS / "ilk_paths.py"

# The .sh runner is POSIX-only and needs gtimeout (coreutils) + git + bash.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32"
    or shutil.which("bash") is None
    or shutil.which("gtimeout") is None
    or shutil.which("git") is None,
    reason="POSIX-only real-runner smoke; needs bash + gtimeout + git",
)


# ── fixture builders ──────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, encoding="utf-8", errors="replace")


def _write_mock_claude(mock_dir: Path) -> Path:
    """A mock `claude` recording the LAST arg (the prompt) as a JSON line."""
    log_path = mock_dir / "prompts.log"
    mock = mock_dir / "claude"
    mock.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # Mock claude — the prompt is the last positional arg. Record it,
            # emit nothing on stdout (the stream-json renderer tolerates empty),
            # exit 0.
            prompt=""
            for a in "$@"; do prompt="$a"; done
            MOCK_LOG='{log_path}' "{sys.executable}" - "$prompt" <<'PY'
            import json, os, sys
            with open(os.environ['MOCK_LOG'], 'a', encoding='utf-8') as fh:
                fh.write(json.dumps({{"prompt": sys.argv[1]}}) + "\\n")
            PY
            exit 0
        """),
        encoding="utf-8",
    )
    mock.chmod(0o755)
    return log_path


def _write_plans(proj: Path) -> None:
    plans = proj / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "MASTER-2026-06-07-tsmoke.md").write_text(textwrap.dedent("""\
        ---
        title: Smoke
        slug: tsmoke
        created: 2026-06-07T00:00:00+08:00
        status: active
        priority: 5
        pause_after_ship: false
        branch: null
        goal: smoke fixture
        out_of_scope: []
        cross_cutting_invariants: []
        ---

        # Smoke

        ## Sub-plan registry

        | # | Order | Slug | Items | Steps (est.) | Status |
        |---|---|---|---|---|---|
        | 1 | 1 | [tsmoke-sub](./2026-06-07-tsmoke-sub.md) | test | 3 | pending |
        """), encoding="utf-8")
    (plans / "2026-06-07-tsmoke-sub.md").write_text(textwrap.dedent("""\
        ---
        plan: tsmoke-sub
        status: pending
        current_step: 0
        tickets: []
        priority: P2
        estimated_steps: 3
        last_updated: 2026-06-07
        ---

        # Sub-plan
        """), encoding="utf-8")


def _project_key(proj: Path, ilk: Path) -> str:
    env = {**os.environ, "ILK_DATA_HOME": str(ilk)}
    out = subprocess.run(
        [sys.executable, str(ILK_PATHS), "--start", str(proj)],
        capture_output=True, text=True, env=env, encoding="utf-8", errors="replace",
    )
    return json.loads(out.stdout)["project_key"]


@pytest.fixture()
def smoke(tmp_path):
    """Build (proj, ilk, mock_dir, prompt_log, steer_dir, run_env)."""
    proj = tmp_path / "proj"
    ilk = tmp_path / "ilk-data"
    mock_dir = tmp_path / "mock"
    proj.mkdir(); ilk.mkdir(); mock_dir.mkdir()

    _git(proj, "init", "-q", ".")
    _git(proj, "config", "user.email", "t@t.co")
    _git(proj, "config", "user.name", "t")
    _git(proj, "commit", "-q", "--allow-empty", "-m", "init")
    _write_plans(proj)
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "add plans")

    prompt_log = _write_mock_claude(mock_dir)
    key = _project_key(proj, ilk)
    steer_dir = ilk / "projects" / key / "runtime" / "steer"
    steer_dir.mkdir(parents=True, exist_ok=True)

    run_env = {**os.environ, "ILK_DATA_HOME": str(ilk),
               "PATH": str(mock_dir) + os.pathsep + os.environ.get("PATH", "")}
    return proj, ilk, mock_dir, prompt_log, steer_dir, run_env


def _write_inbox(steer_dir: Path, uuid: str, text: str) -> None:
    (steer_dir / "inbox.md").write_text(
        f"<!-- uuid: {uuid} -->\n{text}\n", encoding="utf-8")


def _read_prompts(prompt_log: Path) -> list[str]:
    if not prompt_log.exists():
        return []
    return [json.loads(ln)["prompt"]
            for ln in prompt_log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _run_runner(proj: Path, run_env: dict, *, wall_sec: int) -> subprocess.CompletedProcess:
    """Run the real runner for one iteration, hard-bounded by an outer gtimeout
    (so a pause-idle run terminates instead of hanging)."""
    cmd = ["gtimeout", str(wall_sec), "bash", str(RUNNER),
           "--project-path", str(proj), "--max-iterations", "1"]
    return subprocess.run(cmd, capture_output=True, text=True, env=run_env,
                          encoding="utf-8", errors="replace", timeout=wall_sec + 30)


# ── task 2a: real runner injects + consumes exactly once ─────────────

def test_real_runner_injects_and_consumes_once(smoke):
    proj, ilk, mock_dir, prompt_log, steer_dir, run_env = smoke
    _write_inbox(steer_dir, "smoke-uuid-1", "SMOKE_MARKER handle this")

    # First run — the real runner should inject the interjection once.
    r1 = _run_runner(proj, run_env, wall_sec=90)
    assert r1.returncode == 0, f"runner rc={r1.returncode}\n{r1.stdout}\n{r1.stderr}"

    prompts = _read_prompts(prompt_log)
    assert len(prompts) == 1, f"expected 1 claude call, got {len(prompts)}: {prompts}"
    assert "SMOKE_MARKER handle this" in prompts[0]
    assert "OPERATOR INTERJECTIONS" in prompts[0]

    # Consumed exactly once: uuid recorded, inbox renamed away.
    consumed = (steer_dir / "inbox.consumed.jsonl")
    assert consumed.exists()
    recs = [json.loads(ln) for ln in consumed.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    assert [r["uuid"] for r in recs] == ["smoke-uuid-1"]
    assert not (steer_dir / "inbox.md").exists()

    # Second run (fresh runner invocation) — must NOT re-inject.
    r2 = _run_runner(proj, run_env, wall_sec=90)
    assert r2.returncode == 0, f"runner rc={r2.returncode}\n{r2.stdout}\n{r2.stderr}"
    prompts = _read_prompts(prompt_log)
    assert len(prompts) == 2, f"expected 2 claude calls total, got {len(prompts)}"
    assert "OPERATOR INTERJECTIONS" not in prompts[1], "re-injected an already-consumed entry"


# ── task 2b: real runner respects pause.flag ─────────────────────────

def test_real_runner_respects_pause_flag(smoke):
    proj, ilk, mock_dir, prompt_log, steer_dir, run_env = smoke
    _write_inbox(steer_dir, "pause-uuid", "SHOULD_NOT_APPEAR while paused")
    (steer_dir / "pause.flag").write_text("", encoding="utf-8")

    # The pause gate idles indefinitely; the outer gtimeout kills it (rc 124).
    r = _run_runner(proj, run_env, wall_sec=8)
    assert r.returncode == 124, f"expected gtimeout kill (124) while paused, got {r.returncode}\n{r.stdout}"
    assert "pause.flag detected" in r.stdout

    # claude never invoked; inbox not consumed.
    assert _read_prompts(prompt_log) == [], "claude was invoked while paused"
    assert (steer_dir / "inbox.md").exists(), "inbox.md consumed while paused"
    assert not (steer_dir / "inbox.consumed.jsonl").exists()
