# vl_describe — vision as a tool (not a model switch)

**Status:** design sketch (2026-07-01). Supersedes the deprecated
`vl-context-aware-planning` model-switch approach.

## Problem

The default worker model (`mimo-v2.5-pro`) is **text-only**. When a sub-plan's
verification needs to *see* a screenshot (e.g. a render `AC-VIS`), the agent
does `Read(board.png)` and gets back `@{type=image; source=}` — an empty image —
so it can't make the visual judgment, makes no progress, and the run dies
(then gets mislabeled `dependency-unreachable`).

## Decision

Rather than swap the whole iteration onto the VL model (`mimo-v2.5`, 200K ctx,
weaker coder, requires plan-time `requires_vl` detection + runner surgery),
expose vision as a **callable tool**. The text worker stays on the strong
1M-ctx coder and delegates only the narrow "look at this image and answer X"
to the VL model. Additive (no iteration-loop changes), finer-grained (callable
mid-iteration), and reusable.

## Alternatives considered: model switch vs. VL-as-a-tool

Two ways to give a text-only worker vision. We chose the tool.

| | **Model switch** (per-iteration `--model`) | **VL-as-a-tool** (chosen) |
|---|---|---|
| Coder quality / context | whole sub-plan runs on VL — 200K ctx, weaker coder | stays on the strong text model (1M ctx); only the glance is offloaded |
| Granularity | per-sub-plan / per-iteration (one model per `claude` process) | **any point mid-iteration** — call whenever an image appears |
| Detection needed | yes — `requires_vl` field + plan-lint classification + sizing gate (the whole Phase-1 work) | no — agent calls the tool when it has an image; implicit & runtime |
| Blast radius | **edits `run_ilk_loop_claude.ps1`** model resolution — the live, scheduler-shared, PS-runtime-fragile runner | **additive**: a standalone script + a prompt contract; iteration loop untouched |
| Information fidelity | VL model sees the image *with full task context*, judges holistically | lossy: VL returns **text**; orchestrator must frame a good question and trust the words |
| Generality | vision-specific | reusable "specialist-as-a-tool" primitive |
| Cost / latency | one model, no extra hop | extra subprocess + inference per image call |

**Why the tool wins for this toolkit:**

1. Keeps the strong coder + 1M ctx for the ~95% that's code/text; offloads only
   the brief vision glance. Forcing an entire sub-plan onto the 200K/weaker VL
   model just to enable one screenshot check is a blunt instrument.
2. **Lower blast radius** — doesn't touch the runner's model resolution, the
   file class behind repeated PS-runtime / scheduler-cascade incidents. Additive
   beats surgery.
3. Finer granularity — vision at any point in an iteration, not gated to whole
   sub-plans.
4. Makes the unshipped Phase-1 VL-context work unnecessary (no `requires_vl`
   detection, no 200K sizing discipline). Net simplification.

