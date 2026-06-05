#!/usr/bin/env bash
# Launch Claude Code under a Worker home (macOS / Linux).
#
# Wraps `claude` so it runs against a separate Worker Claude home (default
# ~/.claude-worker) pinned to an explicit Anthropic-compatible provider, while
# the Planner Claude keeps the default ~/.claude home on its official provider.
# See docs/dual-claude-homes-design.md. Create the worker home first with
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
#   -h | --help         show this help and exit
#
# Exit codes: 0 ok / preflight ok, 2 usage error, 3 incomplete provider env or
# missing worker home / settings / skills.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

worker_home="${CLAUDE_WORKER_HOME:-$HOME/.claude-worker}"
claude_bin="${CLAUDE_BIN:-}"

usage() {
  sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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

# --- argument parsing -------------------------------------------------------
# Recognized wrapper flags are consumed; everything else is forwarded to
# claude verbatim (preserved in claude_args).
preflight_only=0
no_skip_permissions=0
dry_run=0
claude_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              usage; exit 0 ;;
    --preflight-only)       preflight_only=1 ;;
    --no-skip-permissions)  no_skip_permissions=1 ;;
    --dry-run)              dry_run=1 ;;
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

echo "=== claude-worker ==="
echo "worker home:     $worker_home"
echo "ILK_SKILL_HOME:  $skill_home"

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

echo "base url:        ${base_url:-(missing)}"
echo "auth token:      $(mask_secret "$auth_token")"
echo "model:           ${model:-(missing)}"

[[ -z "$base_url" ]]   && problems+=("ANTHROPIC_BASE_URL missing from $settings_file")
[[ -z "$auth_token" ]] && problems+=("ANTHROPIC_AUTH_TOKEN missing from $settings_file")
[[ -z "$model" ]]      && problems+=("ANTHROPIC_MODEL missing from $settings_file")

if [[ ! -d "$skill_home/ilk-runner" ]]; then
  problems+=("ilk-runner skill not found at $skill_home/ilk-runner (run install.sh --claude-home \"$worker_home\" --only-claude)")
fi

echo

if [[ ${#problems[@]} -gt 0 ]]; then
  echo "ERROR: worker preflight failed — refusing to launch a worker that would" >&2
  echo "silently fall back to the planner's official OAuth identity." >&2
  echo "Problems:" >&2
  for p in "${problems[@]}"; do echo "  - $p" >&2; done
  exit 3
fi

echo "Preflight OK: worker home, provider env, and ilk-runner all present."

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

echo "claude bin:      $resolved_claude_bin"

if [[ $dry_run -eq 1 ]]; then
  echo "claude args:     ${claude_args[*]:-}"
  echo "(--dry-run: not launching claude)"
  exit 0
fi

echo "Launching claude with CLAUDE_CONFIG_DIR=$worker_home ..."

# Write a PID file so bootstrap can detect an active worker run before
# overwriting the provider settings.  The PID stays valid after exec
# (same process id, different binary).  Bootstrap checks if the PID is
# still alive; a stale file with a dead PID is harmless.
pid_file="$worker_home/running.pid"
if ! echo $$ > "$pid_file"; then
  echo "WARN: could not write worker PID file: $pid_file" >&2
  echo "      provider-switch guardrails may not detect this running session." >&2
fi

# Guard the array expansion so an empty claude_args doesn't trip `set -u` on
# bash 3.2 (the default on macOS).
exec "$resolved_claude_bin" ${claude_args[@]+"${claude_args[@]}"}
