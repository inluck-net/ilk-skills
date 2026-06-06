#!/usr/bin/env bash
# Bootstrap a Worker Claude Code home (macOS / Linux).
#
# Creates a separate Claude Code home (default ~/.claude-worker) pinned to an
# explicit Anthropic-compatible provider, so a Worker Claude can run cheap
# implementation loops while the Planner Claude keeps the default ~/.claude
# home on its official provider. See docs/dual-claude-homes-design.md.
#
# SAFETY (non-negotiable, enforced by this script):
#   * Never reads, writes, or mutates ~/.claude, CCSwitch state, or
#     ~/.cc-switch/cc-switch.db. It only touches the worker home you name.
#   * Never extracts a provider token from anywhere. The token must be
#     supplied explicitly (--auth-token or ANTHROPIC_AUTH_TOKEN).
#   * Fails closed: if any of base URL / auth token / model is missing it
#     writes nothing, so the worker can never silently fall back to the
#     planner's official OAuth identity.
#   * Token values are masked in all output.
#
# Provider values (each via flag OR environment variable):
#   --base-url   <url>    ANTHROPIC_BASE_URL    e.g. https://provider.example.com/anthropic
#   --auth-token <token>  ANTHROPIC_AUTH_TOKEN  user-supplied provider token
#   --model      <id>     ANTHROPIC_MODEL       worker model id
#
# Flags:
#   --home <dir>        worker Claude home (default: ~/.claude-worker; also
#                       honors CLAUDE_WORKER_HOME)
#   --apply             actually create the home and write config files
#   --dry-run           preview only (the default; accepted for symmetry)
#   --link-skills       also link ilk skills/commands into the worker home
#                       (delegates to install.sh --claude-home; step 3)
#   --repo <dir>        repo root holding install.sh (default: inferred from
#                       this script's location)
#   --list-ccswitch-providers   list discovered CCSwitch Claude providers and
#                               exit (redacted; no secrets printed)
#   --from-ccswitch             import provider settings from CCSwitch (requires
#                               --provider or --interactive)
#   --provider <id>             CCSwitch provider id or name (with --from-ccswitch)
#   --interactive               pick a CCSwitch provider interactively
#   --allow-official            allow importing an official/Claude OAuth provider
#                               into the worker (refused by default to prevent
#                               the worker from accidentally using the planner's
#                               official identity)
#   --force                     overwrite provider settings even if an active
#                               worker/ilk run appears to be using this home
#   --clone-slot <n>            clone the base worker home into a per-slot home
#                               (e.g. ~/.claude-worker-2). Idempotent + lazy.
#   --from <base-home>          base home to clone from (default: ~/.claude-worker)
#                               Only meaningful with --clone-slot.
#   -h | --help                 show this help and exit
#
# Exit codes: 0 ok / dry-run, 2 usage error, 3 incomplete provider env.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# tools/claude-worker/bootstrap.sh -> repo root is two levels up.
DEFAULT_REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source shared worker-session helper (sentinel write/remove/test).
. "$SCRIPT_DIR/_worker_session.sh"

worker_home="${CLAUDE_WORKER_HOME:-$HOME/.claude-worker}"
base_url="${ANTHROPIC_BASE_URL:-}"
auth_token="${ANTHROPIC_AUTH_TOKEN:-}"
model="${ANTHROPIC_MODEL:-}"
apply=0
link_skills=0
repo_root="$DEFAULT_REPO_ROOT"
list_ccswitch=0
from_ccswitch=0
ccswitch_provider=""
interactive=0
allow_official=0
force=0
clone_slot=""
clone_from=""

