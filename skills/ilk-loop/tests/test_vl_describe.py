"""Tests for vl_describe.py — gateway smoke + (step 1) mocked unit tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "vl_describe.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HELLO_PNG = FIXTURES / "vl_hello.png"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _has_gateway_creds() -> bool:
    """Check if the worker gateway creds are available."""
    config_dir = Path(os.environ.get("CLAUDE_WORKER_HOME",
                                     os.path.expanduser("~/.claude-worker")))
    settings = config_dir / "settings.json"
    if not settings.exists():
        return False
    try:
        data = json.loads(settings.read_text(encoding="utf-8-sig"))
        env = data.get("env", {})
        return bool(env.get("ANTHROPIC_BASE_URL") and env.get("ANTHROPIC_AUTH_TOKEN"))
    except Exception:
        return False


def _run_vl(*extra_args: str) -> subprocess.CompletedProcess:
    """Run vl_describe.py as a subprocess (the real consumer entry point)."""
    cmd = [sys.executable, str(SCRIPT)] + list(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


# ---------------------------------------------------------------------------
# Gateway smoke (network-gated)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _has_gateway_creds(), reason="Worker gateway creds not available")
@pytest.mark.skipif(not HELLO_PNG.exists(), reason="vl_hello.png fixture missing")
class TestSmokeGateway:
    """Step 0: prove the gateway accepts image blocks for mimo-v2.5."""

    def test_hello_image_returns_answer(self):
        """POST vl_hello.png asking 'what text is shown?' — answer must contain HELLO."""
        result = _run_vl(
            "--image", str(HELLO_PNG),
            "--question", "What text is shown in this image? Reply with the text only.",
        )
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["ok"] is True, f"Expected ok:true, got {data}"
        answer = data["answer"].upper()
        assert "HELLO" in answer, f"Expected 'HELLO' in answer, got: {data['answer']}"
        # Basic envelope shape
        assert "model" in data
        assert "usage" in data
        assert data["image"] == str(HELLO_PNG)

    def test_missing_image_fails_loud(self):
        """A nonexistent image path must produce ok:false + exit 1."""
        result = _run_vl(
            "--image", r"C:\nonexistent\fake.png",
            "--question", "What do you see?",
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert "error" in data

    def test_envelope_shape_has_detail_on_failure(self):
        """Failure envelope must include detail (the actionable next step)."""
        result = _run_vl(
            "--image", r"C:\nonexistent\fake.png",
            "--question", "test",
        )
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert "detail" in data, f"Failure envelope missing 'detail': {data}"
