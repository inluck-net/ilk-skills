#!/usr/bin/env bash
# Tests for claude-manager.sh — the manager role launches the official Claude
# account from ONE host-provided OAuth token file (ILK_MANAGER_OAUTH_TOKEN_FILE).
#
# Proves, against a fake HOME and synthetic sentinel tokens:
#   * an absent path, missing / nonregular / empty file, a file owned by
#     someone else, and permissions broader than 0600 are all refused BEFORE
#     the child claude-worker is ever invoked;
#   * the success path hands CLAUDE_CODE_OAUTH_TOKEN to the child only, with
#     every ambient ANTHROPIC_* variable cleared (a poisoned GLM base url /
#     token cannot ride along);
#   * the sentinel never appears in argv, stdout, stderr, or failure
#     diagnostics, and is never written into settings.json;
#   * --home "$HOME/.claude-manager" --quiet still forwards verbatim;
#   * through the REAL claude-worker preflight and a stub claude executable,
#     clearing shell variables does not erase the manager home's top-level
#     "model": "opus", and the token survives all the way to claude.
#
# Hermetic: HOME is a throwaway fake directory, every token is synthetic, and
# nothing outside the temp tree is written. Exit 0 on success, 1 on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
MANAGER_SCRIPT="$SCRIPT_DIR/../claude-manager.sh"
WORKER_SCRIPT="$SCRIPT_DIR/../claude-worker.sh"

if [[ ! -f "$MANAGER_SCRIPT" ]]; then
  echo "FAIL: claude-manager.sh not found at $MANAGER_SCRIPT" >&2
  exit 1
fi

pass=0
fail=0

ok() { # ok "<label>" <0-pass|1-fail>
  if [[ "$2" -eq 0 ]]; then
    echo "  PASS: $1"
    pass=$((pass + 1))
  else
    echo "  FAIL: $1"
    fail=$((fail + 1))
  fi
}

check_rc() { # check_rc "<label>" <expected>
  if [[ "$RUN_RC" -eq "$2" ]]; then
    ok "$1 (exit $RUN_RC)" 0
  else
    ok "$1 (exit $RUN_RC, want $2)" 1
    echo "  output: $RUN_OUT"
  fi
}

check_contains() { # check_contains "<label>" "<needle>"  — in captured output
  case "$RUN_OUT" in
    *"$2"*) ok "$1" 0 ;;
    *)      ok "$1 (missing '$2')" 1; echo "  output: $RUN_OUT" ;;
  esac
}

check_absent_text() { # check_absent_text "<label>" "<needle>"  — in captured output
  case "$RUN_OUT" in
    *"$2"*) ok "$1 (found '$2')" 1; echo "  output: $RUN_OUT" ;;
    *)      ok "$1" 0 ;;
  esac
}

check_file_absent() { # check_file_absent "<label>" "<path>"
  if [[ ! -e "$2" ]]; then ok "$1" 0; else ok "$1 ($2 exists)" 1; fi
}

check_file_has() { # check_file_has "<label>" "<path>" "<needle>"
  # No file contents in diagnostics: these side channels record live
  # environments and may hold real credentials.
  if [[ -f "$2" ]] && grep -qF -- "$3" "$2"; then
    ok "$1" 0
  else
    ok "$1 (missing '$3' in $(basename "$2"))" 1
  fi
}

check_file_lacks() { # check_file_lacks "<label>" "<path>" "<needle>"
  if [[ -f "$2" ]] && grep -qF -- "$3" "$2"; then
    ok "$1 (found '$3' in $(basename "$2"))" 1
  else
    ok "$1" 0
  fi
}

# --- Setup: throwaway HOME + side channel for the stubs ---------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

OUT="$TMP/out"
mkdir -p "$OUT"

FAKE_HOME="$TMP/home"
STUB_BIN="$FAKE_HOME/.local/bin"
mkdir -p "$STUB_BIN"

SENTINEL="SENTINEL-oauth-sentinel-4c1f9a2e"
TOKEN_FILE="$TMP/oauth.token"
printf '%s\n' "$SENTINEL" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

# Stub claude-worker: records that it ran, its argv, and its environment.
# Nothing is printed — the token must be observable only through $OUT.
cat > "$STUB_BIN/claude-worker" <<EOF
#!/usr/bin/env bash
printf 'invoked\n' > "$OUT/child-ran"
printf '%s\n' "\$@" > "$OUT/child-argv.txt"
env | sort > "$OUT/child-env.txt"
exit 0
EOF
chmod +x "$STUB_BIN/claude-worker"

