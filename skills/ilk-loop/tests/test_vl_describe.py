"""Tests for vl_describe.py — gateway smoke + mocked unit tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")


def _run_vl_mocked(mock_code: str, *extra_args: str,
                   env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    """Run vl_describe.py in a subprocess with urllib.request.urlopen mocked.

    ``mock_code`` is Python source that defines:
        def mock_urlopen(req, timeout=None) -> context manager
    returning a response whose .read() returns the JSON bytes the gateway
    would return.
    """
    import tempfile
    wrapper = (
        "import sys, json, urllib.request\n"
        "from unittest.mock import patch, MagicMock\n"
        "from types import SimpleNamespace\n"
        "from io import BytesIO\n"
        "\n"
        + mock_code + "\n"
        "\n"
        "with patch('urllib.request.urlopen', side_effect=mock_urlopen):\n"
        "    import importlib.util\n"
        f"    spec = importlib.util.spec_from_file_location('vl_describe', r'{SCRIPT}')\n"
        "    mod = importlib.util.module_from_spec(spec)\n"
        f"    sys.argv = ['vl_describe.py'] + {list(extra_args)!r}\n"
        "    spec.loader.exec_module(mod)\n"
        "    mod.main()\n"
    )
    env = {**os.environ, **(env_overrides or {}),
           "CLAUDE_WORKER_HOME": str(Path(__file__).resolve().parent / "fixtures" / "fake_worker")}
    # Create a fake settings.json for the mocked tests
    fake_dir = Path(env["CLAUDE_WORKER_HOME"])
    fake_dir.mkdir(parents=True, exist_ok=True)
    fake_settings = fake_dir / "settings.json"
    if not fake_settings.exists():
        fake_settings.write_text(json.dumps({
            "env": {
                "ANTHROPIC_BASE_URL": "https://fake-gateway.example.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": "fake-token-for-testing",
            }
        }), encoding="utf-8")
    # Write wrapper to a temp file to avoid escaping issues
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(wrapper)
        tmp_path = f.name
    try:
        return subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=30,
            env=env, encoding="utf-8",
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# A mock_urlopen that returns a successful VL response
_MOCK_SUCCESS_URLOPEN = """\
def mock_urlopen(req, timeout=None):
    resp_data = json.dumps({
        "model": "mimo-v2.5",
        "content": [{"type": "text", "text": "I see the text HELLO in the image."}],
        "usage": {"input_tokens": 100, "output_tokens": 20}
    }).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = resp_data
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp
"""

# A mock_urlopen that simulates an HTTP error
_MOCK_HTTP_ERROR_URLOPEN = """\
def mock_urlopen(req, timeout=None):
    import urllib.error
    raise urllib.error.HTTPError(
        url="https://fake/v1/messages", code=400, msg="Bad Request",
        hdrs=None, fp=None)
"""

# A mock_urlopen that simulates a vision-unsupported (blank content) response
_MOCK_BLANK_URLOPEN = """\
def mock_urlopen(req, timeout=None):
    resp_data = json.dumps({
        "model": "mimo-v2.5",
        "content": [],
        "usage": {"input_tokens": 100, "output_tokens": 0}
    }).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = resp_data
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp
"""


# ---------------------------------------------------------------------------
# Mocked unit tests (deterministic, no network)
# ---------------------------------------------------------------------------
class TestMockedUnit:
    """Step 1: HTTP layer mocked — deterministic success and failure paths."""

    def test_success_ok_true_with_answer(self):
        """Mocked success → ok:true + answer + envelope fields."""
        result = _run_vl_mocked(
            _MOCK_SUCCESS_URLOPEN,
            "--image", str(HELLO_PNG),
            "--question", "What text is shown?",
        )
        assert result.returncode == 0, f"exit {result.returncode}, stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert "HELLO" in data["answer"].upper()
        assert data["model"] == "mimo-v2.5"
        assert data["usage"]["in"] == 100
        assert data["usage"]["out"] == 20

    def test_http_error_fails_loud(self):
        """HTTP 400 → ok:false + exit 1 + detail."""
        result = _run_vl_mocked(
            _MOCK_HTTP_ERROR_URLOPEN,
            "--image", str(HELLO_PNG),
            "--question", "test",
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert data["error"] == "vl_call_failed"
        assert "detail" in data

    def test_blank_answer_fails_loud(self):
        """Blank/empty answer → ok:false + exit 1 (never a blank success)."""
        result = _run_vl_mocked(
            _MOCK_BLANK_URLOPEN,
            "--image", str(HELLO_PNG),
            "--question", "test",
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert data["error"] == "blank_answer"

    def test_missing_image_fails_loud_mocked(self):
        """Nonexistent image → ok:false + exit 1 (no network needed)."""
        result = _run_vl_mocked(
            _MOCK_SUCCESS_URLOPEN,
            "--image", r"C:\nonexistent\fake.png",
            "--question", "test",
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert data["error"] == "image_load_failed"

    def test_stdout_is_utf8(self):
        """The tool must reconfigure stdout to utf-8 (zh-CN GBK safety)."""
        # If stdout encoding were GBK, non-ASCII in the answer would crash.
        # This test just verifies the tool doesn't crash on non-ASCII answers.
        mock_code = """\
def mock_urlopen(req, timeout=None):
    resp_data = json.dumps({
        "model": "mimo-v2.5",
        "content": [{"type": "text", "text": "图片中显示了中文：你好世界"}],
        "usage": {"input_tokens": 100, "output_tokens": 20}
    }).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = resp_data
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp
"""
        result = _run_vl_mocked(
            mock_code,
            "--image", str(HELLO_PNG),
            "--question", "What text?",
        )
        assert result.returncode == 0, f"exit {result.returncode}, stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert "你好" in data["answer"]


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
