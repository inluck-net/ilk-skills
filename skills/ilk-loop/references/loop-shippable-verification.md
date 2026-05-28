# Loop-Shippable Verification

The loop runs via Claude Code CLI (`run_ilk_loop_claude.ps1`).
Whether a verification step counts as "loop-shippable" depends on
which MCPs are registered for that CLI. Check at planning time:

```powershell
claude mcp list
```

The set of tools available to the loop = Claude Code's built-ins
(Bash/Edit/Read/Grep/Glob/Task/Write/etc.) PLUS whatever shows up in
`claude mcp list`. Anything outside that set is NOT loop-shippable
and must be moved to a "Manual user verification" section.

## Preferred: browser-enabled loop (Option A)

If the loop will frequently need browser verification, register
`chrome-devtools` MCP **once** for Claude Code CLI (the same MCP your
Cursor uses):

```powershell
claude mcp add chrome-devtools --scope user -- npx chrome-devtools-mcp@latest --browserUrl http://localhost:9222
```

Requires Chrome to be running with `--remote-debugging-port=9222`
(see `<skill-root>/browser-automation/SKILL.md`).

Once registered, the loop has full parity with Cursor: it can
`take_snapshot`, `click`, `type_text`, `evaluate_script`,
`list_network_requests`, `list_console_messages`, etc. Browser-based
ACs become normal loop steps — no manual section needed.

This is the **default assumption** when authoring plans. `/ilk-plan`
should run `claude mcp list` during planning and, if `chrome-devtools`
is present, freely include browser-based verification as in-loop steps.

## Fallback: CLI-only sub-plan (Option B)

Use when `chrome-devtools` (or whatever MCP a verification needs) is
**not** registered for Claude Code CLI. Structure the sub-plan with
two distinct verify sections:

1. **Steps** — 100% completable with the tools `claude mcp list`
   actually shows. The final step ships the plan after either:
   - A pytest run (preferred — covers the contract in-process), AND/OR
   - An ad-hoc verification script the loop authors in
     `<project>/scripts/verify_<topic>.py` that exercises the live HTTP
     wire contract via `requests` + `AccessToken.for_user(user)` (or
     equivalent). The script must always-restore any DB state it
     mutates (use `try/finally`).

2. **Manual user verification (run AFTER the loop ships)** — at the
   bottom of the sub-plan file, separate H2 section. The browser walk
   lives here, with explicit click-by-click instructions, expected
   toasts/responses, and a one-liner re-open instruction
   (`flip status back to in-progress, current_step=N`) for the user
   in case anything fails.

Acceptance criteria split: AC-1..AC-N covers loop-shippable bits;
the final AC ("works in Chrome") moves to the Manual section. The
loop ships once AC-1..N pass; the user takes the browser walk on
their own time.

## Decision matrix

| Sub-plan needs… | `chrome-devtools` registered? | Pattern |
|---|---|---|
| No browser at all | either | Normal sub-plan, no manual section |
| Browser walk | yes | Browser steps stay in loop (Option A) |
| Browser walk | no | Move to Manual section (Option B) |
| Some other MCP not in `claude mcp list` | n/a | Move that step to Manual section |

**Rule of thumb**: prefer Option A whenever you can — fewer human
hand-offs, higher fidelity, and the user only has to tell the agent
"continue the loop" once. Use Option B only when registering the
needed MCP isn't worth it for a one-off verification.