reset_trace() {
  rm -f "$OUT/child-ran" "$OUT/child-argv.txt" "$OUT/child-env.txt"
}

echo "=== Test 1: an unset ILK_MANAGER_OAUTH_TOKEN_FILE is refused ==="
reset_trace
set +e
RUN_OUT="$(env -u ILK_MANAGER_OAUTH_TOKEN_FILE HOME="$FAKE_HOME" \
  ANTHROPIC_BASE_URL="https://glm.example/anthropic" \
  ANTHROPIC_AUTH_TOKEN="tok-ambient-glm" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "unset token file path -> exit 3" 3
check_contains "unset token file path -> names the variable" "ILK_MANAGER_OAUTH_TOKEN_FILE"
check_file_absent "unset token file path -> child never invoked" "$OUT/child-ran"
check_absent_text "unset token file path -> no sentinel in output" "$SENTINEL"

echo ""
echo "=== Test 2: an empty ILK_MANAGER_OAUTH_TOKEN_FILE is refused ==="
reset_trace
set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="" HOME="$FAKE_HOME" \
  ANTHROPIC_BASE_URL="https://glm.example/anthropic" \
  ANTHROPIC_AUTH_TOKEN="tok-ambient-glm" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "empty token file path -> exit 3" 3
check_contains "empty token file path -> names the variable" "ILK_MANAGER_OAUTH_TOKEN_FILE"
check_file_absent "empty token file path -> child never invoked" "$OUT/child-ran"
check_absent_text "empty token file path -> no sentinel in output" "$SENTINEL"

echo ""
echo "=== Test 3: a missing token file is refused ==="
reset_trace
set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$TMP/absent.token" HOME="$FAKE_HOME" \
  ANTHROPIC_BASE_URL="https://glm.example/anthropic" \
  ANTHROPIC_AUTH_TOKEN="tok-ambient-glm" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "missing token file -> exit 3" 3
check_contains "missing token file -> names the path" "$TMP/absent.token"
check_file_absent "missing token file -> child never invoked" "$OUT/child-ran"
check_absent_text "missing token file -> no sentinel in output" "$SENTINEL"

echo ""
echo "=== Test 4: a non-regular token file is refused ==="
mkdir -p "$TMP/oauth.dir"
reset_trace
set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$TMP/oauth.dir" HOME="$FAKE_HOME" \
  ANTHROPIC_BASE_URL="https://glm.example/anthropic" \
  ANTHROPIC_AUTH_TOKEN="tok-ambient-glm" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "directory in place of the token file -> exit 3" 3
check_contains "directory -> names the non-regular reason" "not a regular file"
check_file_absent "directory -> child never invoked" "$OUT/child-ran"
check_absent_text "directory -> no sentinel in output" "$SENTINEL"

echo ""
echo "=== Test 5: an empty token file is refused ==="
: > "$TMP/empty.token"
chmod 600 "$TMP/empty.token"
reset_trace
set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$TMP/empty.token" HOME="$FAKE_HOME" \
  ANTHROPIC_BASE_URL="https://glm.example/anthropic" \
  ANTHROPIC_AUTH_TOKEN="tok-ambient-glm" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "zero-byte token file -> exit 3" 3
check_contains "zero-byte token file -> names the empty reason" "empty"
check_file_absent "zero-byte token file -> child never invoked" "$OUT/child-ran"
check_absent_text "zero-byte token file -> no sentinel in output" "$SENTINEL"

printf '\n' > "$TMP/newline.token"
chmod 600 "$TMP/newline.token"
reset_trace
set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$TMP/newline.token" HOME="$FAKE_HOME" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "newline-only token file -> exit 3" 3
check_contains "newline-only token file -> names the empty reason" "empty"
check_file_absent "newline-only token file -> child never invoked" "$OUT/child-ran"

echo ""
echo "=== Test 6: a token file owned by another user is refused ==="
# No root here, so the foreign-owned file has to already exist on the system.
# The premise is checked out loud: if every candidate is ours, the test fails
# rather than quietly skipping the owner gate.
foreign=""
for cand in /etc/hosts /etc/group /etc/passwd /etc/services; do
  [[ -f "$cand" ]] || continue
  cand_uid="$(stat -f '%u' "$cand" 2>/dev/null || stat -c '%u' "$cand" 2>/dev/null || echo "")"
  if [[ -n "$cand_uid" && "$cand_uid" != "$(id -u)" ]]; then
    foreign="$cand"
    break
  fi
done
if [[ -z "$foreign" ]]; then
  ok "premise: a file owned by another user exists" 1
