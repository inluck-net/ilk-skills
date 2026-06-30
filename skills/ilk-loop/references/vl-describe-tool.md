# vl_describe — Worker-side contract

**File:** `skills/ilk-loop/scripts/vl_describe.py`

## When to use

You are running on a **text-only model** (e.g. `mimo-v2.5-pro`). To interpret
any image (screenshot, rendered board, diagram, chart), do **not** `Read` it
directly — the model returns a blank image. Instead, call `vl_describe`:

```
python <skill-root>/ilk-loop/scripts/vl_describe.py --image <abs-path> --question "<your specific question>"
```

- `<skill-root>` is your agent's skills directory (e.g. `~/.claude-worker/skills`).
- `<abs-path>` must be an absolute path to a local image (png/jpg/webp).
- `<question>` must be **specific** — the tool's value is asking precisely,
  not "describe this". Good: "Does the button have a blue border?" Bad: "What
  do you see?"

## Output

The tool prints one JSON object to stdout:

```jsonc
// success → exit 0
{ "ok": true, "answer": "<VL model's text>", "model": "mimo-v2.5",
  "image": "<path>", "usage": {"in": 1234, "out": 88} }

// failure → exit 1
{ "ok": false, "error": "<what failed>", "detail": "<actionable next step>" }
```

## Rules

1. **If `ok:false`**: surface the error to the user/orchestrator. Do **not**
   pretend you saw the image.
2. **If `ok:true`**: use the `answer` text as your visual judgment. The answer
   is the VL model's text response — treat it as authoritative for the narrow
   question you asked.
3. **Never blank-success**: the tool is designed to fail loud. If the gateway
   rejects image input or the answer is empty, you get `ok:false` — that's
   intentional. Report it.
4. **Question quality matters**: the VL model sees the image but has no task
   context. Frame your question with enough detail to get a useful answer.

## How it works

- Sends a single-shot `/v1/messages` POST with a base64 image content block
  to `mimo-v2.5` (VL model) via the worker's existing gateway.
- Reuses the worker's `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` from
  `<config-dir>/settings.json`. No new secrets needed.
- **Not** a nested `claude` agent — deterministic, no tool loop.

## Installation (post-ship manual action)

Append the rule from the "When to use" section above to your
`~/.claude-worker/CLAUDE.md` so every text-only worker session knows to call
this tool instead of reading images directly. This is machine-local, not a
repo commit.

## Related

- Design doc: `docs/future-work/2026-07-01-vl-describe-tool.md`
- Tests: `skills/ilk-loop/tests/test_vl_describe.py`