usage() {
  sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# Mask a secret for logs: keep nothing but a length-bucketed placeholder so
# the same token can't be reconstructed and isn't even partially leaked.
mask_secret() {
  local v="$1"
  if [[ -z "$v" ]]; then
    echo "(missing)"
  else
    echo "***set (${#v} chars)***"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)        apply=1 ;;
    --dry-run)      apply=0 ;;
    --link-skills)  link_skills=1 ;;
    --home)
      shift
      [[ $# -eq 0 ]] && { echo "error: --home requires a directory argument" >&2; exit 2; }
      worker_home="$1"
      ;;
    --home=*)        worker_home="${1#--home=}" ;;
    --base-url)
      shift
      [[ $# -eq 0 ]] && { echo "error: --base-url requires a value" >&2; exit 2; }
      base_url="$1"
      ;;
    --base-url=*)    base_url="${1#--base-url=}" ;;
    --auth-token)
      shift
      [[ $# -eq 0 ]] && { echo "error: --auth-token requires a value" >&2; exit 2; }
      auth_token="$1"
      ;;
    --auth-token=*)  auth_token="${1#--auth-token=}" ;;
    --model)
      shift
      [[ $# -eq 0 ]] && { echo "error: --model requires a value" >&2; exit 2; }
      model="$1"
      ;;
    --model=*)       model="${1#--model=}" ;;
    --repo)
      shift
      [[ $# -eq 0 ]] && { echo "error: --repo requires a directory argument" >&2; exit 2; }
      repo_root="$1"
      ;;
    --repo=*)        repo_root="${1#--repo=}" ;;
    --list-ccswitch-providers) list_ccswitch=1 ;;
    --from-ccswitch) from_ccswitch=1 ;;
    --provider)
      shift
      [[ $# -eq 0 ]] && { echo "error: --provider requires a value" >&2; exit 2; }
      ccswitch_provider="$1"
      ;;
    --provider=*)    ccswitch_provider="${1#--provider=}" ;;
    --interactive)   interactive=1 ;;
    --allow-official) allow_official=1 ;;
    --force)         force=1 ;;
    --clone-slot)
      shift
      [[ $# -eq 0 ]] && { echo "error: --clone-slot requires a slot number" >&2; exit 2; }
      clone_slot="$1"
      ;;
    --clone-slot=*)  clone_slot="${1#--clone-slot=}" ;;
    --from)
      shift
      [[ $# -eq 0 ]] && { echo "error: --from requires a directory argument" >&2; exit 2; }
      clone_from="$1"
      ;;
    --from=*)        clone_from="${1#--from=}" ;;
    -h|--help)       usage; exit 0 ;;
    *)               echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

