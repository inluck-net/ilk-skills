#!/usr/bin/env bash
# Switch the Worker Claude home to another provider — a CCSwitch-style picker.
#
# CCSwitch flips the *planner's* provider (~/.claude). This flips the
# *worker's* (~/.claude-worker), reading the same CCSwitch provider list so
# there is one place to store keys. It is a thin front-end over bootstrap.sh:
# every write, backup, merge, and fail-closed check lives there.
#
# Usage:
#   claude-worker-switch                 interactive picker (default)
#   claude-worker-switch <id|name>       switch directly (name is case-
#                                        insensitive; a unique substring works)
#   claude-worker-switch -l | --list     show providers and the current one
#   claude-worker-switch -c | --current  print the worker's current provider
#
# Flags:
#   --home <dir>   worker Claude home (default: ~/.claude-worker; also honors
#                  CLAUDE_WORKER_HOME)
#   -n|--dry-run   resolve and preview, write nothing
#   -h|--help      this help
#
# Exit codes: 0 ok, 2 usage / no match / ambiguous, 3 write refused.

set -euo pipefail

script_source="${BASH_SOURCE[0]}"
while [[ -L "$script_source" ]]; do
  script_dir="$(cd "$(dirname "$script_source")" && pwd)"
  script_source="$(readlink "$script_source")"
  [[ "$script_source" != /* ]] && script_source="$script_dir/$script_source"
done
SCRIPT_DIR="$(cd "$(dirname "$script_source")" && pwd)"

HELPER="$SCRIPT_DIR/ccswitch_import.py"
BOOTSTRAP="$SCRIPT_DIR/bootstrap.sh"

worker_home="${CLAUDE_WORKER_HOME:-$HOME/.claude-worker}"
mode="pick"
target=""
dry_run=0

usage() { sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)     usage; exit 0 ;;
    -l|--list)     mode="list" ;;
    -c|--current)  mode="current" ;;
    -n|--dry-run)  dry_run=1 ;;
    --home)        shift; [[ $# -eq 0 ]] && { echo "error: --home needs a directory" >&2; exit 2; }; worker_home="$1" ;;
    --home=*)      worker_home="${1#--home=}" ;;
    -*)            echo "error: unknown flag: $1" >&2; usage >&2; exit 2 ;;
    *)             target="$1"; mode="direct" ;;
  esac
  shift
done

case "$worker_home" in
  "~")   worker_home="$HOME" ;;
  "~/"*) worker_home="$HOME/${worker_home#\~/}" ;;
esac

for f in "$HELPER" "$BOOTSTRAP"; do
  [[ -f "$f" ]] || { echo "error: missing $f" >&2; exit 2; }
done
command -v python3 >/dev/null 2>&1 || { echo "error: python3 required" >&2; exit 2; }

settings_file="$worker_home/settings.json"

# Print the worker's active base_url + model (never the token).
show_current() {
  if [[ ! -f "$settings_file" ]]; then
    echo "current: (no worker home at $worker_home)"
    return
  fi
  python3 - "$settings_file" <<'PYCUR'
import json, sys
try:
    env = (json.load(open(sys.argv[1])).get("env") or {})
except Exception:
    print("current: (unreadable settings.json)")
    sys.exit(0)
url = env.get("ANTHROPIC_BASE_URL", "(unset)")
model = env.get("ANTHROPIC_MODEL", "(unset)")
print(f"current: {model}  @  {url}")
PYCUR
}

# Fetch the CCSwitch provider list as JSON, or fail with a message that names
# the fallback. A host without CCSwitch (a headless runner) is an expected
# state, so it must not surface as a traceback.
providers_json() {
  local out
  if ! out="$(python3 "$HELPER" list --format json 2>&1)"; then
    echo "error: cannot read the CCSwitch provider list on this host." >&2
    echo "$out" | sed 's/^/  /' >&2
    echo >&2
    echo "This host has no CCSwitch. Set the worker provider directly:" >&2
    echo "  bash \"$BOOTSTRAP\" --apply \\" >&2
    echo "    --home \"$worker_home\" \\" >&2
    echo "    --base-url <url> --auth-token <token> --model <id>" >&2
    return 2
  fi
  printf '%s' "$out"
}

# Render the provider table, marking the one the worker is on.
list_providers() {
  local rows_json
  rows_json="$(providers_json)" || return 2
  CW_ROWS="$rows_json" python3 - "$settings_file" <<'PYLIST'
import json, os, sys
rows = json.loads(os.environ["CW_ROWS"])
rows = [r for r in rows if r.get("base_url") and r.get("model")]
try:
    cur = (json.load(open(sys.argv[1])).get("env") or {}).get("ANTHROPIC_BASE_URL", "")
except Exception:
    cur = ""
width = max((len(r["name"]) for r in rows), default=4)
for i, r in enumerate(rows, 1):
    mark = "*" if cur and r["base_url"] == cur else " "
    print(f" {mark} {i:2}) {r['name']:<{width}}  {r['model']:<22}  {r['base_url']}")
PYLIST
}

# Resolve a user string to exactly one provider id. Tries id, then exact
# name, then unique case-insensitive substring; ambiguity is an error rather
# than a guess.
resolve_target() {
  local rows_json
  rows_json="$(providers_json)" || return 2
  CW_TARGET="$1" CW_ROWS="$rows_json" python3 - <<'PYRES'
import json, os, sys
q = os.environ["CW_TARGET"]
rows = json.loads(os.environ["CW_ROWS"])
rows = [r for r in rows if r.get("base_url") and r.get("model")]

if q.isdigit():
    i = int(q)
    if 1 <= i <= len(rows):
        print(rows[i - 1]["id"]); sys.exit(0)
    print(f"error: no provider #{i} (1-{len(rows)})", file=sys.stderr); sys.exit(2)

for r in rows:
    if r["id"] == q or r["name"].lower() == q.lower():
        print(r["id"]); sys.exit(0)

hits = [r for r in rows if q.lower() in r["name"].lower()]
if len(hits) == 1:
    print(hits[0]["id"]); sys.exit(0)
if not hits:
    print(f"error: no provider matches '{q}'", file=sys.stderr); sys.exit(2)
print(f"error: '{q}' is ambiguous:", file=sys.stderr)
for r in hits:
    print(f"  {r['name']}", file=sys.stderr)
sys.exit(2)
PYRES
}

export CW_SCRIPT_DIR="$SCRIPT_DIR"

echo "=== claude-worker provider ==="
echo "worker home: $worker_home"
show_current
echo

if [[ "$mode" == "current" ]]; then
  exit 0
fi

if [[ "$mode" != "direct" ]]; then
  list_providers || exit 2
  echo
fi

if [[ "$mode" == "list" ]]; then
  exit 0
fi

if [[ "$mode" == "pick" ]]; then
  [[ -t 0 ]] || { echo "error: not a terminal — pass a provider name or id" >&2; exit 2; }
  read -rp "Switch to [number/name, empty to cancel]: " target
  [[ -z "$target" ]] && { echo "Cancelled."; exit 0; }
fi

provider_id="$(resolve_target "$target")" || exit 2

# An active worker run holds a sentinel; bootstrap refuses to overwrite under
# it unless forced, so the running session's provider can't change mid-flight.
bootstrap_args=(--from-ccswitch --provider "$provider_id" --home "$worker_home")
if [[ $dry_run -eq 1 ]]; then
  bootstrap_args+=(--dry-run)
else
  bootstrap_args+=(--apply)
fi

bash "$BOOTSTRAP" "${bootstrap_args[@]}"

if [[ $dry_run -eq 0 ]]; then
  echo
  show_current
  echo "Restart any running worker session to pick this up."
fi
