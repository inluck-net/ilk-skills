#!/usr/bin/env python3
"""
_stream_json_render.py -- read JSONL from stdin, render live stream summaries.

Mirror of the PowerShell Write-ClaudeStreamLine function in
run_ilk_loop_claude.ps1. Reads line-by-line from stdin (the output of
`claude -p --output-format stream-json --include-partial-messages`),
parses each line as JSON, and prints human-friendly one-line summaries
to stdout.

Usage:
    claude -p --output-format stream-json ... | python _stream_json_render.py
"""

import json
import sys


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
        print(line, flush=True)
        return

    event_type = obj.get("type")
    emit = None
    inline = False

    if event_type == "system":
        subtype = obj.get("subtype", "")
        if subtype == "init":
            model = obj.get("model", "")
            session = obj.get("session_id", "")
            cwd = obj.get("cwd", "")
            emit = f"[init] model={model} session={session} cwd={cwd}"
        else:
            emit = f"[system] {subtype}"

    elif event_type == "stream_event":
        ev = obj.get("event", {})
        if not ev:
            return
        ev_type = ev.get("type")
        if ev_type == "content_block_delta":
            delta = ev.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    emit = text
                    inline = True
            elif delta.get("type") == "thinking_delta":
                return  # skip live thinking stream; too noisy
            else:
                return  # input_json_delta etc.
        elif ev_type in (
            "content_block_start",
            "content_block_stop",
            "message_start",
            "message_delta",
            "message_stop",
        ):
            return
        else:
            return

    elif event_type == "assistant":
        blocks = obj.get("message", {}).get("content", [])
        for block in blocks:
            if block.get("type") == "tool_use":
                arg_summary = format_tool_args(
                    block.get("name", ""), block.get("input", {})
                )
                print(
                    f"\n[tool >] {block.get('name', '')}({arg_summary})", flush=True
                )
        return

    elif event_type == "user":
        blocks = obj.get("message", {}).get("content", [])
        for block in blocks:
            if block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = " ".join(str(c) for c in content)
                preview = " ".join(str(content).split())
                if len(preview) > 160:
                    preview = preview[:160] + "..."
                tag = "[result !]" if block.get("is_error") else "[result <]"
                print(f"{tag} {preview}", flush=True)
        return

    elif event_type == "result":
        duration_ms = obj.get("duration_ms", 0)
        sec = round(duration_ms / 1000.0, 1)
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
        emit = (
            f"\n[done] {obj.get('subtype', '')} in {sec}s{cost_str}{tok_str}"
        )

    else:
        emit = f"[{event_type}]"

    if emit is not None:
        if inline:
            print(emit, end="", flush=True)
        else:
            print(emit, flush=True)


def main() -> int:
    for line in sys.stdin:
        render_line(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