**The one real downside — lossy text round-trip** — is acceptable here:
verification questions are *specific* ("does the `/`-drawn mirror send the
eastbound beam north?"), so the orchestrator frames a precise query and gets a
focused answer. The tool is weaker only for *open-ended* visual understanding
("look and tell me what's wrong"), where the text model doesn't know what to
ask. **Model switch would only win if a whole sub-plan is fundamentally
vision-centric** (mostly "reason over many images") — rare here; handle that
edge case by splitting the visual work into its own VL-run sub-plan if it ever
arises.

## The tool

A cross-platform Python CLI at `skills/ilk-loop/scripts/vl_describe.py`,
invoked by the worker through its existing **Bash** tool.

### Invocation

```
python vl_describe.py --image <abs-path> --question "<specific question>" \
    [--model mimo-v2.5] [--config-dir <claude config dir>] \
    [--max-tokens 1024] [--timeout 60]
```

| arg | req | meaning |
|---|---|---|
| `--image` | yes | absolute path to a local image (png/jpg/webp); validated to exist |
| `--question` | yes | the *specific* question — the tool's value is asking precisely, not "describe this" |
| `--model` | no | VL model id (default `mimo-v2.5`) |
| `--config-dir` | no | Claude config dir holding the gateway creds (default: `$CLAUDE_WORKER_HOME` → `~/.claude-worker`) |
| `--max-tokens` / `--timeout` | no | bound the call |

### How it gets creds (key point — reuse the worker's gateway)

Read the **same** endpoint + token the worker already uses, from
`<config-dir>/settings.json` `env` block:
`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`. The only thing that differs from
the worker's normal calls is `model = mimo-v2.5` (VL) instead of
`mimo-v2.5-pro` (text). No new secrets, no new endpoint.

**Implementation = a single-shot `/v1/messages` POST** with one image content
block + the question (NOT a nested `claude` agent — we want a deterministic
function, no tool loop). Base64-encode the image; send the Anthropic-format
`{"type":"image","source":{"type":"base64",...}}` block.

### Output (machine-parseable, UTF-8)

stdout, always one JSON object; `stdout.reconfigure(encoding="utf-8")` (zh-CN
GBK console otherwise crashes on non-ASCII answers):

```jsonc
// success → exit 0
{ "ok": true, "answer": "<VL model's text>", "model": "mimo-v2.5",
  "image": "<path>", "usage": {"in": 1234, "out": 88} }
// failure → exit 1
{ "ok": false, "error": "<what failed>", "hint": "<actionable next step>" }
```

**Critical:** if the gateway rejects the image (vision unsupported), return
`ok:false` with a clear error — never an empty/blank success. That blank-success
is the original bug; the tool must fail *loud*.

## The contract (where the instruction lives)

The text worker must know to call this instead of `Read`-ing an image. Install
a standing instruction into the **worker's** `~/.claude-worker/CLAUDE.md` (the
worker always runs the text model, so it's unconditional there; the planner —
real, vision-capable Claude — does **not** get it):

> You are running on a text-only model. To interpret any image (screenshot,
> rendered board, diagram, chart), do **not** `Read` it directly — run
> `python <skill-root>/ilk-loop/scripts/vl_describe.py --image <path>
> --question "<your specific question>"` and use the returned `answer`. If it
> returns `ok:false`, surface the error; do not pretend you saw the image.

Plus a one-line pointer in `subplan-template.md`'s `AC-VIS` section so visual
sub-plans reference it.

## Load-bearing unknown (verify FIRST in the plan)

We've confirmed `mimo-v2.5-pro` is text-only. We are **assuming** the same
`token-plan-cn.xiaomimimo.com/anthropic` gateway accepts image content blocks
for `mimo-v2.5`. **Step 0 of the plan must be a diagnostic spike:** POST a known
fixture image (e.g. one with the text "HELLO") to `mimo-v2.5` via the gateway
and confirm a sensible answer. If the gateway doesn't support image input for
`mimo-v2.5`, this whole approach needs a different VL endpoint — so prove it
before building anything else.

## Verification (for the plan's gate)

- A fixture image with a known, unambiguous answer (rendered text / a colored
  shape) → assert the returned `answer` contains the expected token (loose
  match — VL output is non-deterministic).
- An error-path test: missing file, and a forced gateway/vision-unsupported
  error → assert `ok:false` + non-zero exit (the fail-loud guarantee).
- Mock the HTTP layer for the deterministic unit gate; keep one real-gateway
  smoke (network-gated) as a separate, clearly-labeled check.

## Knock-on

- The `vl-context-aware-planning` master + its 3 sub-plans (`requires_vl`
  detection, plan-lint VL classification, ilk-plan VL wiring, 200K sizing) are
  **deprecated** — unnecessary when the model never switches.
- The `collect.py` `dependency-unreachable` misclassification fix is
  independent and still wanted (a vision/model-incapability stall must not be
  mislabeled as a missing MCP).
