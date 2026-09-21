#!/usr/bin/env bash
# Launch Claude Code under a Worker home (macOS / Linux).
#
# Wraps `claude` so it runs against a separate Worker Claude home (default
# ~/.claude-worker) pinned to an explicit Anthropic-compatible provider, while
# the Planner Claude keeps the default ~/.claude home on its official provider.
# See docs/architecture/dual-claude-homes-design.md. Create the worker home first with
# tools/claude-worker/bootstrap.sh.
#
# The wrapper sets two environment variables before launching:
#   CLAUDE_CONFIG_DIR  -> the worker home (selects settings.json, .claude.json)
#   ILK_SKILL_HOME     -> <worker home>/skills (selects ilk skills/commands)
#
# SAFETY (non-negotiable, enforced by this script):
#   * Never reads, writes, or mutates ~/.claude, CCSwitch state, or any
#     cc-switch.db. It only reads the worker home you name.
#   * Fails closed: refuses to launch unless the worker home, its
#     settings.json, every required ANTHROPIC_* value, and the ilk-runner
#     skill are all present — so the worker can never silently fall back to
#     the planner's official OAuth identity.
#   * Token values are masked in all output; the raw token is never printed.
#
# Flags (anything else is passed through to `claude`):
#   --home <dir>        worker Claude home (default: ~/.claude-worker; also
#                       honors CLAUDE_WORKER_HOME)
#   --claude-bin <path> Claude Code executable (default: CLAUDE_BIN, then PATH,
#                       then common macOS/Linux install shims)
#   --preflight-only    run all checks, print the active worker home, exit 0
#                       without launching claude
#   --no-skip-permissions  disable the default --dangerously-skip-permissions
#                       injection; worker launches with normal permission prompts
#   --dry-run           run preflight, print resolved claude bin + assembled args,
#                       exit 0 without launching claude
#   --quiet             suppress the informational banner (worker home, provider
#                       env, claude bin, "Launching claude" lines). Preflight
#                       failures still print every problem to stderr, exit 3.
#   -h | --help         show this help and exit
#
# Exit codes: 0 ok / preflight ok, 2 usage error, 3 incomplete provider env or
# missing worker home / settings / skills.

set -euo pipefail

