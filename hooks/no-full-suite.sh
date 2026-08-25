#!/usr/bin/env bash
# Personal guardrail — no unscoped full test-suite runs.
#
# Chad, 2026-08-10: "why are you always running unittests ... don't waste my
# time and tokens." Advisory guidance had already failed four documents deep
# (~/.claude/CLAUDE.md:16-17 disqualifies filtered runs as evidence;
# decomposition-principles.md:793-806 asks for a baseline-green suite), so this
# is the harness-enforced version.
#
# Tier: DENY. The reason string is fed back to Claude with the cheap
# alternatives, so the agent redirects instead of prompting the operator.
#
# Escape hatches (both silent):
#   ILK_ALLOW_FULL_SUITE=1  in the command, or exported in the environment
#   run it yourself with the `!` prefix
#
# Denies only UNSCOPED invocations. A run that names a path, or uses
# --collect-only / -k / -m / --lf / --ff, is already cheap and passes through.

set -uo pipefail

allow() { exit 0; }

deny() {
  DENY_REASON="$1" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": os.environ["DENY_REASON"],
    }
}))
PY
  exit 0
}

event="$(cat)"
[[ -n "${event}" ]] || allow

field() {
  EVENT_JSON="${event}" FIELD_PATH="$1" python3 - <<'PY' 2>/dev/null || true
import json, os
cur = json.loads(os.environ["EVENT_JSON"])
for part in os.environ["FIELD_PATH"].split("."):
    if not isinstance(cur, dict):
        cur = ""
        break
    cur = cur.get(part, "")
print(cur if isinstance(cur, str) else "")
PY
}

[[ "$(field tool_name)" == "Bash" ]] || allow

cmd="$(field tool_input.command)"
[[ -n "${cmd}" ]] || allow

# Normalize `python -m pytest` -> `pytest` BEFORE any flag matching. Otherwise
# python's module flag (`-m pytest`) is indistinguishable from pytest's marker
# selector (`-m slow`), and every bare `python3 -m pytest -q` reads as scoped.
# (Done in python, not sed: BSD sed on macOS has no \b and silently no-ops.)
norm="$(RAW="${cmd}" python3 - <<'PY' 2>/dev/null || printf '%s' "${cmd}"
import os, re
print(re.sub(
    r"\b(?:python[\d.]*|uv\s+run|poetry\s+run)\s+-m\s+(?:pytest|py\.test)\b",
    "pytest",
    os.environ["RAW"],
))
PY
)"
[[ -n "${norm}" ]] || norm="${cmd}"

# Strip quoted substrings before runner detection. A command that merely
# *mentions* a runner inside an argument — prose in a --gap string, a grep
# pattern, a commit message — is not running it. Without this the hook fires on
# `backlog add --gap "...19 separate pytest invocations..."` (observed
# 2026-08-10, first live false positive).
bare="$(NORM="${norm}" python3 - <<'PY' 2>/dev/null || printf '%s' "${norm}"
import os, re
s = os.environ["NORM"]
# Heredocs FIRST: quote-stripping would mangle the <<'TAG' marker (the quoted
# TAG is itself a quoted string), after which the body survives into the scan
# and any fixture text mentioning a runner reads as an invocation. Observed
# 2026-08-10 on a `cat >> test_file <<'PYEOF'` writing pytest fixtures.
s = re.sub(r"<<-?\s*'?[A-Za-z_][A-Za-z0-9_]*'?.*", " ", s, flags=re.S)
s = re.sub(r"'[^']*'", " ", s)      # single-quoted
s = re.sub(r'"[^"]*"', " ", s)      # double-quoted
print(s)
PY
)"
[[ -n "${bare}" ]] || bare="${norm}"

# --- is this a test-suite runner at all? -------------------------------------
runner=""
case "${bare}" in
  *pytest*)                       runner="pytest" ;;
  *"npm test"*|*"npm run test"*)  runner="npm test" ;;
  *"yarn test"*)                  runner="yarn test" ;;
  *"bun test"*)                   runner="bun test" ;;
  *"cargo test"*)                 runner="cargo test" ;;
  *"go test ./..."*)              runner="go test ./..." ;;
