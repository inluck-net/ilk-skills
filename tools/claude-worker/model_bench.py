#!/usr/bin/env python3
"""Compare worker models on one fixed, read-only, multi-turn repo question.

Each model answers the same question about this repo through a worker home,
with only Read/Grep/Glob available.  Runs are interleaved (model A, B, C, then
A, B, C again) so load from live loops on the shared endpoint hits every model
alike.  Per run it records the model the endpoint actually served (from
``modelUsage``, never the model's self-report), seconds per turn, output
tokens, thinking characters, and whether the answer names every fact in the
answer key.

    python3 tools/claude-worker/model_bench.py mimo-v2.5-pro mimo-v2.6-pro mimo-v2.6-flash
    python3 tools/claude-worker/model_bench.py --reps 2 --home ~/.claude-worker glm-5.3

The answer key is graded against ``skills/ilk-loop/scripts/ilk_paths.py``.
If a refactor moves those facts, the bench refuses to run rather than grade
every model wrong; update QUESTION and ANSWER_KEY together.

First used 2026-10-10 to choose a replacement for mimo-v2.5-pro, which MiMo
routes to mimo-v2.6-pro from 2026-10-14 18:00 (UTC+8).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KEY_SOURCE = REPO / "skills/ilk-loop/scripts/ilk_paths.py"

QUESTION = (
    "In this repo, find how ilk decides whether a linked git worktree gets its own "
    "project key or shares its main clone's key. Name the host switch file, the JSON "
    "key and value that turn on clone keying, the function that reads it (file:line), "
    "and what happens if the file is missing or unreadable. Answer in under 8 lines. "
    "Do not modify anything."
)
ANSWER_KEY = ["host-compat.json", "linked_worktree_key", "clone",
              "_linked_worktree_keys_follow_clone"]

# Pinned to the benched model too, so no side call uses the home's default.
MODEL_ENV_VARS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                  "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                  "ANTHROPIC_REASONING_MODEL", "ANTHROPIC_SMALL_FAST_MODEL")


def answer_key_drift(source: Path = KEY_SOURCE) -> list[str]:
    """Answer-key facts no longer present in the source the key is graded against."""
    text = source.read_text(encoding="utf-8")
    return [k for k in ANSWER_KEY if k not in text]


def parse_stream(lines: list[str]) -> dict:
    """Metrics from one ``claude -p --output-format stream-json`` log.

    Non-JSON lines (stderr warnings such as ``unrecognized_model``) are skipped.
    """
    init_model, result, think_chars = None, {}, 0
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init_model = ev.get("model")
        elif ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "thinking":
                    think_chars += len(block.get("thinking", ""))
        elif ev.get("type") == "result":
            result = ev
    usage = result.get("modelUsage") or {}
    out_tok = sum(m.get("outputTokens", 0) for m in usage.values())
    turns = result.get("num_turns") or 0
    api_s = (result.get("duration_api_ms") or 0) / 1000
    answer = result.get("result") or ""
    missing = [k for k in ANSWER_KEY if k not in answer]
    return {
        "init_model": init_model,
        "served_models": sorted(usage),
        "is_error": result.get("is_error"),
        "turns": turns,
        "api_s": round(api_s, 1),
        "s_per_turn": round(api_s / turns, 1) if turns else None,
        "out_tok": out_tok,
        "out_tok_per_turn": round(out_tok / turns) if turns else None,
        "think_chars": think_chars,
        "correct": bool(result) and not missing,
        "missing": missing,
    }


def run_one(model: str, rep: int, home: str, out_dir: Path, max_turns: int,
            effort: str | None) -> dict:
    env = dict(os.environ, CLAUDE_CONFIG_DIR=home)
    for var in MODEL_ENV_VARS:
        env[var] = model
    cmd = ["claude", "-p", "--model", model, "--output-format", "stream-json", "--verbose",
           "--tools", "Read,Grep,Glob", "--max-turns", str(max_turns)]
    if effort:
        cmd += ["--effort", effort]
    cmd.append(QUESTION)
    log = out_dir / f"{model}-r{rep}.jsonl"
    t0 = time.time()
    with open(log, "w") as fh:
        rc = subprocess.run(cmd, cwd=REPO, env=env, stdout=fh,
                            stderr=subprocess.STDOUT, timeout=900).returncode
    row = {"model": model, "rep": rep, "rc": rc, "wall_s": round(time.time() - t0, 1)}
    row.update(parse_stream(log.read_text().splitlines()))
    row["served_as_asked"] = row["served_models"] == [model]
    return row


def summarize(rows: list[dict]) -> str:
    """One line per model: medians over runs that were served as asked."""
    def median(xs):
        xs = sorted(x for x in xs if x is not None)
        return xs[len(xs) // 2] if xs else None

    lines = ["model | runs ok/total | correct | s/turn (median) | turns | out tok/turn | thinking chars"]
    for model in dict.fromkeys(r["model"] for r in rows):
        mine = [r for r in rows if r["model"] == model]
        ok = [r for r in mine if r["served_as_asked"] and not r["is_error"]]
        lines.append(" | ".join(str(x) for x in (
            model, f"{len(ok)}/{len(mine)}", f"{sum(r['correct'] for r in ok)}/{len(ok)}",
            median([r["s_per_turn"] for r in ok]), median([r["turns"] for r in ok]),
            median([r["out_tok_per_turn"] for r in ok]),
            median([r["think_chars"] for r in ok]))))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("models", nargs="+", help="model ids, as the home's endpoint names them")
    ap.add_argument("--home", default=os.path.expanduser("~/.claude-worker"),
                    help="Claude config dir whose provider serves the models")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--effort", choices=["low", "medium", "high"], default=None)
    ap.add_argument("--out", type=Path,
                    default=Path(os.environ.get("TMPDIR", "/tmp")) / f"model-bench-{time.strftime('%Y%m%d-%H%M%S')}")
    args = ap.parse_args(argv)

    drift = answer_key_drift()
    if drift:
        print(f"refusing: answer key facts {drift} are gone from {KEY_SOURCE}; "
              "update QUESTION and ANSWER_KEY together", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for rep in range(1, args.reps + 1):
        for model in args.models:
            row = run_one(model, rep, args.home, args.out, args.max_turns, args.effort)
            rows.append(row)
            print(json.dumps(row), flush=True)
    (args.out / "summary.json").write_text(json.dumps(rows, indent=1))
    print(summarize(rows))
    print(f"logs: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