# Resolve the real script directory even when invoked via a ~/.local/bin symlink.
script_source="${BASH_SOURCE[0]}"
while [[ -L "$script_source" ]]; do
  script_dir="$(cd "$(dirname "$script_source")" && pwd)"
  script_source="$(readlink "$script_source")"
  [[ "$script_source" != /* ]] && script_source="$script_dir/$script_source"
done
SCRIPT_DIR="$(cd "$(dirname "$script_source")" && pwd)"

# Source shared worker-session helper (sentinel write/remove/test).
. "$SCRIPT_DIR/_worker_session.sh"

worker_home="${CLAUDE_WORKER_HOME:-$HOME/.claude-worker}"
claude_bin="${CLAUDE_BIN:-}"

usage() {
  sed -n '2,35p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# Mask a secret for logs: keep nothing but a length-bucketed placeholder so
# the token can't be reconstructed and isn't even partially leaked.
mask_secret() {
  local v="$1"
  if [[ -z "$v" ]]; then
    echo "(missing)"
  else
    echo "***set (${#v} chars)***"
  fi
}

# Informational stdout (the launch banner); --quiet silences it. Errors and
# exit codes are never silenced — the fail-closed preflight stays loud.
info() {
  if [[ $quiet -eq 1 ]]; then return 0; fi
  echo "$@"
}

# Resolve a Python 3 interpreter for JSON parsing (python3 > python > py -3).
resolve_python() {
  if command -v python3 >/dev/null 2>&1; then echo "python3"; return 0; fi
  if command -v python  >/dev/null 2>&1; then echo "python";  return 0; fi
  if command -v py       >/dev/null 2>&1; then echo "py";      return 0; fi
  return 1
}

resolve_claude_bin() {
  is_stable_claude_bin() {
    case "$1" in
      *"/.local/state/fnm_multishells/"*) return 1 ;;
      *) [[ -x "$1" ]] && "$1" --version >/dev/null 2>&1 ;;
    esac
  }

  if [[ -n "$claude_bin" ]]; then
    if is_stable_claude_bin "$claude_bin"; then
      echo "$claude_bin"
      return 0
    fi
    return 1
  fi

  if is_stable_claude_bin "$HOME/.local/bin/claude"; then
    echo "$HOME/.local/bin/claude"
    return 0
  fi

  local shell_claude=""
  if command -v zsh >/dev/null 2>&1; then
    shell_claude="$(zsh -lc 'command -v claude' 2>/dev/null || true)"
    if [[ -n "$shell_claude" ]] && is_stable_claude_bin "$shell_claude"; then
      echo "$shell_claude"
      return 0
    fi
  fi

  local npm_prefix=""
  if command -v npm >/dev/null 2>&1; then
    npm_prefix="$(npm config get prefix 2>/dev/null || true)"
    if [[ -n "$npm_prefix" ]] && is_stable_claude_bin "$npm_prefix/bin/claude"; then
      echo "$npm_prefix/bin/claude"
      return 0
    fi
  fi

  if command -v claude >/dev/null 2>&1; then
    local path_claude
    path_claude="$(command -v claude)"
    if is_stable_claude_bin "$path_claude"; then
      echo "$path_claude"
      return 0
    fi
  fi

  local candidates=(
    ${npm_prefix:+"$npm_prefix/bin/claude"}
    "$HOME/.local/share/fnm/node-versions/v20.20.2/installation/bin/claude"
    "$HOME/.local/bin/claude"
    "$HOME/.npm-global/bin/claude"
    "$HOME/Library/pnpm/claude"
    "$HOME/.volta/bin/claude"
    "/opt/homebrew/bin/claude"
    "/usr/local/bin/claude"
    "$HOME/.local/node/bin/claude"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done

  return 1
}

# Read env.<KEY> from the worker settings.json. Prefer Python (handles JSON
# escaping correctly); fall back to a grep/sed extractor for the flat,
# string-valued env block bootstrap.sh writes when no Python is available.
read_setting() {
  local key="$1" file="$2" py
  if py="$(resolve_python)"; then
    local args=("$file" "$key")
    if [[ "$py" == "py" ]]; then
      py -3 - "${args[@]}" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    with open(path) as fh:
        data = json.load(fh)
except Exception:
    sys.exit(0)
val = (data.get("env") or {}).get(key)
if isinstance(val, str):
    sys.stdout.write(val)
PY
    else
      "$py" - "${args[@]}" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    with open(path) as fh:
        data = json.load(fh)
except Exception:
    sys.exit(0)
val = (data.get("env") or {}).get(key)
if isinstance(val, str):
    sys.stdout.write(val)
PY
    fi
    return 0
  fi
  # Fallback: extract "KEY": "value" from the flat env block.
  sed -n "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"\(.*\)\".*/\1/p" "$file" | head -n1
}

# Name the role that OWNS this home, if the role registry declares it to run
# on the official Claude account ("auth": "official"). Prints the role name;
# prints nothing for every other home. Silence is the fail-closed answer: an
# unreadable registry, a missing Python, or a home no role claims all keep the
# strict provider-env preflight below, so an ACCIDENTAL fallback to the
# planner's OAuth identity is still refused. Only a home the registry names is
# allowed to carry no provider env.
official_role_for_home() {
  local home="$1" registry py
  registry="${ILK_ROLE_REGISTRY:-$SCRIPT_DIR/role-registry.json}"
  [[ -f "$registry" ]] || return 0
  py="$(resolve_python)" || return 0
  [[ "$py" == "py" ]] && py="py -3"
  $py - "$registry" "$home" <<'PYROLE'
import json, os, sys
registry, home = sys.argv[1], sys.argv[2]
try:
    with open(registry) as fh:
        roles = (json.load(fh) or {}).get("roles") or {}
except Exception:
    sys.exit(0)
want = os.path.realpath(os.path.expanduser(home))
for name, role in roles.items():
    if not isinstance(role, dict):
        continue
    if str(role.get("auth", "")).lower() != "official":
        continue
    if os.path.realpath(os.path.expanduser(str(role.get("home", "")))) == want:
        sys.stdout.write(name)
        break
PYROLE
}

# --- argument parsing -------------------------------------------------------
# Recognized wrapper flags are consumed; everything else is forwarded to
# claude verbatim (preserved in claude_args).
preflight_only=0
no_skip_permissions=0
dry_run=0
quiet=0
claude_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              usage; exit 0 ;;
    --preflight-only)       preflight_only=1 ;;
    --no-skip-permissions)  no_skip_permissions=1 ;;
    --dry-run)              dry_run=1 ;;
    --quiet)                quiet=1 ;;
    --home)
      shift
      [[ $# -eq 0 ]] && { echo "error: --home requires a directory argument" >&2; exit 2; }
      worker_home="$1"
      ;;
    --home=*)               worker_home="${1#--home=}" ;;
    --claude-bin)
      shift
      [[ $# -eq 0 ]] && { echo "error: --claude-bin requires a path argument" >&2; exit 2; }
      claude_bin="$1"
      ;;
    --claude-bin=*)         claude_bin="${1#--claude-bin=}" ;;
    *)                      claude_args+=("$1") ;;
  esac
  shift