else
  ok "premise: a file owned by another user exists ($foreign)" 0
  reset_trace
  set +e
  RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$foreign" HOME="$FAKE_HOME" \
    ANTHROPIC_BASE_URL="https://glm.example/anthropic" \
    ANTHROPIC_AUTH_TOKEN="tok-ambient-glm" \
    bash "$MANAGER_SCRIPT" 2>&1)"
  RUN_RC=$?
  set -e
  check_rc "foreign-owned token file -> exit 3" 3
  check_contains "foreign-owned token file -> names the owner reason" "owner"
  check_file_absent "foreign-owned token file -> child never invoked" "$OUT/child-ran"
  check_absent_text "foreign-owned token file -> no sentinel in output" "$SENTINEL"
fi

echo ""
echo "=== Test 7: permissions broader than 0600 are refused ==="
cp "$TOKEN_FILE" "$TMP/broad.token"
chmod 644 "$TMP/broad.token"
reset_trace
set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$TMP/broad.token" HOME="$FAKE_HOME" \
  ANTHROPIC_BASE_URL="https://glm.example/anthropic" \
  ANTHROPIC_AUTH_TOKEN="tok-ambient-glm" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "mode 0644 token file -> exit 3" 3
check_contains "mode 0644 token file -> names the permissions reason" "0600"
check_file_absent "mode 0644 token file -> child never invoked" "$OUT/child-ran"
check_absent_text "mode 0644 token file -> sentinel not leaked" "$SENTINEL"

cp "$TOKEN_FILE" "$TMP/broad2.token"
chmod 444 "$TMP/broad2.token"
reset_trace
set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$TMP/broad2.token" HOME="$FAKE_HOME" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "mode 0444 token file -> exit 3" 3
check_contains "mode 0444 token file -> names the permissions reason" "0600"
check_file_absent "mode 0444 token file -> child never invoked" "$OUT/child-ran"
check_absent_text "mode 0444 token file -> sentinel not leaked" "$SENTINEL"

echo ""
echo "=== Test 8: success hands the token to the child only ==="
reset_trace
set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$TOKEN_FILE" HOME="$FAKE_HOME" \
  ANTHROPIC_BASE_URL="https://glm.example/anthropic" \
  ANTHROPIC_AUTH_TOKEN="tok-ambient-glm" \
  ANTHROPIC_API_KEY="key-ambient" \
  ANTHROPIC_MODEL="glm-5.3" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "0600 token file -> exit 0" 0
check_file_has "success -> child invoked" "$OUT/child-ran" "invoked"
check_file_has "success -> child receives CLAUDE_CODE_OAUTH_TOKEN" \
  "$OUT/child-env.txt" "CLAUDE_CODE_OAUTH_TOKEN=$SENTINEL"
check_file_lacks "success -> no ANTHROPIC_BASE_URL reaches the child" \
  "$OUT/child-env.txt" "ANTHROPIC_BASE_URL="
check_file_lacks "success -> no ANTHROPIC_AUTH_TOKEN reaches the child" \
  "$OUT/child-env.txt" "ANTHROPIC_AUTH_TOKEN="
check_file_lacks "success -> no ANTHROPIC_MODEL reaches the child" \
  "$OUT/child-env.txt" "ANTHROPIC_MODEL="
check_file_lacks "success -> no ANTHROPIC_API_KEY reaches the child" \
  "$OUT/child-env.txt" "ANTHROPIC_API_KEY="
if [[ -f "$OUT/child-env.txt" ]] && grep -q '^ANTHROPIC_' "$OUT/child-env.txt"; then
  ok "success -> no ANTHROPIC_* of any name reaches the child" 1
  grep -c '^ANTHROPIC_' "$OUT/child-env.txt" | sed 's/^/  ANTHROPIC_* vars in child env: /'
else
  ok "success -> no ANTHROPIC_* of any name reaches the child" 0
fi
check_file_has "success -> --home forwarding contract" \
  "$OUT/child-argv.txt" "$FAKE_HOME/.claude-manager"
check_file_has "success -> --quiet forwarding contract" \
  "$OUT/child-argv.txt" "--quiet"
check_file_lacks "success -> token is not in the child's argv" \
  "$OUT/child-argv.txt" "$SENTINEL"
check_absent_text "success -> token is not in stdout/stderr" "$SENTINEL"

echo ""
echo "=== Test 9: 0400 is narrower than 0600, so it launches ==="
cp "$TOKEN_FILE" "$TMP/narrow.token"
chmod 400 "$TMP/narrow.token"
reset_trace
set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$TMP/narrow.token" HOME="$FAKE_HOME" \
  bash "$MANAGER_SCRIPT" 2>&1)"
