"""Runtime harness: exercises the steer-hook wiring inside the bash runner.

Bash parity of test_steer_hook_runner.py. Puts a mock `claude` on PATH
(records the prompt it receives, exits 0), then runs a minimal bash
iteration loop that sources steer_hook.sh and replicates the wiring from
run_ilk_loop_claude.sh.

Tests (per AC-2/3/4/6):
- (a) an inbox entry appears in the recorded prompt exactly once
- (b) a second iteration does NOT re-inject
- (c) pause.flag → the mock claude is NEVER invoked while paused
- (d) a leftover inbox.processing.md does not double-inject
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
STEER_HOOK = SCRIPTS / "steer_hook.sh"

# Drives the POSIX-only .sh runner wiring. Skip on win32 (Git Bash isn't a
# faithful POSIX env — see test_steer_hook_sh.py) — the .sh runner never runs
# detached on Windows anyway.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="POSIX-only (.sh runner); bash required and not win32",
)


# ── helpers ──────────────────────────────────────────────────────────

def _write_mock_claude(mock_dir: Path) -> Path:
    """Write a mock `claude` that records its prompt as a JSON line."""
    log_path = mock_dir / "claude_prompts.log"
    mock = mock_dir / "claude"
    mock.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # Mock claude — record prompt as JSON line, exit 0.
            # Args are: -p <prompt> ; drop the flag, keep the rest.
            shift
            prompt="$*"
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


def _write_inbox(steer_dir: Path, entries: list[dict]):
    blocks = []
    for entry in entries:
        blocks.append(f"<!-- uuid: {entry['uuid']} -->\n{entry['text']}")
    content = "\n---\n".join(blocks) + "\n"
    (steer_dir / "inbox.md").write_text(content, encoding="utf-8")


def _read_prompts(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    prompts = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            prompts.append(json.loads(line).get("prompt", ""))
        except json.JSONDecodeError:
            prompts.append(line)
    return prompts


def _run_iteration(
    ilk_data_home: Path,
    project_key: str,
    mock_dir: Path,
    prompt: str = "/ilk please continue the active plan",
    iteration_count: int = 1,
) -> subprocess.CompletedProcess:
    """Run a minimal bash iteration loop that exercises the steer-hook wiring."""
    steer_dir = ilk_data_home / "projects" / project_key / "runtime" / "steer"
    script = textwrap.dedent(f"""\
        set -Eeuo pipefail
        export ILK_DATA_HOME='{ilk_data_home}'
        source '{STEER_HOOK}'

        PROJECT_KEY='{project_key}'
        PROMPT='{prompt}'
        mkdir -p '{steer_dir}'

        for (( i = 1; i <= {iteration_count}; i++ )); do
            echo "--- Iteration $i ---"

            # -- Steer hook: pause gate (OUTSIDE timed region) --
            invoke_steer_hook "$PROJECT_KEY"
            if [[ "$STEER_PAUSED" -eq 1 ]]; then
                echo "[steer] pause.flag detected — idling"
                echo "[steer] skipped iteration (paused)"
                continue
            fi

            # -- Interjection --
            iter_prompt="$PROMPT"
            if [[ -n "$STEER_INTERJECTION_TEXT" ]]; then
                iter_prompt="OPERATOR INTERJECTIONS (honor before continuing the plan):
${{STEER_INTERJECTION_TEXT}}

