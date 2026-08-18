#!/usr/bin/env python3
"""
_stream_json_render.py -- read JSONL from stdin, render live stream summaries.

Originally a mirror of the PowerShell Write-ClaudeStreamLine function in
run_ilk_loop_claude.ps1; the two have since diverged — the display contract
below applies to this POSIX path only. Reads line-by-line from stdin (the output of
`claude -p --output-format stream-json --include-partial-messages`),
parses each line as JSON, and prints human-friendly one-line summaries
to stdout.

Usage:
    claude -p --output-format stream-json ... | python _stream_json_render.py

Display contract (so `tail -f iter-NN.log` stays readable and honest):

  * Every standalone line is prefixed with a wall-clock `[HH:MM:SS]` stamp,
    so a stalled log can be diagnosed from the log alone.
  * Thinking is coalesced. Upstream emits one `thinking_delta` plus one
    `system/thinking_tokens` event per chunk; rendering those one-per-line
    buried the real output (18190 of 18440 lines in one measured iteration
    were the bare string "[system] thinking_tokens"). Instead we accumulate
    and emit a progress line at most every THINK_EMIT_SEC, plus one summary
    line when the block ends.
  * SSE `ping` keepalives drive a `[waiting]` heartbeat. Pings arrive only
    while the API has produced nothing, so they are the one signal that
    distinguishes "provider is slow" from "pipeline is wedged". Without
    this the log goes completely silent for minutes at a time.

Both cadences are env-tunable: ILK_RENDER_THINK_SEC, ILK_RENDER_WAIT_SEC.

Note: the heartbeat is ping-driven rather than timer-driven, so it only
fires while the upstream is still sending keepalives. A transport that
dies without pings still shows as silence.
"""

import json
import os
import sys
import time


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# Emit a thinking-progress line at most this often while a block is open.
THINK_EMIT_SEC = _env_float("ILK_RENDER_THINK_SEC", 5.0)
# Emit a waiting heartbeat once output has been silent this long, and
# then again every interval after that.
WAIT_EMIT_SEC = _env_float("ILK_RENDER_WAIT_SEC", 30.0)

_STATE = {
    "at_line_start": True,   # is the cursor at column 0?
    "last_out": time.monotonic(),   # last *substantive* emission
    "last_wait": time.monotonic(),  # last waiting heartbeat
    "think_start": None,     # monotonic start of the open thinking block
    "think_chars": 0,
    "think_emit": 0.0,       # offset into the block of the last progress line
}


def _emit(text: str, real: bool = True, blank_before: bool = False) -> None:
    """Write one timestamped standalone line."""
    lead = "" if _STATE["at_line_start"] else "\n"
    if blank_before:
        lead += "\n"
    sys.stdout.write(f"{lead}[{time.strftime('%H:%M:%S')}] {text}\n")
    sys.stdout.flush()
    _STATE["at_line_start"] = True
    now = time.monotonic()
    _STATE["last_wait"] = now
    if real:
        _STATE["last_out"] = now


def _emit_inline(text: str) -> None:
    """Write streamed assistant text with no stamp and no forced newline."""
    sys.stdout.write(text)
    sys.stdout.flush()
    _STATE["at_line_start"] = text.endswith("\n")
    now = time.monotonic()
    _STATE["last_out"] = now
    _STATE["last_wait"] = now


def _think_delta(text: str) -> None:
    now = time.monotonic()
    if _STATE["think_start"] is None:
        _STATE["think_start"] = now
        _STATE["think_chars"] = 0
        _STATE["think_emit"] = 0.0
    _STATE["think_chars"] += len(text)
    elapsed = now - _STATE["think_start"]
    if elapsed - _STATE["think_emit"] >= THINK_EMIT_SEC:
        _STATE["think_emit"] = elapsed
        _emit(f"[thinking] {_STATE['think_chars']} chars, {elapsed:.0f}s...")
    else:
        # Still real activity for heartbeat purposes, even if unprinted.
        _STATE["last_out"] = now
        _STATE["last_wait"] = now


def _think_flush() -> None:
    if _STATE["think_start"] is None:
        return
    elapsed = time.monotonic() - _STATE["think_start"]
    chars = _STATE["think_chars"]
    _STATE["think_start"] = None
    _STATE["think_chars"] = 0
    if chars:
        _emit(f"[thinking] {chars} chars in {elapsed:.1f}s")


def _wait_beat() -> None:
    """Heartbeat on an SSE ping: the API is connected but producing nothing."""
    now = time.monotonic()
    idle = now - _STATE["last_out"]
    if idle >= WAIT_EMIT_SEC and now - _STATE["last_wait"] >= WAIT_EMIT_SEC:
        _emit(f"[waiting] {idle:.0f}s with no output from the API", real=False)


