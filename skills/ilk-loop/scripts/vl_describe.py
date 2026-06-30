"""vl_describe — vision-as-a-tool for text-only workers.

Single-shot CLI: sends a base64 image + question to a VL model via the
worker's existing Anthropic-compatible gateway, prints a JSON envelope.

Usage:
    python vl_describe.py --image <abs-path> --question "<q>" \
        [--model mimo-v2.5] [--config-dir <dir>] [--max-tokens 1024] [--timeout 60]

Exit 0 on success (``ok:true``), exit 1 on any failure (``ok:false``).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# UTF-8 stdout (zh-CN GBK console crashes on non-ASCII answers)
# ---------------------------------------------------------------------------
def _ensure_utf8():
    """Reconfigure stdout/stderr to utf-8 if possible."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# Creds — reuse the worker's gateway settings
# ---------------------------------------------------------------------------
def _load_creds(config_dir: Path) -> tuple[str, str]:
    """Return (base_url, auth_token) from <config_dir>/settings.json."""
    settings_path = config_dir / "settings.json"
    if not settings_path.exists():
        raise FileNotFoundError(f"settings.json not found: {settings_path}")
    data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    env = data.get("env", {})
    base_url = env.get("ANTHROPIC_BASE_URL", "")
    token = env.get("ANTHROPIC_AUTH_TOKEN", "")
    if not base_url:
        raise ValueError("ANTHROPIC_BASE_URL missing from settings.json env block")
    if not token:
        raise ValueError("ANTHROPIC_AUTH_TOKEN missing from settings.json env block")
    return base_url, token


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------
def _load_image(path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for the image."""
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    suffix = path.suffix.lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp", ".gif": "image/gif"}
    media_type = media_map.get(suffix, "image/png")
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii"), media_type


# ---------------------------------------------------------------------------
# The actual call
# ---------------------------------------------------------------------------
def _call_vl(base_url: str, token: str, model: str,
             image_b64: str, media_type: str, question: str,
             max_tokens: int, timeout: int) -> dict:
    """POST to /v1/messages with an image content block. Returns parsed JSON."""
    import urllib.request
    import urllib.error

    url = base_url.rstrip("/") + "/v1/messages"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": question,
                    },
                ],
            }
        ],
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    # Extract text from the response
    answer_parts = []
    for block in resp_body.get("content", []):
        if block.get("type") == "text":
            answer_parts.append(block.get("text", ""))
    answer = "\n".join(answer_parts).strip()

    usage = resp_body.get("usage", {})
    return {
        "answer": answer,
        "model": resp_body.get("model", model),
        "usage": {"in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0)},
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    _ensure_utf8()

    p = argparse.ArgumentParser(
        description="Describe an image using a VL model via the worker gateway.")
    p.add_argument("--image", required=True, help="Absolute path to a local image")
    p.add_argument("--question", required=True, help="Specific question about the image")
    p.add_argument("--model", default="mimo-v2.5", help="VL model id (default: mimo-v2.5)")
    p.add_argument("--config-dir", default=None,
                   help="Claude config dir with settings.json (default: ~/.claude-worker)")
    p.add_argument("--max-tokens", type=int, default=1024, help="Max tokens in response")
    p.add_argument("--timeout", type=int, default=60, help="HTTP timeout in seconds")
    args = p.parse_args()

    config_dir = Path(args.config_dir) if args.config_dir else Path(
        os.environ.get("CLAUDE_WORKER_HOME", os.path.expanduser("~/.claude-worker"))
    )
    image_path = Path(args.image)

    # --- load creds ---
    try:
        base_url, token = _load_creds(config_dir)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "creds_load_failed", "detail": str(exc)}))
        sys.exit(1)

    # --- load image ---
    try:
        image_b64, media_type = _load_image(image_path)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "image_load_failed", "detail": str(exc)}))
        sys.exit(1)

    # --- call VL ---
    try:
        result = _call_vl(base_url, token, args.model,
                          image_b64, media_type, args.question,
                          args.max_tokens, args.timeout)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "vl_call_failed", "detail": str(exc)}))
        sys.exit(1)

    # --- success ---
    if not result["answer"]:
        print(json.dumps({"ok": False, "error": "blank_answer",
                          "detail": "VL model returned empty text — vision may be unsupported"}))
        sys.exit(1)

    print(json.dumps({
        "ok": True,
        "answer": result["answer"],
        "model": result["model"],
        "image": str(image_path),
        "usage": result["usage"],
    }))


if __name__ == "__main__":
    main()
