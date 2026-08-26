#!/usr/bin/env bash
# PreToolUse guard — refuse a Read of a file already in context.
#
# Measured on gh-resolve run 20260825-234253: 99 of 157 Read calls re-read a
# path already read in the same iteration. Model decide-time before those
# redundant reads was 773s of the 6889s run (11.2%).
#
# Tier: DENY. The reason string names the path and says it is already in
# context so the agent redirects.
#
# Ledger: per-session JSON at ~/.ilk-data/runtime/read-ledger/<session-key>.json
# Override: ILK_READ_LEDGER_DIR (for tests redirecting to tmp_path).
# Session key selection: session_id → transcript_path → cwd+PPID.
#
# Fail open always — any error allows the call through.

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

# Only guard Read calls — everything else passes through.
[[ "$(field tool_name)" == "Read" ]] || allow

# Resolve the session key: session_id → transcript_path → cwd+PPID.
session_key=""
sid="$(field session_id)"
if [[ -n "${sid}" ]]; then
  session_key="${sid}"
else
  tpath="$(field transcript_path)"
  if [[ -n "${tpath}" ]]; then
    session_key="${tpath}"
  else
    hook_cwd="$(field cwd)"
    if [[ -n "${hook_cwd}" ]]; then
      session_key="${hook_cwd}__${PPID}"
    fi
  fi
fi

# No session key → fail open.
[[ -n "${session_key}" ]] || allow

# Ledger path.
ledger_dir="${ILK_READ_LEDGER_DIR:-${ILK_DATA_HOME:-${ILK_DATA_DIR:-$HOME/.ilk-data}}/runtime/read-ledger}"
mkdir -p "${ledger_dir}" 2>/dev/null || allow

ledger_file="${ledger_dir}/${session_key}.json"

# Resolve the target file path from tool_input.file_path.
target_path="$(field tool_input.file_path)"
[[ -n "${target_path}" ]] || allow

# Resolve to absolute path.
if [[ "${target_path}" != /* ]]; then
  hook_cwd="$(field cwd)"
  [[ -n "${hook_cwd}" ]] || allow
  target_path="${hook_cwd}/${target_path}"
fi

# Stat the file — if it doesn't exist, fail open (allow the read; the agent
# will see the error from the Read tool itself).
if [[ ! -e "${target_path}" ]]; then
  allow
fi

# Get current mtime_ns and size via Python (portable across macOS/Linux).
read -r current_mtime current_size <<<"$(python3 -c "
import os, sys
try:
    s = os.stat(sys.argv[1])
    print(f'{s.st_mtime_ns} {s.st_size}')
except Exception:
    print('0 0')
" "${target_path}" 2>/dev/null || echo "0 0")"

# If stat failed, fail open.
[[ "${current_mtime}" != "0" ]] || allow

# Read the existing ledger (or start empty).
ledger_json="{}"
if [[ -f "${ledger_file}" ]]; then
  ledger_json="$(cat "${ledger_file}" 2>/dev/null || echo "{}")"
fi

# Check if this path is already recorded and unchanged.
RESULT="$(LEDGER_JSON="${ledger_json}" TARGET="${target_path}" \
  MTIME="${current_mtime}" SIZE="${current_size}" \
  python3 - <<'PY' 2>/dev/null || echo "ERROR"
import json, os
try:
    ledger = json.loads(os.environ["LEDGER_JSON"])
except Exception:
    print("ALLOW")
    raise SystemExit

target = os.environ["TARGET"]
mtime = int(os.environ["MTIME"])
size = int(os.environ["SIZE"])

entry = ledger.get(target)
if entry is None:
    print("ALLOW")
elif entry.get("mtime_ns") == mtime and entry.get("size") == size:
    print("DENY")
else:
    print("ALLOW_CHANGED")
PY
)"

case "${RESULT}" in
  DENY)
    deny "Path ${target_path} is already in context — it was read earlier in this session and the file has not changed."
    ;;
  ALLOW_CHANGED)
    # File changed — update the ledger and allow.
    ;;
  ALLOW)
    # First read — record it.
    ;;
  *)
    # Parse error — fail open.
    allow
    ;;
esac

# Update the ledger with the current mtime/size.
LEDGER_FILE="${ledger_file}" TARGET="${target_path}" \
  MTIME="${current_mtime}" SIZE="${current_size}" \
  python3 - <<'PY' 2>/dev/null || true
import json, os
ledger_path = os.environ["LEDGER_FILE"]
target = os.environ["TARGET"]
mtime = int(os.environ["MTIME"])
size = int(os.environ["SIZE"])
try:
    with open(ledger_path) as f:
        ledger = json.load(f)
except Exception:
    ledger = {}
ledger[target] = {"mtime_ns": mtime, "size": size}
with open(ledger_path, "w") as f:
    json.dump(ledger, f)
PY

allow