# --- Slot-home clone (--clone-slot <n>) -------------------------------------
# Clone the base worker home into a per-slot home (e.g. ~/.claude-worker-2).
# Idempotent (re-clone is a no-op / refresh) and lazy (created on first use).
# Accepts --model (V2 hook; currently ignored, documented for future use).
if [[ -n "$clone_slot" ]]; then
  # Resolve the base home to clone from.
  clone_base="${clone_from:-$HOME/.claude-worker}"
  case "$clone_base" in
    "~")   clone_base="$HOME" ;;
    "~/"*) clone_base="$HOME/${clone_base#\~/}" ;;
  esac
  case "$clone_base" in
    /*) ;;
    *)  clone_base="$(pwd)/$clone_base" ;;
  esac

  # Target: <base>-<slot> (e.g. ~/.claude-worker-2).
  slot_home="${clone_base}-${clone_slot}"

  if [[ ! -d "$clone_base" ]]; then
    echo "error: base worker home does not exist: $clone_base" >&2
    exit 1
  fi
  if [[ ! -f "$clone_base/settings.json" ]]; then
    echo "error: base worker home has no settings.json: $clone_base" >&2
    exit 1
  fi

  mkdir -p "$slot_home"

  # Copy settings.json (provider env block). Idempotent: overwrite on re-clone.
  cp -p "$clone_base/settings.json" "$slot_home/settings.json"
  echo "  cloned settings.json -> $slot_home/settings.json"

  # Minimal .claude.json: never clobber an existing one.
  if [[ ! -e "$slot_home/.claude.json" ]]; then
    cat > "$slot_home/.claude.json" <<'EOF'
{
  "mcpServers": {}
}
EOF
    echo "  wrote $slot_home/.claude.json (no MCP servers)"
  else
    echo "  kept existing $slot_home/.claude.json (left untouched)"
  fi

  # Link skills: symlink on POSIX, copy fallback.
  if [[ -d "$clone_base/skills" ]]; then
    if [[ -L "$slot_home/skills" ]]; then
      # Already a symlink — verify it points to the right place.
      current_target="$(readlink "$slot_home/skills")"
      if [[ "$current_target" == "$clone_base/skills" ]]; then
        echo "  skills symlink already correct"
      else
        rm "$slot_home/skills"
        ln -s "$clone_base/skills" "$slot_home/skills"
        echo "  updated skills symlink -> $clone_base/skills"
      fi
    elif [[ -d "$slot_home/skills" ]]; then
      echo "  kept existing skills directory (left untouched)"
    else
      ln -s "$clone_base/skills" "$slot_home/skills"
      echo "  linked skills -> $clone_base/skills"
    fi
  fi

  echo
  echo "Slot home ready: $slot_home"
  exit 0
fi

# --- CCSwitch provider discovery (--list-ccswitch-providers) -----------------
# List providers and exit early.  This is read-only and never exposes tokens.
if [[ $list_ccswitch -eq 1 ]]; then
  helper="$SCRIPT_DIR/ccswitch_import.py"
  if [[ ! -f "$helper" ]]; then
    echo "error: ccswitch_import.py not found at $helper" >&2
    exit 1
  fi
  python3 "$helper" list
  exit $?
fi

# Normalize the worker home: expand a leading ~ and make relative paths
# absolute. The directory need not exist yet (we may be creating it).
case "$worker_home" in
  "~")   worker_home="$HOME" ;;
  "~/"*) worker_home="$HOME/${worker_home#\~/}" ;;
esac
case "$worker_home" in
  /*) ;;
  *)  worker_home="$(pwd)/$worker_home" ;;
esac

# --- CCSwitch provider import (--from-ccswitch) ------------------------------
# Import provider settings from CCSwitch into the base_url / auth_token /
# model variables before the fail-closed validation below.  This is the only
# place where bootstrap reads CCSwitch state; it delegates to ccswitch_import.py
# which is strictly read-only.
if [[ $from_ccswitch -eq 1 ]]; then
  helper="$SCRIPT_DIR/ccswitch_import.py"
  if [[ ! -f "$helper" ]]; then
    echo "error: ccswitch_import.py not found at $helper" >&2
    exit 1
  fi

  # Resolve the provider: either from --provider flag or interactive picker.
  if [[ -z "$ccswitch_provider" && $interactive -eq 0 ]]; then
    echo "error: --from-ccswitch requires --provider <id> or --interactive" >&2
    exit 2
  fi

  if [[ $interactive -eq 1 ]]; then
    # Show the list and prompt the user to choose.
    echo "Available CCSwitch Claude providers:"
    echo
    python3 "$helper" list
    echo
    read -rp "Enter provider id or name: " ccswitch_provider
    if [[ -z "$ccswitch_provider" ]]; then
      echo "error: no provider selected" >&2
      exit 2
    fi

    # Preview the selection (redacted) and ask for confirmation.
    preview_json="$(python3 "$helper" export --provider "$ccswitch_provider")" || {
      echo "error: provider '$ccswitch_provider' not found" >&2
      exit 1
    }
    echo
    echo "Selected provider:"
    echo "$preview_json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
official = ' [official — refused by default]' if d.get('is_official') else ''
print(f'  name:       {d.get(\"name\", \"?\")}{official}')
print(f'  base_url:   {d.get(\"ANTHROPIC_BASE_URL\", \"(not set)\")}')
print(f'  auth_token: {d.get(\"ANTHROPIC_AUTH_TOKEN\", \"(redacted)\")}')
print(f'  model:      {d.get(\"ANTHROPIC_MODEL\", \"(not set)\")}')
"
    echo
    if [[ $apply -eq 0 ]]; then
      echo "Dry-run: would import this provider. Re-run with --apply to proceed."
      exit 0
    fi
    read -rp "Import this provider? [y/N] " confirm
    case "$confirm" in
      [yY]|[yY][eE][sS]) ;;
      *) echo "Aborted."; exit 0 ;;
    esac
  fi

  # Export the selected provider's env vars (--machine for raw token).
  export_json="$(python3 "$helper" export --provider "$ccswitch_provider" --machine)" || {
    echo "error: failed to export CCSwitch provider '$ccswitch_provider'" >&2
    exit 1
  }

  # Refuse official/Claude OAuth providers by default.  Importing an official
  # provider into the worker home would let the worker use the planner's OAuth
  # identity, defeating the purpose of dual homes.
  provider_is_official="$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
print('1' if d.get('is_official') else '0')
" "$export_json")"
  if [[ "$provider_is_official" == "1" && $allow_official -eq 0 ]]; then
    echo "error: provider '$ccswitch_provider' is an official/Claude OAuth provider." >&2
    echo "Importing it into the worker home would use the planner's official identity." >&2
    echo "Pass --allow-official to override (not recommended)." >&2
    exit 2
  fi

  # Parse the JSON output into shell variables.  Uses python3 for portability
  # (no jq dependency).
  eval "$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
print(f'base_url={json.dumps(d[\"ANTHROPIC_BASE_URL\"])}')
print(f'auth_token={json.dumps(d[\"ANTHROPIC_AUTH_TOKEN\"])}')
print(f'model={json.dumps(d[\"ANTHROPIC_MODEL\"])}')
" "$export_json")"

  echo "Imported provider '$ccswitch_provider' from CCSwitch."
  echo
fi

# --- fail-closed provider validation ---------------------------------------
# A worker home with an incomplete provider env would let Claude Code fall
# back to the planner's OAuth credential. Refuse to write anything in that
# case; report every missing field at once.
missing=()
[[ -z "$base_url" ]]   && missing+=("base URL (--base-url / ANTHROPIC_BASE_URL)")
[[ -z "$auth_token" ]] && missing+=("auth token (--auth-token / ANTHROPIC_AUTH_TOKEN)")
[[ -z "$model" ]]      && missing+=("model (--model / ANTHROPIC_MODEL)")

mode="DRY-RUN"
[[ $apply -eq 1 ]] && mode="APPLY"

echo "=== claude-worker bootstrap ($mode) ==="
echo "worker home:  $worker_home"
echo "base url:     ${base_url:-(missing)}"
echo "auth token:   $(mask_secret "$auth_token")"
echo "model:        ${model:-(missing)}"
echo "link skills:  $([[ $link_skills -eq 1 ]] && echo yes || echo no)"
echo

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: incomplete provider env — refusing to write a worker home that" >&2
  echo "would silently fall back to the planner's official OAuth identity." >&2
  echo "Missing:" >&2
  for m in "${missing[@]}"; do echo "  - $m" >&2; done
  exit 3
fi

# --- write worker config ----------------------------------------------------

# Escape a string for embedding inside a JSON double-quoted value. Handles
# the cases that actually occur in provider values (backslashes in Windows-y
# paths, quotes, and stray control chars) without shelling out to python.
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"   # backslash first, so we don't double-escape below
  s="${s//\"/\\\"}"   # double quote
  s="${s//$'\n'/\\n}" # newline
  s="${s//$'\r'/\\r}" # carriage return
  s="${s//$'\t'/\\t}" # tab
  printf '%s' "$s"
}

# Back up a pre-existing file before overwriting, mirroring the installer's
# .pre-ilk-<timestamp> convention, so a previously pinned token is never
# silently lost.
backup_if_present() {
  local f="$1"
  if [[ -e "$f" ]]; then
    local backup
    backup="${f}.pre-ilk-$(date +%Y%m%d-%H%M%S)"
    cp -p "$f" "$backup"
    echo "  backed up existing $(basename "$f") -> $backup"
  fi
}

write_worker_config() {
  mkdir -p "$worker_home"

  local settings_file="$worker_home/settings.json"
  local claude_json="$worker_home/.claude.json"

  # settings.json carries the provider auth token, so create it with
  # owner-only perms from the start (umask scoped to the subshell) and
  # back up any existing file first.
  backup_if_present "$settings_file"
  (
    umask 077
    cat > "$settings_file" <<EOF
{
  "env": {
    "ANTHROPIC_BASE_URL": "$(json_escape "$base_url")",
    "ANTHROPIC_AUTH_TOKEN": "$(json_escape "$auth_token")",
    "ANTHROPIC_MODEL": "$(json_escape "$model")"
  }
}
EOF
  )
  chmod 600 "$settings_file"
  echo "  wrote $settings_file (auth token $(mask_secret "$auth_token"), mode 600)"

  # Minimal .claude.json: worker starts with no MCP servers. Never clobber an
  # existing one — the user may have curated a small worker MCP set already.
  if [[ ! -e "$claude_json" ]]; then
    cat > "$claude_json" <<'EOF'
{
  "mcpServers": {}
}
EOF
    echo "  wrote $claude_json (no MCP servers)"
  else
    echo "  kept existing $claude_json (left untouched)"
  fi
}

# --- link skills/commands into the worker home ------------------------------
# Either run the custom-home installer or just print the exact command. The
# installer only runs destructively (--apply) when the user opted in with
# BOTH --apply and --link-skills; a dry-run bootstrap previews with --dry-run.
install_sh="$repo_root/install.sh"
link_cmd="bash \"$install_sh\" --apply --claude-home \"$worker_home\" --only-claude"

maybe_link_skills() {
  echo
  echo "Link ilk skills/commands into this worker home with:"
  echo "  $link_cmd"
  if [[ $link_skills -eq 0 ]]; then
    echo "  (pass --link-skills to run this automatically under --apply)"
    return
  fi
  # Only ever invoke the installer under --apply. A dry-run bootstrap stays
  # strictly non-mutating, so it just shows the command above.
  if [[ $apply -eq 0 ]]; then
    echo "  --link-skills: deferred (bootstrap is dry-run; re-run with --apply to link)"
    return
  fi
  if [[ ! -f "$install_sh" ]]; then
    echo "  warning: install.sh not found at $install_sh; skipping link." >&2
    return
  fi
  echo "  --link-skills: running installer (--apply) ..."
  bash "$install_sh" --apply --claude-home "$worker_home" --only-claude
}

if [[ $apply -eq 0 ]]; then
  echo "Would create worker home and write settings.json + .claude.json."
  maybe_link_skills
  echo
  echo "Note: after applying, restart any active Worker Claude sessions to pick"
  echo "up the new provider (changes apply to new sessions only)."
  echo
  echo "Dry-run complete. Re-run with --apply to bootstrap."
  exit 0
fi

# --- active worker run guard -------------------------------------------------
# If a worker/ilk loop is running against this home, overwriting the provider
# mid-run could break it.  Check for a sentinel left by claude-worker.sh.
# Uses identity-checked liveness (PID + start-time) from _worker_session.sh.
pid_file="$worker_home/running.pid"
if worker_session_active "$pid_file"; then
  sentinel_pid=""
  content="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ "$content" =~ ^pid=([0-9]+) ]]; then
    sentinel_pid="${BASH_REMATCH[1]}"
  fi
  label="${sentinel_pid:+PID $sentinel_pid}"
  label="${label:-worker}"
  if [[ $force -eq 0 ]]; then
    echo "WARNING: an active worker process ($label) appears to be" >&2
    echo "using this worker home.  Overwriting the provider settings now could" >&2
    echo "break the running session." >&2
    echo "Pass --force to overwrite anyway, or stop the worker first." >&2
    exit 2
  fi
  echo "WARNING: active worker $label detected; --force specified, proceeding anyway."
else
  # Stale or non-existent sentinel — clean it up.
  worker_sentinel_remove "$pid_file"
fi

write_worker_config
maybe_link_skills

echo
echo "Provider settings written.  Restart any active Worker Claude sessions"
echo "to pick up the new provider (changes apply to new sessions only)."
echo
echo "Done."