esac
[[ -n "${runner}" ]] || allow

# --- already-cheap forms pass through ----------------------------------------
# Collection-only, or a run already narrowed to a subset.
case "${bare}" in
  *--collect-only*|*" --co"*|*" -k "*|*" -k'"*|*' -k"'*|*" -m "*|*--lf*|*--last-failed*|*--ff*|*--failed-first*)
    allow ;;
esac

# A positional path argument scopes the run. Walk the pytest/bun/cargo tokens
# and look for any non-flag argument that is not the runner or an interpreter.
scoped="$(CMD="${bare}" RUNNER="${runner}" python3 - <<'PY' 2>/dev/null || echo no
import os, re, shlex

cmd = os.environ["CMD"]
# Only inspect the segment containing the runner, so `cd x && pytest` and
# `pytest ... | tail` do not leak neighbouring words in as positionals.
segment = re.split(r"&&|\|\||;|\|", cmd)
segment = next((s for s in segment if "pytest" in s or "test" in s), cmd)

try:
    tokens = shlex.split(segment)
except ValueError:
    print("no")
    raise SystemExit

NOISE = {
    "pytest", "py.test", "python", "python3", "-m", "npm", "yarn", "bun",
    "cargo", "go", "run", "test", "timeout", "gtimeout", "uv", "poetry",
    "nice", "env", "exec",
}

positional = []
skip_next = False
for tok in tokens[1:] if tokens else []:
    if skip_next:
        skip_next = False
        continue
    if tok.startswith("-"):
        # Flags that consume a following value.
        if tok in {"-p", "-o", "-c", "-W", "-n", "--rootdir", "--junitxml"}:
            skip_next = True
        continue
    if tok in NOISE or tok.isdigit():
        continue
    positional.append(tok)

print("yes" if positional else "no")
PY
)"
[[ "${scoped}" == "yes" ]] && allow

# --- escape hatch: requires backgrounding ------------------------------------
# The hatch exists so a deliberate full-suite gate can proceed.  But a
# foreground broad run cannot succeed — the harness auto-backgrounds at 600s
# and with | tail the pipeline never flushes, costing the full 600s for 0 bytes.
has_hatch=0
case "${cmd}" in *ILK_ALLOW_FULL_SUITE=1*) has_hatch=1 ;; esac
[[ "${ILK_ALLOW_FULL_SUITE:-0}" == "1" ]] && has_hatch=1
if [[ "${has_hatch}" == "1" ]]; then
  trimmed="${bare%"${bare##*[![:space:]]}"}"
  case "${trimmed}" in
    *\&) allow ;;
    *)
      deny "The ILK_ALLOW_FULL_SUITE=1 escape hatch requires a backgrounded run — a foreground broad gate hits the 600s ceiling and returns 0 bytes.

Background the command instead:
  python3 -m pytest -q > /tmp/pytest_gate.log 2>&1 &
  bash skills/ilk-loop/scripts/wait_for_background_output.sh /tmp/pytest_gate.log

Or let the loop's own local_checks run the suite."
      ;;
  esac
fi

deny "Blocked by your own no-full-suite guardrail (~/.claude/hooks/no-full-suite.sh): this is an unscoped '${runner}' run over the whole project.

You asked for this on 2026-08-10 — unprompted full-suite runs waste time and tokens, and advisory guidance in CLAUDE.md had not held.

Use a cheaper form instead, in this order:
  1. counts / collection errors    ->  add --collect-only -q   (seconds, tiny output)
  2. verifying one specific claim  ->  name the path: pytest path/to/test_x.py
  3. 'does X exist' / 'when did Y land'  ->  grep or git log, not a test run
  4. a baseline for a planning gate      ->  record it from --collect-only and let
     the loop's own local_checks run the suite; do not run it from a planning session

If the full run is genuinely what's wanted, prefix the command with
ILK_ALLOW_FULL_SUITE=1, or ask the operator to run it themselves with '!'."
