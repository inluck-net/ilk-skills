#!/usr/bin/env bash
# claude-manager — launch Claude Code under the manager role's home, on the
# official Claude account, from one host-provided OAuth token file.
# Generated from tools/claude-worker/role-registry.json (role "manager",
# home ~/.claude-manager, tier "manager", auth "official"); installed on
# PATH by install.sh --only-path. Replaces the hand-written stopgap that
# lived at ~/.local/bin/claude-manager until 2026-09-18.
# Design: docs/architecture/role-tier-registry-design.md.
#
# Credential contract (one source, one read):
#   * The caller sets ILK_MANAGER_OAUTH_TOKEN_FILE to the token file's path.
#     There is NO default — a launch without it is refused, never quietly
#     downgraded to whatever identity happens to be lying around.
#   * Before Claude is launched the file must exist, be a regular readable
#     non-empty file, owned by the calling user, with no permission bits
#     beyond 0600.
#   * The file is read exactly once. The value goes to the child claude-worker
#     process as CLAUDE_CODE_OAUTH_TOKEN and nowhere else: not argv, not
#     stdout, not stderr, not diagnostics, not settings.json, not the registry.
#   * Every ambient ANTHROPIC_* variable is cleared first, so a caller that
#     once exported a GLM endpoint cannot carry it into an official launch.
#     The manager home's own top-level "model" in settings.json is what pins
#     Opus — clearing shell variables does not touch it.
#
# Passes --quiet so a routine manager launch prints nothing on success; the
# fail-closed checks still report a fixed-class reason (and the path when
# useful) to stderr. For the full banner run: claude-worker --home ~/.claude-manager
#
# Exit codes: 0 ok, 3 refused (credential file or launcher missing).

set -euo pipefail

fail() {
  # Fixed-class reason only, plus the path when useful. Never the token.
  echo "error: $1" >&2
  exit 3
}

# --- validate the token file, then read it exactly once ---------------------
token_path="${ILK_MANAGER_OAUTH_TOKEN_FILE:-}"
if [[ -z "$token_path" ]]; then
  fail "ILK_MANAGER_OAUTH_TOKEN_FILE is not set"
fi

if [[ ! -e "$token_path" && ! -L "$token_path" ]]; then
  fail "token file not found: $token_path"
fi
if [[ ! -f "$token_path" ]]; then
  fail "token file is not a regular file: $token_path"
fi

# Owner + mode in one call. BSD stat (macOS) uses -f, GNU stat uses -c.
stat_owner_mode() {
  local p="$1"
  if stat -c '%u %a' "$p" 2>/dev/null; then return 0; fi
  stat -f '%u %Lp' "$p"
}

if ! owner_mode="$(stat_owner_mode "$token_path")"; then
  fail "token file is not readable: $token_path"
fi
file_uid="${owner_mode%% *}"
file_mode="${owner_mode##* }"

if [[ "$file_uid" != "$(id -u)" ]]; then
  fail "token file owner does not match the caller: $token_path"
fi

# Any bit outside 0600 is broader than 0600 (group/other bits, or execute).
# 0400 and 0600 pass; 0644, 0444, 0700 are refused.
if (( (8#$file_mode & ~0600) != 0 )); then
  fail "token file permissions broader than 0600: $token_path"
fi

if [[ ! -r "$token_path" ]]; then
  fail "token file is not readable: $token_path"
fi

# Single read: the same value is emptiness-checked and handed to the child.
oauth_token="$(<"$token_path")"
if [[ -z "$oauth_token" ]]; then
  fail "token file is empty: $token_path"
fi

# --- clear every ambient ANTHROPIC_* provider variable ----------------------
# The child inherits this process's environment; a leftover base url or token
# would silently keep the launch on a third-party endpoint.
for _name in $(env | sed -n 's/^\(ANTHROPIC_[A-Za-z0-9_]*\)=.*/\1/p'); do
  unset "$_name"
done

worker_bin="$HOME/.local/bin/claude-worker"
if [[ ! -x "$worker_bin" ]]; then
  fail "claude-worker not found: $worker_bin"
fi

# The assignment applies to the child's environment only — it is not argv and
# it is not exported here, so nothing else in this launch can observe it.
CLAUDE_CODE_OAUTH_TOKEN="$oauth_token" exec "$worker_bin" --home "$HOME/.claude-manager" --quiet "$@"
