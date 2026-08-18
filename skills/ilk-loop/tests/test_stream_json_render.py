"""_stream_json_render.py display contract.

The renderer sits on the live loop path: `claude --output-format stream-json`
pipes through it into `iter-NN.log`, which is what `tail -f` and
/ilk-feedback both read. Before this suite it had no coverage.

AC-1: `system/thinking_tokens` events produce no output. Upstream emits one
      per thinking chunk; rendering them one-per-line produced 18190 of
      18440 lines in a measured iteration.

AC-2: A thinking block is coalesced into a single summary line naming the
      character count, not one line per delta.

AC-3: SSE `ping` keepalives drive a `[waiting Ns]` heartbeat once output has
      been silent past the threshold, and stay silent before it. Pings only
      arrive while the API is producing nothing, so this is what separates
      "provider is slow" from "pipeline is wedged".

AC-4: Standalone lines carry a `[HH:MM:SS]` stamp; streamed assistant text
      stays inline and unstamped so prose is not broken mid-sentence.

AC-5: The `[init]` / `[tool >]` / `[result <]` / `[result !]` / `[done]`
      markers are unchanged — /ilk-feedback and operators grep for them.

AC-6: A non-JSON line passes through rather than being swallowed.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

RENDERER = Path(__file__).resolve().parents[1] / "scripts" / "_stream_json_render.py"

STAMP = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")


def render(events, env_extra=None, feed_delay=None):
    """Run the renderer over `events` and return its stdout lines."""
    if feed_delay is None:
        payload = "".join(
            (e if isinstance(e, str) else json.dumps(e)) + "\n" for e in events
        )
        proc = subprocess.run(
            [sys.executable, str(RENDERER)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,
            env=_env(env_extra),
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.splitlines()

    # Timed feed: emit each event, then sleep, so wall-clock thresholds fire.
    driver = (
        "import json,sys,time\n"
        f"evs={json.dumps([e if isinstance(e, str) else json.dumps(e) for e in events])}\n"
        "for e in evs:\n"
        "    print(e); sys.stdout.flush()\n"
        f"    time.sleep({feed_delay})\n"
    )
    feeder = subprocess.Popen(
        [sys.executable, "-u", "-c", driver], stdout=subprocess.PIPE
    )
    proc = subprocess.run(
        [sys.executable, "-u", str(RENDERER)],
        stdin=feeder.stdout,
        capture_output=True,
        text=True,
        timeout=120,
        env=_env(env_extra),
    )
    feeder.wait(timeout=10)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


def _env(extra):
    import os

    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def _thinking(text):
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": text},
        },
    }


def _text(text):
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        },
    }


PING = {"type": "stream_event", "event": {"type": "ping"}}
TOOL = {
    "type": "assistant",
    "message": {
        "content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x.py"}}
        ]
    },
}
RESULT = {
    "type": "user",
    "message": {"content": [{"type": "tool_result", "content": "1 import os"}]},
}


def test_thinking_tokens_system_events_are_silent():
    """AC-1: the noise events that dominated the old log produce nothing."""
    out = render(
        [{"type": "system", "subtype": "thinking_tokens"} for _ in range(50)]
    )
    assert out == [], out


def test_thinking_block_coalesces_to_one_summary_line():
    """AC-2: many deltas -> one line naming the accumulated char count."""
    out = render([_thinking("abc") for _ in range(40)] + [TOOL])
    think = [ln for ln in out if "[thinking]" in ln]
    assert len(think) == 1, think
    assert "120 chars" in think[0], think[0]


def test_ping_heartbeat_fires_only_past_the_threshold():
    """AC-3: silence past the threshold is reported; brief silence is not."""
    quiet = render([PING] * 4, env_extra={"ILK_RENDER_WAIT_SEC": "600"})
    assert [ln for ln in quiet if "[waiting]" in ln] == [], quiet

    beats = render(
        [PING] * 6, env_extra={"ILK_RENDER_WAIT_SEC": "1"}, feed_delay=0.6
    )
    waiting = [ln for ln in beats if "[waiting]" in ln]
    assert len(waiting) >= 2, beats
    assert "no output from the API" in waiting[0]
    # The counter is cumulative idle time, so it must not reset per beat.
    secs = [int(re.search(r"\[waiting\] (\d+)s", ln).group(1)) for ln in waiting]
    assert secs == sorted(secs) and secs[-1] > secs[0], secs


def test_standalone_lines_stamped_and_streamed_text_is_not():
    """AC-4: stamps on event lines, none mid-prose."""
    out = render([TOOL, _text("Hello "), _text("world.\n"), RESULT])
    assert [ln for ln in out if "[tool >]" in ln or "[result <]" in ln]
    for ln in out:
        if "[tool >]" in ln or "[result <]" in ln:
            assert STAMP.match(ln), ln
    assert "Hello world." in "\n".join(out)


def test_markers_are_unchanged():
    """AC-5: the strings /ilk-feedback and operators grep for still appear."""
    out = render(
        [
            {"type": "system", "subtype": "init", "model": "m", "session_id": "s",
             "cwd": "/c"},
            TOOL,
            RESULT,
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "boom", "is_error": True}]}},
            {"type": "result", "subtype": "success", "duration_ms": 21000,
             "total_cost_usd": 0.12,
             "usage": {"input_tokens": 10, "output_tokens": 20}},
        ]
    )
    blob = "\n".join(out)
    for marker in ("[init] model=m", "[tool >] Read(/tmp/x.py)", "[result <]",
                   "[result !]", "[done] success in 21.0s"):
        assert marker in blob, (marker, blob)


def test_api_retry_is_rendered_with_detail():
    """A retry storm is the signal for a flaky endpoint — do not bury it."""
    out = render([{"type": "system", "subtype": "api_retry", "attempt": 1,
                   "max_retries": 10, "retry_delay_ms": 619,
                   "error_status": None, "error": "unknown"}])
    assert len(out) == 1, out
    assert "api_retry attempt=1/10" in out[0] and "delay=619ms" in out[0], out[0]


def test_non_json_line_passes_through():
    """AC-6: stderr bleed / wrapper noise must stay visible."""
    out = render(["gtimeout: sending signal TERM"])
    assert len(out) == 1 and "gtimeout: sending signal TERM" in out[0], out