RUN_RC=$?
set -e
check_rc "mode 0400 token file -> exit 0" 0
check_file_has "mode 0400 -> child receives the token" \
  "$OUT/child-env.txt" "CLAUDE_CODE_OAUTH_TOKEN=$SENTINEL"
check_absent_text "mode 0400 -> token is not in stdout/stderr" "$SENTINEL"

echo ""
echo "=== Test 10: the forwarding contract is spelled exactly as before ==="
if grep -qF -- '--home "$HOME/.claude-manager" --quiet' "$MANAGER_SCRIPT"; then
  ok "claude-manager.sh forwards --home \"\$HOME/.claude-manager\" --quiet" 0
else
  ok "claude-manager.sh forwards --home \"\$HOME/.claude-manager\" --quiet" 1
  grep -n 'home\|quiet' "$MANAGER_SCRIPT" || true
fi

echo ""
echo "=== Test 11: real claude-worker preflight + stub claude (Opus + token) ==="
# A second fake HOME whose claude-worker is the REAL wrapper, so the manager's
# child is the production preflight path. The manager home is the official
# shape: top-level "model": "opus", empty provider env.
HOME2="$TMP/home2"
MGR_HOME="$HOME2/.claude-manager"
OUT2="$TMP/out2"
mkdir -p "$HOME2/.local/bin" "$OUT2" "$MGR_HOME/skills/ilk-runner" "$MGR_HOME/commands"
cat > "$MGR_HOME/settings.json" <<'EOF'
{
  "env": {},
  "model": "opus"
}
EOF
echo "skill-content" > "$MGR_HOME/skills/ilk-runner/SKILL.md"
echo "# /ilk" > "$MGR_HOME/commands/ilk.md"

cat > "$HOME2/.local/bin/claude-worker" <<EOF
#!/usr/bin/env bash
exec bash "$WORKER_SCRIPT" "\$@"
EOF
chmod +x "$HOME2/.local/bin/claude-worker"

cat > "$OUT2/claude" <<EOF
#!/usr/bin/env bash
# Stub claude: report what reached Claude. Prints nothing secret.
if [[ "\${1:-}" == "--version" ]]; then
  echo "stub-claude 1.0"
  exit 0
fi
printf 'invoked\n' > "$OUT2/claude-ran"
env | sort > "$OUT2/claude-env.txt"
if [[ -n "\${CLAUDE_CONFIG_DIR:-}" && -f "\$CLAUDE_CONFIG_DIR/settings.json" ]]; then
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("model"))' \
    "\$CLAUDE_CONFIG_DIR/settings.json" > "$OUT2/claude-model.txt"
fi
exit 0
EOF
chmod +x "$OUT2/claude"

set +e
RUN_OUT="$(env ILK_MANAGER_OAUTH_TOKEN_FILE="$TOKEN_FILE" HOME="$HOME2" \
  ANTHROPIC_BASE_URL="https://glm.example/anthropic" \
  ANTHROPIC_AUTH_TOKEN="tok-ambient-glm" \
  ANTHROPIC_MODEL="glm-5.3" \
  bash "$MANAGER_SCRIPT" --claude-bin "$OUT2/claude" 2>&1)"
RUN_RC=$?
set -e
check_rc "official manager home through the real preflight -> exit 0" 0
check_absent_text "real preflight -> no sentinel in stdout/stderr" "$SENTINEL"
check_file_has "real preflight -> claude was launched" "$OUT2/claude-ran" "invoked"
check_file_has "token survives all the way to claude" \
  "$OUT2/claude-env.txt" "CLAUDE_CODE_OAUTH_TOKEN=$SENTINEL"
if [[ -f "$OUT2/claude-env.txt" ]] && grep -q '^ANTHROPIC_' "$OUT2/claude-env.txt"; then
  ok "clearing shell vars -> claude sees no ANTHROPIC_*" 1
  grep -c '^ANTHROPIC_' "$OUT2/claude-env.txt" | sed 's/^/  ANTHROPIC_* vars in claude env: /'
else
  ok "clearing shell vars -> claude sees no ANTHROPIC_*" 0
fi
check_file_has "clearing shell vars does not erase the home's Opus setting" \
  "$OUT2/claude-model.txt" "opus"
check_file_lacks "the token is not stored in settings.json" \
  "$MGR_HOME/settings.json" "$SENTINEL"
check_file_has "the home's model setting is still in settings.json" \
  "$MGR_HOME/settings.json" "opus"

# === Results ===
echo ""
echo "=== Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
  exit 1
fi
exit 0
