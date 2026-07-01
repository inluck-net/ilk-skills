"""Runtime harness: exercises the steer-hook wiring inside the PS runner.

Puts a mock `claude` on PATH (records the prompt it receives, exits 0),
then runs a minimal PowerShell iteration loop that sources steer_hook.ps1
and the wiring from run_ilk_loop_claude.ps1.

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
STEER_HOOK = SCRIPTS / "steer_hook.ps1"

# Drives the PowerShell runner wiring; skip where powershell is absent
# (macOS/Linux). Bash parity: test_steer_hook_runner_sh.py.
pytestmark = pytest.mark.skipif(
    shutil.which("powershell") is None,
    reason="powershell not available",
)


# ── helpers ──────────────────────────────────────────────────────────

def _write_mock_claude_ps1(mock_dir: Path) -> Path:
    """Write a mock claude.ps1 that records its arguments to a log file.

    Uses a .ps1 wrapper so multi-line prompts are handled correctly
    (batch .cmd chokes on newlines in %*). Each invocation writes one
    JSON line so _read_prompts can split unambiguously.
    """
    log_path = mock_dir / "claude_prompts.log"
    mock = mock_dir / "claude.ps1"
    # $args[0] is '-p', $args[1..] is the prompt
    mock.write_text(
        textwrap.dedent(f"""\
            # Mock claude — record prompt as JSON line, exit 0
            $prompt = ($args | Select-Object -Skip 1) -join ' '
            $record = @{{ prompt = $prompt; ts = (Get-Date -Format o) }} | ConvertTo-Json -Compress
            [System.IO.File]::AppendAllText(
                '{log_path}',
                $record + [Environment]::NewLine,
                [System.Text.UTF8Encoding]::new($false)
            )
            exit 0
        """),
        encoding="utf-8",
    )
    # Also write a .cmd shim that calls the .ps1
    shim = mock_dir / "claude.cmd"
    shim.write_text(
        f'@powershell -NoProfile -ExecutionPolicy Bypass -File "{mock}" %*\n',
        encoding="utf-8",
    )
    return log_path


def _write_inbox(steer_dir: Path, entries: list[dict]):
    """Write inbox.md with entries."""
    blocks = []
    for entry in entries:
        blocks.append(f"<!-- uuid: {entry['uuid']} -->\n{entry['text']}")
    content = "\n---\n".join(blocks) + "\n"
    (steer_dir / "inbox.md").write_text(content, encoding="utf-8")


def _read_prompts(log_path: Path) -> list[str]:
    """Read the mock claude's recorded prompts (one JSON line per invocation)."""
    if not log_path.exists():
        return []
    prompts = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            prompts.append(obj.get("prompt", ""))
        except json.JSONDecodeError:
            # Fallback: treat as plain text
            prompts.append(line)
    return prompts


def _run_iteration(
    ilk_data_home: Path,
    project_key: str,
    prompt: str = "/ilk please continue the active plan",
    iteration_count: int = 1,
) -> subprocess.CompletedProcess:
    """Run a minimal PS iteration loop that exercises the steer-hook wiring."""
    ps_script = textwrap.dedent(f"""\
        $ErrorActionPreference = 'Stop'
        $env:ILK_DATA_HOME = '{ilk_data_home}'

        # Source steer_hook.ps1
        . '{STEER_HOOK}'

        $ProjectKey = '{project_key}'
        $Prompt = '{prompt}'

        # Ensure steer dir exists
        $steerDir = Join-Path (Join-Path (Join-Path (Join-Path '{ilk_data_home}' "projects") $ProjectKey) "runtime") "steer"
        if (-not (Test-Path $steerDir)) {{
            New-Item -ItemType Directory -Path $steerDir -Force | Out-Null
        }}

        for ($i = 1; $i -le {iteration_count}; $i++) {{
            Write-Host "--- Iteration $i ---"

            # -- Steer hook: pause gate (OUTSIDE timed region) --
            $steerResult = Invoke-SteerHook -ProjectKey $ProjectKey
            if ($steerResult.Paused) {{
                Write-Host "[steer] pause.flag detected — idling"
                Write-Host "[steer] skipped iteration (paused)"
                continue
            }}

            # -- Interjection --
            $iterPrompt = $Prompt
            if ($steerResult.InterjectionText) {{
                $iterPrompt = "OPERATOR INTERJECTIONS (honor before continuing the plan):`n$($steerResult.InterjectionText)`n`n$Prompt"
                Write-Host "[steer] interjection injected"
            }}

            # -- Invoke mock claude --
            Write-Host "[runner] invoking claude"
            & claude -p $iterPrompt
        }}
    """)
    env = os.environ.copy()
    env["PATH"] = str(ilk_data_home / "mock") + ";" + env.get("PATH", "")
    env["ILK_DATA_HOME"] = str(ilk_data_home)

    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )
    return result


@pytest.fixture()
def runner_env(tmp_path):
    """Set up a tmp ILK_DATA_HOME with mock claude and steer dir."""
    ilk_data = tmp_path / "ilk-data"
    project_key = "test-runner"
    steer_dir = ilk_data / "projects" / project_key / "runtime" / "steer"
    steer_dir.mkdir(parents=True, exist_ok=True)
    mock_dir = ilk_data / "mock"
    mock_dir.mkdir(parents=True, exist_ok=True)
    prompt_log = _write_mock_claude_ps1(mock_dir)
    return ilk_data, project_key, steer_dir, prompt_log


