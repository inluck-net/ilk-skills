#!/usr/bin/env python3
"""Offline tests for MiniMax draw capability tool — pure core only.

Exercises request builders, image decoders, format detection, and file I/O
without any network calls. The `_post` helper is never invoked; tests inject
fakes where needed.

Part of sub-plan minimax-capability-tools (step 0).
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HERE = Path(__file__).resolve().parent
_TOOLS = _HERE.parent / "tools"
sys.path.insert(0, str(_TOOLS))

from minimax.draw import (
    build_image_request,
    build_curate_request,
    decode_image_payload,
    parse_curate_verdict,
    is_jpeg,
    is_png,
    save_image,
    cmd_gen,
    cmd_curate,
    main,
    SUBCOMMANDS,
    IMAGE_MODEL,
    CURATE_MODEL,
    IMAGE_GEN_URL,
    CURATE_URL,
)

sys.path.pop(0)

# ── Fixture: minimal valid JPEG (22 bytes) ──────────────────────────────────

# SOI + APP0 (JFIF header) + EOI — smallest valid JPEG structure.
_MINIMAL_JPEG_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2Q=="
_MINIMAL_JPEG = base64.b64decode(_MINIMAL_JPEG_B64)


# ── build_image_request ─────────────────────────────────────────────────────

class TestBuildImageRequest:
    def test_basic_prompt(self):
        req = build_image_request("a red circle")
        assert req["model"] == IMAGE_MODEL
        assert req["prompt"] == "a red circle"
        assert req["aspect_ratio"] == "1:1"
        assert req["response_format"] == "base64"
        assert req["n"] == 1

    def test_custom_aspect_ratio(self):
        req = build_image_request("landscape", aspect_ratio="16:9")
        assert req["aspect_ratio"] == "16:9"

    def test_style_prefix_prepended(self):
        req = build_image_request("a castle", style_prefix="pixel art")
        assert req["prompt"] == "pixel art a castle"

    def test_empty_style_prefix_ignored(self):
        req = build_image_request("a castle", style_prefix="")
        assert req["prompt"] == "a castle"

    def test_style_prefix_whitespace_only(self):
        req = build_image_request("a castle", style_prefix="   ")
        assert req["prompt"] == "a castle"


# ── build_curate_request ────────────────────────────────────────────────────

class TestBuildCurateRequest:
    def test_structure(self):
        req = build_curate_request(_MINIMAL_JPEG)
        assert req["model"] == CURATE_MODEL
        assert req["max_tokens"] == 1024
        assert len(req["messages"]) == 1

        msg = req["messages"][0]
        assert msg["role"] == "user"
        assert len(msg["content"]) == 2

        img_block = msg["content"][0]
        assert img_block["type"] == "image"
        assert img_block["source"]["type"] == "base64"
        assert img_block["source"]["media_type"] == "image/jpeg"
        # Verify the base64 data round-trips
        decoded = base64.b64decode(img_block["source"]["data"])
        assert decoded == _MINIMAL_JPEG

        text_block = msg["content"][1]
        assert text_block["type"] == "text"
        assert "Review this image" in text_block["text"]

    def test_custom_criteria(self):
        req = build_curate_request(_MINIMAL_JPEG, criteria="Is it blue?")
        text = req["messages"][0]["content"][1]["text"]
        assert "Is it blue?" in text

    def test_custom_model(self):
        req = build_curate_request(_MINIMAL_JPEG, model="custom-model")
        assert req["model"] == "custom-model"


# ── decode_image_payload ────────────────────────────────────────────────────

class TestDecodeImagePayload:
    def test_string_base64(self):
        resp = {"data": {"image_base64": _MINIMAL_JPEG_B64}}
        result = decode_image_payload(resp)
        assert result == _MINIMAL_JPEG

    def test_list_base64(self):
        resp = {"data": {"image_base64": [_MINIMAL_JPEG_B64, "other"]}}
        result = decode_image_payload(resp)
        assert result == _MINIMAL_JPEG

    def test_empty_list_raises(self):
        resp = {"data": {"image_base64": []}}
        with pytest.raises(ValueError, match="empty list"):
            decode_image_payload(resp)

    def test_missing_data_raises(self):
        with pytest.raises(ValueError, match="missing 'data'"):
            decode_image_payload({})

    def test_missing_image_base64_raises(self):
        with pytest.raises(ValueError, match="missing 'data.image_base64'"):
            decode_image_payload({"data": {}})

    def test_non_string_base64_raises(self):
        resp = {"data": {"image_base64": 42}}
        with pytest.raises(ValueError, match="Expected string"):
            decode_image_payload(resp)

    def test_invalid_base64_raises(self):
        resp = {"data": {"image_base64": "!!!not-base64!!!"}}
        with pytest.raises(ValueError, match="Invalid base64"):
            decode_image_payload(resp)


# ── parse_curate_verdict ────────────────────────────────────────────────────

class TestParseCurateVerdict:
    def test_anthropic_content_blocks(self):
        resp = {
            "content": [
                {"type": "text", "text": '{"verdict": "approve", "score": 8, "notes": "good"}'}
            ]
        }
        result = parse_curate_verdict(resp)
        assert result["verdict"] == "approve"
        assert result["score"] == 8

    def test_string_content(self):
        resp = {"content": '{"verdict": "reject", "score": 3, "notes": "blurry"}'}
        result = parse_curate_verdict(resp)
        assert result["verdict"] == "reject"

    def test_choices_format(self):
        resp = {
            "choices": [
                {"message": {"content": '{"verdict": "approve", "score": 7, "notes": "ok"}'}}
            ]
        }
        result = parse_curate_verdict(resp)
        assert result["verdict"] == "approve"

    def test_non_json_content_wraps(self):
        resp = {"content": "This is not JSON"}
        result = parse_curate_verdict(resp)
        assert result["verdict"] == "unknown"
        assert result["notes"] == "This is not JSON"

    def test_missing_content_raises(self):
        with pytest.raises(ValueError, match="missing content"):
            parse_curate_verdict({})


# ── is_jpeg / is_png ────────────────────────────────────────────────────────

class TestFormatDetection:
    def test_jpeg_detected(self):
        assert is_jpeg(_MINIMAL_JPEG) is True

    def test_png_not_jpeg(self):
        assert is_jpeg(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) is False

    def test_png_detected(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        assert is_png(png) is True

    def test_jpeg_not_png(self):
        assert is_png(_MINIMAL_JPEG) is False

    def test_short_bytes(self):
        assert is_jpeg(b"\xff\xd8") is False  # too short (need >=3)
        assert is_png(b"\x89PNG") is False     # too short (need >=8)

    def test_empty_bytes(self):
        assert is_jpeg(b"") is False
        assert is_png(b"") is False


# ── save_image ──────────────────────────────────────────────────────────────

class TestSaveImage:
    def test_save_within_project_root(self, tmp_path):
        # Use a path under the project root
        project_root = Path(__file__).resolve().parent.parent
        out = project_root / "scratch" / "test_output.jpg"
        try:
            result = save_image(_MINIMAL_JPEG, out)
            assert result.exists()
            assert result.read_bytes() == _MINIMAL_JPEG
        finally:
            out.unlink(missing_ok=True)
            out.parent.rmdir() if out.parent.exists() and not any(out.parent.iterdir()) else None

    def test_reject_outside_project_root(self, tmp_path):
        outside = tmp_path / "outside.jpg"
        with pytest.raises(ValueError, match="outside project root"):
            save_image(_MINIMAL_JPEG, outside)

    def test_creates_parent_dirs(self, tmp_path):
        project_root = Path(__file__).resolve().parent.parent
        nested = project_root / "scratch" / "nested" / "deep" / "test.jpg"
        try:
            result = save_image(_MINIMAL_JPEG, nested)
            assert result.exists()
            assert result.read_bytes() == _MINIMAL_JPEG
        finally:
            nested.unlink(missing_ok=True)
            # Clean up nested dirs
            for d in [nested.parent, nested.parent.parent, nested.parent.parent.parent]:
                try:
                    d.rmdir()
                except OSError:
                    break


# ── Subcommand registry ─────────────────────────────────────────────────────

class TestSubcommandRegistry:
    def test_gen_registered(self):
        assert "gen" in SUBCOMMANDS

    def test_curate_registered(self):
        assert "curate" in SUBCOMMANDS

    def test_tts_stt_seam_documented(self):
        """SUBCOMMANDS dict is the documented seam for future tts/stt."""
        # The dict exists and is extensible — this is the seam.
        assert isinstance(SUBCOMMANDS, dict)
        assert len(SUBCOMMANDS) >= 2

    def test_unknown_subcommand_exits_nonzero(self):
        """Unknown subcommand should exit non-zero (argparse rejects it)."""
        with patch("sys.argv", ["draw", "nonexistent"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # argparse exits 2 for invalid choice; our fallback also exits 1
            assert exc_info.value.code != 0

    def test_no_subcommand_shows_help_and_exits(self):
        """Running with no subcommand should show help and exit 0."""
        with patch("sys.argv", ["draw"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ── cmd_gen (injected _post) ────────────────────────────────────────────────

class TestCmdGen:
    """Test cmd_gen by injecting fake _post and _load_minimax_token."""

    def _make_args(self, **overrides):
        import argparse
        defaults = {
            "prompt": "a red circle",
            "aspect_ratio": "1:1",
            "style_prefix": "",
            "out": "scratch/test_gen.jpg",
            "provider": "MiniMax",
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_posts_to_image_gen_url(self):
        """cmd_gen should POST to the image-01 endpoint."""
        posted = {}
        fake_response = {"data": {"image_base64": _MINIMAL_JPEG_B64}}

        def fake_post(url, headers, payload, timeout=120):
            posted["url"] = url
            posted["headers"] = headers
            posted["payload"] = payload
            return fake_response

        args = self._make_args()
        with patch("minimax.draw._post", side_effect=fake_post), \
             patch("minimax.draw._load_minimax_token", return_value="fake-token"):
            cmd_gen(args)

        assert posted["url"] == IMAGE_GEN_URL
        assert posted["headers"]["Authorization"] == "Bearer fake-token"

    def test_model_is_image_01(self):
        """The payload model must be image-01."""
        posted = {}
        fake_response = {"data": {"image_base64": _MINIMAL_JPEG_B64}}

        def fake_post(url, headers, payload, timeout=120):
            posted["payload"] = payload
            return fake_response

        args = self._make_args()
        with patch("minimax.draw._post", side_effect=fake_post), \
             patch("minimax.draw._load_minimax_token", return_value="fake-token"):
            cmd_gen(args)

        assert posted["payload"]["model"] == IMAGE_MODEL

    def test_prompt_includes_style_prefix(self):
        """When style_prefix is set, the prompt should include it."""
        posted = {}
        fake_response = {"data": {"image_base64": _MINIMAL_JPEG_B64}}

        def fake_post(url, headers, payload, timeout=120):
            posted["payload"] = payload
            return fake_response

        args = self._make_args(prompt="a castle", style_prefix="pixel art")
        with patch("minimax.draw._post", side_effect=fake_post), \
             patch("minimax.draw._load_minimax_token", return_value="fake-token"):
            cmd_gen(args)

        assert posted["payload"]["prompt"] == "pixel art a castle"

    def test_saves_decoded_image(self):
        """cmd_gen should decode the response and save to --out."""
        fake_response = {"data": {"image_base64": _MINIMAL_JPEG_B64}}
        project_root = Path(__file__).resolve().parent.parent
        out = project_root / "scratch" / "test_gen_cmd.jpg"

        def fake_post(url, headers, payload, timeout=120):
            return fake_response

        args = self._make_args(out=str(out))
        try:
            with patch("minimax.draw._post", side_effect=fake_post), \
                 patch("minimax.draw._load_minimax_token", return_value="fake-token"):
                cmd_gen(args)

            assert out.exists()
            assert out.read_bytes() == _MINIMAL_JPEG
        finally:
            out.unlink(missing_ok=True)

    def test_no_live_network_call(self):
        """cmd_gen must never call _post live — tests inject a fake."""
        call_log = []
        fake_response = {"data": {"image_base64": _MINIMAL_JPEG_B64}}

        def tracking_post(url, headers, payload, timeout=120):
            call_log.append(("post", url))
            return fake_response

        args = self._make_args()
        project_root = Path(__file__).resolve().parent.parent
        out = project_root / "scratch" / "test_gen_no_live.jpg"
        args = self._make_args(out=str(out))
        try:
            with patch("minimax.draw._post", side_effect=tracking_post), \
                 patch("minimax.draw._load_minimax_token", return_value="fake-token"):
                cmd_gen(args)

            # _post was called exactly once (our fake), not urllib directly
            assert len(call_log) == 1
            assert call_log[0][0] == "post"
        finally:
            out.unlink(missing_ok=True)


# ── cmd_curate (injected _post) ─────────────────────────────────────────────

class TestCmdCurate:
    """Test cmd_curate by injecting fake _post and _load_minimax_token."""

    def _make_args(self, image_path, **overrides):
        import argparse
        defaults = {
            "image": str(image_path),
            "criteria": "Is this image well-composed and usable as game art?",
            "provider": "MiniMax",
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_posts_to_curate_url(self):
        """cmd_curate should POST to the M3-VL endpoint."""
        posted = {}
        fake_verdict = {"verdict": "approve", "score": 8, "notes": "good"}

        def fake_post(url, headers, payload, timeout=120):
            posted["url"] = url
            posted["headers"] = headers
            posted["payload"] = payload
            return {"content": [{"type": "text", "text": json.dumps(fake_verdict)}]}

        project_root = Path(__file__).resolve().parent.parent
        img_path = project_root / "scratch" / "test_curate_input.jpg"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(_MINIMAL_JPEG)
        try:
            args = self._make_args(img_path)
            with patch("minimax.draw._post", side_effect=fake_post), \
                 patch("minimax.draw._load_minimax_token", return_value="fake-token"):
                cmd_curate(args)

            assert posted["url"] == CURATE_URL
            assert posted["headers"]["Authorization"] == "Bearer fake-token"
        finally:
            img_path.unlink(missing_ok=True)

    def test_includes_image_bytes(self):
        """The curate payload should contain the image as base64."""
        posted = {}
        fake_verdict = {"verdict": "approve", "score": 8, "notes": "good"}

        def fake_post(url, headers, payload, timeout=120):
            posted["payload"] = payload
            return {"content": [{"type": "text", "text": json.dumps(fake_verdict)}]}

        project_root = Path(__file__).resolve().parent.parent
        img_path = project_root / "scratch" / "test_curate_img.jpg"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(_MINIMAL_JPEG)
        try:
            args = self._make_args(img_path)
            with patch("minimax.draw._post", side_effect=fake_post), \
                 patch("minimax.draw._load_minimax_token", return_value="fake-token"):
                cmd_curate(args)

            # Verify the image block contains our JPEG data
            msg = posted["payload"]["messages"][0]
            img_block = msg["content"][0]
            assert img_block["type"] == "image"
            decoded = base64.b64decode(img_block["source"]["data"])
            assert decoded == _MINIMAL_JPEG
        finally:
            img_path.unlink(missing_ok=True)

    def test_custom_criteria(self):
        """Custom criteria should appear in the text block."""
        posted = {}
        fake_verdict = {"verdict": "reject", "score": 2, "notes": "bad"}

        def fake_post(url, headers, payload, timeout=120):
            posted["payload"] = payload
            return {"content": [{"type": "text", "text": json.dumps(fake_verdict)}]}

        project_root = Path(__file__).resolve().parent.parent
        img_path = project_root / "scratch" / "test_curate_criteria.jpg"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(_MINIMAL_JPEG)
        try:
            args = self._make_args(img_path, criteria="Is it blue?")
            with patch("minimax.draw._post", side_effect=fake_post), \
                 patch("minimax.draw._load_minimax_token", return_value="fake-token"):
                cmd_curate(args)

            text_block = posted["payload"]["messages"][0]["content"][1]
            assert "Is it blue?" in text_block["text"]
        finally:
            img_path.unlink(missing_ok=True)

    def test_no_live_network_call(self):
        """cmd_curate must never call _post live — tests inject a fake."""
        call_log = []
        fake_verdict = {"verdict": "approve", "score": 7, "notes": "ok"}

        def tracking_post(url, headers, payload, timeout=120):
            call_log.append(("post", url))
            return {"content": [{"type": "text", "text": json.dumps(fake_verdict)}]}

        project_root = Path(__file__).resolve().parent.parent
        img_path = project_root / "scratch" / "test_curate_no_live.jpg"
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(_MINIMAL_JPEG)
        try:
            args = self._make_args(img_path)
            with patch("minimax.draw._post", side_effect=tracking_post), \
                 patch("minimax.draw._load_minimax_token", return_value="fake-token"):
                cmd_curate(args)

            assert len(call_log) == 1
            assert call_log[0][0] == "post"
        finally:
            img_path.unlink(missing_ok=True)