${{PROMPT}}"
                echo "[steer] interjection injected"
            fi

            # -- Invoke mock claude --
            echo "[runner] invoking claude"
            claude -p "$iter_prompt"
        done
    """)
    env = os.environ.copy()
    env["PATH"] = str(mock_dir) + os.pathsep + env.get("PATH", "")
    env["ILK_DATA_HOME"] = str(ilk_data_home)

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=60, env=env,
    )


@pytest.fixture()
def runner_env(tmp_path):
    ilk_data = tmp_path / "ilk-data"
    project_key = "test-runner"
    steer_dir = ilk_data / "projects" / project_key / "runtime" / "steer"
    steer_dir.mkdir(parents=True, exist_ok=True)
    mock_dir = ilk_data / "mock"
    mock_dir.mkdir(parents=True, exist_ok=True)
    prompt_log = _write_mock_claude(mock_dir)
    return ilk_data, project_key, steer_dir, mock_dir, prompt_log


# ── (a) inbox entry appears in recorded prompt exactly once ──────────

class TestInterjectionInjected:
    def test_single_entry_in_prompt(self, runner_env):
        ilk_data, key, steer_dir, mock_dir, prompt_log = runner_env
        _write_inbox(steer_dir, [{"uuid": "test-uuid-001", "text": "fix the bug now"}])

        result = _run_iteration(ilk_data, key, mock_dir)

        assert result.returncode == 0, f"bash exited {result.returncode}:\n{result.stderr}\nstdout: {result.stdout}"
        prompts = _read_prompts(prompt_log)
        assert len(prompts) == 1, f"Expected 1 prompt, got {len(prompts)}: {prompts}"
        assert "fix the bug now" in prompts[0]
        assert "OPERATOR INTERJECTIONS" in prompts[0]

    def test_entry_appears_exactly_once(self, runner_env):
        ilk_data, key, steer_dir, mock_dir, prompt_log = runner_env
        _write_inbox(steer_dir, [{"uuid": "once-only", "text": "do this once"}])

        r1 = _run_iteration(ilk_data, key, mock_dir)
        assert r1.returncode == 0
        r2 = _run_iteration(ilk_data, key, mock_dir)
        assert r2.returncode == 0

        prompts = _read_prompts(prompt_log)
        assert len(prompts) == 2, f"Expected 2 prompts, got {len(prompts)}: {prompts}"
        assert "do this once" in prompts[0]
        assert "OPERATOR INTERJECTIONS" in prompts[0]
        assert "OPERATOR INTERJECTIONS" not in prompts[1]


# ── (b) second iteration does NOT re-inject ─────────────────────────

class TestNoReinjectAcrossIterations:
    def test_two_iterations_one_injection(self, runner_env):
        ilk_data, key, steer_dir, mock_dir, prompt_log = runner_env
        _write_inbox(steer_dir, [{"uuid": "iter-test", "text": "inject once"}])

        result = _run_iteration(ilk_data, key, mock_dir, iteration_count=2)
        assert result.returncode == 0

        prompts = _read_prompts(prompt_log)
        assert len(prompts) == 2, f"Expected 2 prompts, got {len(prompts)}: {prompts}"
        assert "inject once" in prompts[0]
        assert "OPERATOR INTERJECTIONS" in prompts[0]
        assert "OPERATOR INTERJECTIONS" not in prompts[1]


# ── (c) pause.flag → mock claude NEVER invoked ─────────────────────

class TestPauseGate:
    def test_pause_prevents_claude_invocation(self, runner_env):
        ilk_data, key, steer_dir, mock_dir, prompt_log = runner_env
        _write_inbox(steer_dir, [{"uuid": "paused-uuid", "text": "should not inject"}])
        (steer_dir / "pause.flag").write_text("", encoding="utf-8")

        result = _run_iteration(ilk_data, key, mock_dir)
        assert result.returncode == 0

        prompts = _read_prompts(prompt_log)
        assert len(prompts) == 0, f"claude was invoked while paused: {prompts}"
        assert (steer_dir / "inbox.md").exists(), "inbox.md was consumed while paused"


# ── (d) leftover inbox.processing.md does not double-inject ─────────

class TestCrashRecovery:
    def test_leftover_processing_no_double_inject(self, runner_env):
        ilk_data, key, steer_dir, mock_dir, prompt_log = runner_env
        processing = steer_dir / "inbox.processing.md"
        processing.write_text(
            "<!-- uuid: crash-uuid -->\nrecovered text\n",
            encoding="utf-8",
        )

        r1 = _run_iteration(ilk_data, key, mock_dir)
        assert r1.returncode == 0
        prompts1 = _read_prompts(prompt_log)
        assert len(prompts1) == 1, f"Expected 1 prompt after crash recovery, got {len(prompts1)}"
        assert "recovered text" in prompts1[0]

        r2 = _run_iteration(ilk_data, key, mock_dir)
        assert r2.returncode == 0
        prompts2 = _read_prompts(prompt_log)
        assert len(prompts2) == 2, f"Expected 2 prompts, got {len(prompts2)}"
        assert "OPERATOR INTERJECTIONS" not in prompts2[1], "Double-injection after crash recovery!"