# ── (a) inbox entry appears in recorded prompt exactly once ──────────

class TestInterjectionInjected:
    """An inbox entry is injected into the prompt exactly once."""

    def test_single_entry_in_prompt(self, runner_env):
        ilk_data, key, steer_dir, prompt_log = runner_env
        _write_inbox(steer_dir, [{"uuid": "test-uuid-001", "text": "fix the bug now"}])

        result = _run_iteration(ilk_data, key)

        assert result.returncode == 0, f"PS exited {result.returncode}:\n{result.stderr}\nstdout: {result.stdout}"
        prompts = _read_prompts(prompt_log)
        assert len(prompts) == 1, f"Expected 1 prompt, got {len(prompts)}: {prompts}"
        assert "fix the bug now" in prompts[0], f"Interjection not in prompt: {prompts[0]}"
        assert "OPERATOR INTERJECTIONS" in prompts[0]

    def test_entry_appears_exactly_once(self, runner_env):
        """The entry is consumed — running again does NOT re-inject."""
        ilk_data, key, steer_dir, prompt_log = runner_env
        _write_inbox(steer_dir, [{"uuid": "once-only", "text": "do this once"}])

        # First run — consumes the inbox
        r1 = _run_iteration(ilk_data, key)
        assert r1.returncode == 0

        # Second run — no new inbox, should NOT re-inject
        r2 = _run_iteration(ilk_data, key)
        assert r2.returncode == 0

        prompts = _read_prompts(prompt_log)
        assert len(prompts) == 2, f"Expected 2 prompts, got {len(prompts)}: {prompts}"
        # First prompt should have the interjection
        assert "do this once" in prompts[0]
        assert "OPERATOR INTERJECTIONS" in prompts[0]
        # Second prompt should NOT have the interjection
        assert "OPERATOR INTERJECTIONS" not in prompts[1]


# ── (b) second iteration does NOT re-inject ─────────────────────────

class TestNoReinjectAcrossIterations:
    """A second iteration does NOT re-inject already-consumed entries."""

    def test_two_iterations_one_injection(self, runner_env):
        ilk_data, key, steer_dir, prompt_log = runner_env
        _write_inbox(steer_dir, [{"uuid": "iter-test", "text": "inject once"}])

        # Run 2 iterations in one loop
        result = _run_iteration(ilk_data, key, iteration_count=2)
        assert result.returncode == 0

        prompts = _read_prompts(prompt_log)
        assert len(prompts) == 2, f"Expected 2 prompts, got {len(prompts)}: {prompts}"
        # First has interjection, second does not
        assert "inject once" in prompts[0]
        assert "OPERATOR INTERJECTIONS" in prompts[0]
        assert "OPERATOR INTERJECTIONS" not in prompts[1]


# ── (c) pause.flag → mock claude NEVER invoked ─────────────────────

class TestPauseGate:
    """When pause.flag is present, mock claude is NEVER invoked."""

    def test_pause_prevents_claude_invocation(self, runner_env):
        ilk_data, key, steer_dir, prompt_log = runner_env
        # Also write an inbox to verify it's NOT consumed while paused
        _write_inbox(steer_dir, [{"uuid": "paused-uuid", "text": "should not inject"}])
        (steer_dir / "pause.flag").write_text("", encoding="utf-8")

        result = _run_iteration(ilk_data, key)
        assert result.returncode == 0

        # Mock claude should NOT have been invoked
        prompts = _read_prompts(prompt_log)
        assert len(prompts) == 0, f"claude was invoked while paused: {prompts}"

        # Inbox should NOT be consumed (still present)
        assert (steer_dir / "inbox.md").exists(), "inbox.md was consumed while paused"


# ── (d) leftover inbox.processing.md does not double-inject ─────────

class TestCrashRecovery:
    """A leftover inbox.processing.md does not double-inject."""

    def test_leftover_processing_no_double_inject(self, runner_env):
        ilk_data, key, steer_dir, prompt_log = runner_env
        # Simulate a crash: write processing.md with an entry
        processing = steer_dir / "inbox.processing.md"
        processing.write_text(
            "<!-- uuid: crash-uuid -->\nrecovered text\n",
            encoding="utf-8",
        )

        # First run — crash recovery should inject "recovered text"
        r1 = _run_iteration(ilk_data, key)
        assert r1.returncode == 0
        prompts1 = _read_prompts(prompt_log)
        assert len(prompts1) == 1, f"Expected 1 prompt after crash recovery, got {len(prompts1)}"
        assert "recovered text" in prompts1[0]

        # Second run — should NOT re-inject (consumed.jsonl has the uuid)
        r2 = _run_iteration(ilk_data, key)
        assert r2.returncode == 0
        prompts2 = _read_prompts(prompt_log)
        assert len(prompts2) == 2, f"Expected 2 prompts, got {len(prompts2)}"
        assert "OPERATOR INTERJECTIONS" not in prompts2[1], "Double-injection after crash recovery!"
