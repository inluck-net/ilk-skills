#!/usr/bin/env python3
"""MiniMax draw capability tool — image generation (image-01) and curation (M3-VL).

Part of the model-worker framework capability-services layer.
This is an HTTP tool, NOT a worker home — see docs/model-worker-framework.md §2b.

Pure-core functions (request builders, decoders, file I/O) have NO network
dependency and are fully unit-testable offline. The single network call lives
behind `_post()` which tests inject/monkeypatch — never invoked by loop gates.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

IMAGE_GEN_URL = "https://api.minimaxi.com/v1/image_generation"
CURATE_URL = "https://api.minimaxi.com/anthropic/v1/messages"
IMAGE_MODEL = "image-01"
CURATE_MODEL = "MiniMax-M3"

# JPEG and PNG magic bytes
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ── Pure core — request builders ────────────────────────────────────────────

def build_image_request(
    prompt: str,
    aspect_ratio: str = "1:1",
    style_prefix: str = "",
) -> dict[str, Any]:
    """Build the JSON payload for image-01 image generation.

    Returns a dict suitable for json.dumps → POST body.
    """
    full_prompt = f"{style_prefix} {prompt}".strip() if style_prefix else prompt
    return {
        "model": IMAGE_MODEL,
        "prompt": full_prompt,
        "aspect_ratio": aspect_ratio,
        "response_format": "base64",
        "n": 1,
    }


def build_curate_request(
    image_bytes: bytes,
    criteria: str = "Is this image well-composed, on-style, and usable as game art?",
    model: str = CURATE_MODEL,
) -> dict[str, Any]:
    """Build an Anthropic-style messages payload for M3-VL curation.

    Sends the image as a base64 image block alongside a text prompt.
    Returns a dict suitable for json.dumps → POST body.
    """
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "model": model,
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Review this image. Criteria: {criteria}\n"
                            "Respond as JSON: {\"verdict\": \"approve\"|\"reject\", "
                            "\"score\": 0-10, \"notes\": \"...\"}"
                        ),
                    },
                ],
            }
        ],
    }


# ── Pure core — response decoders ───────────────────────────────────────────

def decode_image_payload(resp_json: dict[str, Any]) -> bytes:
    """Extract and decode the base64 image from an image-01 response.

    Handles `data.image_base64` as either a single string or a list of strings
    (takes the first element if list). Raises ValueError on missing/invalid data.
    """
    data = resp_json.get("data")
    if data is None:
        raise ValueError("Response missing 'data' field")

    b64_str = data.get("image_base64") if isinstance(data, dict) else None
    if b64_str is None:
        raise ValueError("Response missing 'data.image_base64' field")

    # image_base64 can be a string or a list of strings
    if isinstance(b64_str, list):
        if not b64_str:
            raise ValueError("'data.image_base64' is an empty list")
        b64_str = b64_str[0]

    if not isinstance(b64_str, str):
        raise ValueError(f"Expected string, got {type(b64_str).__name__}")

    try:
        return base64.b64decode(b64_str)
    except Exception as e:
        raise ValueError(f"Invalid base64 data: {e}") from e


def parse_curate_verdict(resp_json: dict[str, Any]) -> dict[str, Any]:
    """Extract the text verdict from an M3-VL Anthropic-style response.

    Returns the parsed JSON verdict dict. Raises ValueError if the response
    structure is unexpected or the content is not valid JSON.
    """
    content = resp_json.get("content")
    if content is None:
        # Anthropic-style responses may nest under "content" at top level
        # or inside choices[0].message.content — try both.
        choices = resp_json.get("choices")
        if choices and isinstance(choices, list) and len(choices) > 0:
            msg = choices[0].get("message", {})
            content = msg.get("content")

    if content is None:
        raise ValueError("Response missing content field")

    # content may be a list of blocks (Anthropic format) or a plain string
    if isinstance(content, list):
        text_parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(text_parts)
    elif isinstance(content, str):
        text = content
    else:
        raise ValueError(f"Unexpected content type: {type(content).__name__}")

    # Try to parse as JSON; fall back to raw text wrapping
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"verdict": "unknown", "score": 0, "notes": text}


# ── Pure core — image format detection ──────────────────────────────────────

def is_jpeg(b: bytes) -> bool:
    """Check if bytes start with a JPEG magic signature."""
    return len(b) >= 3 and b[:3] == _JPEG_MAGIC


def is_png(b: bytes) -> bool:
    """Check if bytes start with a PNG magic signature."""
    return len(b) >= 8 and b[:8] == _PNG_MAGIC


# ── Pure core — file I/O ────────────────────────────────────────────────────

def _project_root() -> Path:
    """Return the project root (parent of tools/)."""
    return Path(__file__).resolve().parent.parent.parent


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def save_image(b: bytes, out_path: str | Path) -> Path:
    """Save image bytes to *out_path*, rejecting paths outside the project root.

    "Project root" is the caller's CWD (so a consumer like GRIDLOCK can write into
    its own repo, e.g. `--out src/assets/x.jpg`) OR this tool's own repo root (for
    in-tree tests). Anything outside both is refused.

    Returns the resolved Path of the written file.
    Raises ValueError if the resolved path escapes both roots.
    """
    resolved = Path(out_path).resolve()
    allowed = [Path.cwd().resolve(), _project_root()]

    if not any(_within(resolved, root) for root in allowed):
        raise ValueError(
            f"Path {resolved} is outside project root {allowed[0]}. "
            "Refusing to write."
        )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b)
    return resolved


# ── Network layer (single isolation point) ──────────────────────────────────

def _post(url: str, headers: dict[str, str], payload: dict[str, Any],
           timeout: int = 120) -> dict[str, Any]:
    """POST JSON to *url* and return the parsed response.

    This is the ONLY network call in the module. Tests inject a fake.
    """
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Credential loading ──────────────────────────────────────────────────────

def _load_minimax_token(provider: str = "MiniMax") -> str:
    """Load the MiniMax API token from ccswitch_import (read-only).

    Never prints, logs, or returns the token for human display.
    Raises RuntimeError if the provider is not found or has no token.
    """
    # Import the sibling ccswitch_import module. Its dir is `tools/claude-worker`
    # (hyphen) — not an importable package name — so add that dir to sys.path and
    # import the module by its (non-hyphenated) module name.
    cw_dir = Path(__file__).resolve().parent.parent / "claude-worker"
    sys.path.insert(0, str(cw_dir))
    try:
        import ccswitch_import as cc
    finally:
        try:
            sys.path.remove(str(cw_dir))
        except ValueError:
            pass

    providers = [p for p in cc.parse_providers_from_db(cc.CCSWITCH_DB) if p.is_claude]
    match = next((p for p in providers if p.id == provider or p.name == provider), None)
    if match is None:
        raise RuntimeError(
            f"Provider '{provider}' not found in CCSwitch. "
            f"Available: {[p.name for p in providers]}"
        )
    if not match.auth_token:
        raise RuntimeError(f"Provider '{provider}' has no auth token configured.")
    return match.auth_token


# ── Subcommand: gen ─────────────────────────────────────────────────────────

def cmd_gen(args: argparse.Namespace) -> None:
    """Generate an image via MiniMax image-01."""
    token = _load_minimax_token(getattr(args, "provider", "MiniMax"))

    prompt = args.prompt
    style_prefix = getattr(args, "style_prefix", "") or ""
    aspect_ratio = getattr(args, "aspect_ratio", "1:1") or "1:1"

    payload = build_image_request(prompt, aspect_ratio=aspect_ratio,
                                   style_prefix=style_prefix)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = _post(IMAGE_GEN_URL, headers, payload)
    image_bytes = decode_image_payload(resp)

    out_path = Path(args.out)
    saved = save_image(image_bytes, out_path)
    print(f"Image saved to {saved}")


# ── Subcommand: curate ──────────────────────────────────────────────────────

def cmd_curate(args: argparse.Namespace) -> None:
    """Curate an image via M3-VL."""
    token = _load_minimax_token(getattr(args, "provider", "MiniMax"))

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Error: image not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    image_bytes = image_path.read_bytes()
    criteria = getattr(args, "criteria", "Is this image well-composed and usable as game art?")

    payload = build_curate_request(image_bytes, criteria=criteria)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = _post(CURATE_URL, headers, payload)
    verdict = parse_curate_verdict(resp)
    print(json.dumps(verdict, indent=2, ensure_ascii=False))


# ── Subcommand registry (TTS/STT seam) ─────────────────────────────────────

# Add future subcommands here: "tts": cmd_tts, "stt": cmd_stt
SUBCOMMANDS: dict[str, Any] = {
    "gen": cmd_gen,
    "curate": cmd_curate,
}


# ── CLI entry point ─────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="draw",
        description="MiniMax draw capability tool — image generation and curation.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # gen
    gen_p = subparsers.add_parser("gen", help="Generate an image via image-01")
    gen_p.add_argument("--prompt", required=True, help="Text prompt for image generation")
    gen_p.add_argument("--aspect-ratio", default="1:1", help="Aspect ratio (e.g. 1:1, 16:9)")
    gen_p.add_argument("--style-prefix", default="", help="Prepended style instruction")
    gen_p.add_argument("--out", required=True, help="Output file path (JPEG)")
    gen_p.add_argument("--provider", default="MiniMax", help="ccswitch provider name")

    # curate
    cur_p = subparsers.add_parser("curate", help="Curate an image via M3-VL")
    cur_p.add_argument("--image", required=True, help="Path to image file")
    cur_p.add_argument("--criteria", default="Is this image well-composed and usable as game art?",
                        help="Curation criteria prompt")
    cur_p.add_argument("--provider", default="MiniMax", help="ccswitch provider name")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.subcommand is None:
        parser.print_help()
        print(f"\nAvailable subcommands: {', '.join(SUBCOMMANDS.keys())}")
        sys.exit(0)

    handler = SUBCOMMANDS.get(args.subcommand)
    if handler is None:
        print(f"Unknown subcommand: {args.subcommand}", file=sys.stderr)
        print(f"Available: {', '.join(SUBCOMMANDS.keys())}", file=sys.stderr)
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