done

# Normalize the worker home: expand a leading ~ and make relative paths
# absolute, matching bootstrap.sh.
case "$worker_home" in
  "~")   worker_home="$HOME" ;;
  "~/"*) worker_home="$HOME/${worker_home#\~/}" ;;
esac
case "$worker_home" in
  /*) ;;
  *)  worker_home="$(pwd)/$worker_home" ;;
esac

skill_home="$worker_home/skills"
settings_file="$worker_home/settings.json"

info "=== claude-worker ==="
info "worker home:     $worker_home"
info "ILK_SKILL_HOME:  $skill_home"

# --- fail-closed preflight --------------------------------------------------
# Each missing prerequisite is collected so the operator sees every problem at
# once rather than fixing them one launch at a time.
problems=()

if [[ ! -d "$worker_home" ]]; then
  problems+=("worker home does not exist: $worker_home (run tools/claude-worker/bootstrap.sh --apply)")
fi
if [[ ! -f "$settings_file" ]]; then
  problems+=("worker settings.json missing: $settings_file")
fi

base_url=""; auth_token=""; model=""
if [[ -f "$settings_file" ]]; then
  base_url="$(read_setting ANTHROPIC_BASE_URL "$settings_file")"
  auth_token="$(read_setting ANTHROPIC_AUTH_TOKEN "$settings_file")"
  model="$(read_setting ANTHROPIC_MODEL "$settings_file")"
fi

info "base url:        ${base_url:-(missing)}"
info "auth token:      $(mask_secret "$auth_token")"
info "model:           ${model:-(missing)}"

# A home the registry declares official runs ON the official OAuth identity
# deliberately, so the three provider-env checks below would refuse exactly
# the configuration it is supposed to have. The checks invert instead: a
# leftover base url or token would mean the home is still on a third-party
# provider while the registry claims otherwise — the same silent-identity bug
# pointing the other way.
official_role="$(official_role_for_home "$worker_home")"
if [[ -n "$official_role" ]]; then
  info "identity:        Claude Official (OAuth) - role '$official_role'"
  [[ -n "$base_url" ]] && problems+=("ANTHROPIC_BASE_URL is set in $settings_file, but role '$official_role' is declared auth=official - remove it, or drop the declaration")
  [[ -n "$auth_token" ]] && problems+=("ANTHROPIC_AUTH_TOKEN is set in $settings_file, but role '$official_role' is declared auth=official - remove it, or drop the declaration")
else
  [[ -z "$base_url" ]]   && problems+=("ANTHROPIC_BASE_URL missing from $settings_file")
  [[ -z "$auth_token" ]] && problems+=("ANTHROPIC_AUTH_TOKEN missing from $settings_file")
  [[ -z "$model" ]]      && problems+=("ANTHROPIC_MODEL missing from $settings_file")
fi

if [[ ! -d "$skill_home/ilk-runner" ]]; then
  problems+=("ilk-runner skill not found at $skill_home/ilk-runner (run install.sh --claude-home \"$worker_home\" --only-claude)")
fi

if [[ ! -r "$worker_home/commands/ilk.md" ]]; then
  problems+=("commands/ilk.md not readable at $worker_home/commands/ilk.md (run install.sh --claude-home \"$worker_home\" --only-claude)")
fi

info

if [[ ${#problems[@]} -gt 0 ]]; then
  echo "ERROR: worker preflight failed — refusing to launch a worker that would" >&2
  echo "silently fall back to the planner's official OAuth identity." >&2
  echo "Problems:" >&2
  for p in "${problems[@]}"; do echo "  - $p" >&2; done
  exit 3
fi

info "Preflight OK: worker home, provider env, and ilk-runner all present."

if [[ $preflight_only -eq 1 ]]; then
  echo "(--preflight-only: not launching claude)"
  exit 0
fi

# --- default --dangerously-skip-permissions ---
# Unless the user opted out with --no-skip-permissions, inject the flag so the
# worker launches without permission prompts.  Idempotent: skip if the user
# already passed it explicitly.
if [[ $no_skip_permissions -eq 0 ]]; then
  has_dangerous=0
  for a in "${claude_args[@]+"${claude_args[@]}"}"; do
    if [[ "$a" == "--dangerously-skip-permissions" ]]; then
      has_dangerous=1
      break
    fi
  done
  if [[ $has_dangerous -eq 0 ]]; then
    claude_args=("--dangerously-skip-permissions" "${claude_args[@]+"${claude_args[@]}"}")
  fi
fi

# --- launch -----------------------------------------------------------------
# Select the worker home for Claude Code and the ilk skill root, then hand off
# to claude. The provider token lives in the worker settings.json, which
# CLAUDE_CONFIG_DIR points Claude Code at — we never put it on the command line
# or in the environment here.
export CLAUDE_CONFIG_DIR="$worker_home"
export ILK_SKILL_HOME="$skill_home"

if ! resolved_claude_bin="$(resolve_claude_bin)"; then
  echo "ERROR: Claude Code executable not found; cannot launch the worker." >&2
  echo "Set CLAUDE_BIN=/path/to/claude or pass --claude-bin /path/to/claude." >&2
  exit 3
fi

info "claude bin:      $resolved_claude_bin"

if [[ $dry_run -eq 1 ]]; then
  echo "claude args:     ${claude_args[*]:-}"
  echo "(--dry-run: not launching claude)"
  exit 0
fi

info "Launching claude with CLAUDE_CONFIG_DIR=$worker_home ..."

# Write a sentinel so bootstrap can detect an active worker run before
# overwriting the provider settings.  Uses PID + start-time identity so
# a reused PID is never mistaken for an active worker.
pid_file="$worker_home/running.pid"
worker_sentinel_write "$pid_file"

# Clean up the sentinel on exit (normal, error, or signal).
trap 'worker_sentinel_remove "$pid_file"' EXIT

# Guard the array expansion so an empty claude_args doesn't trip `set -u` on
# bash 3.2 (the default on macOS).
# Launch as a child (not exec) so the EXIT trap fires on exit.
"$resolved_claude_bin" ${claude_args[@]+"${claude_args[@]}"}
exit $?