def format_tool_args(tool_name: str, tool_input: dict) -> str:
    if not tool_input:
        return ""
    t = tool_input
    if tool_name == "Bash":
        cmd = t.get("command", "")
        if cmd:
            cmd = " ".join(cmd.split())
            if len(cmd) > 100:
                cmd = cmd[:100] + "..."
            return f"$ {cmd}"
    if tool_name in ("Read", "Edit", "Write", "MultiEdit"):
        return t.get("file_path", "")
    if tool_name == "Glob":
        return t.get("pattern", "")
    if tool_name == "Grep":
        pat = t.get("pattern", "")
        if len(pat) > 60:
            pat = pat[:60] + "..."
        loc = t.get("path", "")
        return f"/{pat}/ in {loc}" if loc else f"/{pat}/"
    if tool_name == "Task":
        return t.get("description", "")
    if tool_name == "TodoWrite":
        todos = t.get("todos", [])
        return f"{len(todos)} todos"
    # fallback: show first 2 string properties
    shown = []
    for k, v in t.items():
        if isinstance(v, str) and v:
            sv = v[:60] + "..." if len(v) > 60 else v
            shown.append(f"{k}={sv}")
            if len(shown) >= 2:
                break
    return ", ".join(shown)


def render_line(line: str) -> None:
    line = line.strip()
    if not line:
        return

    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        _think_flush()
        _emit(line)
        return

    event_type = obj.get("type")

    if event_type == "system":
        subtype = obj.get("subtype", "")
        if subtype == "thinking_tokens":
            # Paired 1:1 with thinking_delta; the delta handler owns display.
            return
        _think_flush()
        if subtype == "init":
            _emit(
                f"[init] model={obj.get('model', '')}"
                f" session={obj.get('session_id', '')}"
                f" cwd={obj.get('cwd', '')}"
            )
        elif subtype == "status":
            _emit(f"[system] status={obj.get('status', '')}")
        elif subtype == "api_retry":
            _emit(
                f"[system] api_retry attempt={obj.get('attempt', '')}"
                f"/{obj.get('max_retries', '')}"
                f" delay={obj.get('retry_delay_ms', '')}ms"
                f" status={obj.get('error_status')}"
                f" error={obj.get('error', '')}"
            )
        else:
            _emit(f"[system] {subtype}")
        return

    if event_type == "stream_event":
        ev = obj.get("event", {})
        if not ev:
            return
        ev_type = ev.get("type")
        if ev_type == "ping":
            _wait_beat()
            return
        if ev_type == "content_block_delta":
            delta = ev.get("delta", {})
            dtype = delta.get("type")
            if dtype == "thinking_delta":
                _think_delta(delta.get("thinking", "") or "")
            elif dtype == "text_delta":
                text = delta.get("text", "")
                if text:
                    _think_flush()
                    _emit_inline(text)
            # input_json_delta / signature_delta: no display, but they are
            # live traffic — keep the heartbeat quiet.
            else:
                _STATE["last_out"] = time.monotonic()
        return

    if event_type == "assistant":
        blocks = obj.get("message", {}).get("content", [])
        for block in blocks:
            if block.get("type") == "tool_use":
                _think_flush()
                arg_summary = format_tool_args(
                    block.get("name", ""), block.get("input", {})
                )
                _emit(
                    f"[tool >] {block.get('name', '')}({arg_summary})",
                    blank_before=True,
                )
        return

    if event_type == "user":
        blocks = obj.get("message", {}).get("content", [])
        for block in blocks:
            if block.get("type") == "tool_result":
                _think_flush()
                content = block.get("content", "")
                if isinstance(content, list):
                    content = " ".join(str(c) for c in content)
                preview = " ".join(str(content).split())
                if len(preview) > 160:
                    preview = preview[:160] + "..."
                tag = "[result !]" if block.get("is_error") else "[result <]"
                _emit(f"{tag} {preview}")
        return

    if event_type == "result":
        _think_flush()
        sec = round(obj.get("duration_ms", 0) / 1000.0, 1)
        cost_str = ""
        total_cost = obj.get("total_cost_usd")
        if total_cost is not None:
            cost_str = f" cost=${round(total_cost, 4)}"
        tok_str = ""
        usage = obj.get("usage", {})
        if usage:
            tok_str = (
                f" tokens(in={usage.get('input_tokens', '')}"
                f" out={usage.get('output_tokens', '')})"
            )
        _emit(
            f"[done] {obj.get('subtype', '')} in {sec}s{cost_str}{tok_str}",
            blank_before=True,
        )
        return

    _think_flush()
    _emit(f"[{event_type}]")


def main() -> int:
    for line in sys.stdin:
        render_line(line)
    _think_flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
