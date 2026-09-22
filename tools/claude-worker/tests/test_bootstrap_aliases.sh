#!/usr/bin/env bash
# Tests for provider-alias preservation on bootstrap.sh's flags-only path.
#
# Defect D4: --from-ccswitch carries the provider's alias keys, but the
# flags-only path synthesizes exactly three keys and the merge replaces
# provider keys wholesale, so no alias can be supplied and none survives --
# the failure the CCSwitch comment describes is still reachable by the other
# entrance.
#
# Covers:
#   AC-1: flags-only with alias values supplied writes them into the home's env
#   AC-2: flags-only with none supplied writes exactly the three keys, as today
#   AC-3: stale ANTHROPIC_*/CLAUDE_CODE_* keys from a previous provider do not
#         survive
#   AC-4: missing base url / token / model still refuses and writes nothing
#   AC-5: non-provider keys (hooks, theme, permission flags) are preserved
#   AC-6: --from-ccswitch output is byte-identical to the pinned golden
# Exit 0 on success, 1 on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
BOOTSTRAP="$SCRIPT_DIR/../bootstrap.sh"

if [[ ! -f "$BOOTSTRAP" ]]; then
  echo "FAIL: bootstrap.sh not found at $BOOTSTRAP" >&2
  exit 1
fi

pass=0
fail=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected '$expected', got '$actual'"
    fail=$((fail + 1))
  fi
}

assert_exit_code() {
  local label="$1" expected="$2"
  shift 2
  local output rc
  set +e
  output="$("$@" 2>&1)"
  rc=$?
  set -e
  if [[ $rc -eq "$expected" ]]; then
    echo "  PASS: $label (exit $rc)"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — expected exit $expected, got $rc"
    echo "  output: $output"
    fail=$((fail + 1))
  fi
}

assert_output_contains() {
  local label="$1" needle="$2"
  shift 2
  local output rc
  set +e
  output="$("$@" 2>&1)"
  rc=$?
  set -e
  if [[ "$output" == *"$needle"* ]]; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — output does not contain '$needle'"
    echo "  output: $output"
    fail=$((fail + 1))
  fi
}

assert_file_unchanged() {
  local label="$1" before="$2" after="$3"
  if cmp -s "$before" "$after"; then
    echo "  PASS: $label"
    pass=$((pass + 1))
  else
    echo "  FAIL: $label — file was modified"
    fail=$((fail + 1))
  fi
}

# env_get <settings.json> <key> -> value, or a diagnostic placeholder when the
# file or key is absent (so a failed write produces a readable assert mismatch
# instead of tripping set -e inside python).
env_get() {
  local settings="$1" key="$2"
  if [[ ! -f "$settings" ]]; then
    echo "(no settings.json)"
    return 0
  fi
  python3 -c "
import json, sys
try:
    env = json.load(open(sys.argv[1])).get('env') or {}
except (ValueError, OSError):
    print('(unparseable settings.json)')
    raise SystemExit(0)
print(env.get(sys.argv[2], '(absent)'))
" "$settings" "$key"
}

# env_keys <settings.json> -> sorted env key list, one per line.
env_keys() {
  local settings="$1"
  if [[ ! -f "$settings" ]]; then
    echo "(no settings.json)"
    return 0
  fi
  python3 -c "
import json, sys
env = json.load(open(sys.argv[1])).get('env') or {}
print('\n'.join(sorted(env)))
" "$settings"
}

# --- Setup: fake home under repo-local scratch ---
SCRATCH="$REPO_ROOT/scratch/bootstrap-aliases-test"
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH"

cleanup() {
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

fresh_home() {
  # fresh_home <name> -> prints the home path; starts from no settings.json.
  local home="$SCRATCH/$1"
  rm -rf "$home"
  mkdir -p "$home"
  printf '%s' "$home"
}

# run_bootstrap <env-assignments...> <command...>
#
# bootstrap.sh accepts each provider value "via flag OR environment variable"
# (its header), so an ambient ANTHROPIC_BASE_URL / _AUTH_TOKEN / _MODEL silently
# satisfies a call that is supposed to be missing one. These tests run inside a
# worker home that exports exactly those three. Clear them so the flags under
# test are the only input; without this, the AC-4 refusal case writes the home.
run_bootstrap() {
  env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_MODEL \
      -u CLAUDE_WORKER_HOME \
      "$@"
}

# === Test 1 (AC-1): flags-only with alias values writes them into the home ===
echo "=== Test 1 (AC-1): flags-only carries --env provider aliases ==="

FAKE_HOME="$(fresh_home ac1)"

assert_exit_code "bootstrap --apply with --env aliases succeeds" 0 \
  run_bootstrap bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://glm.example.com/anthropic" \
    --auth-token "test-token-12345" \
    --model "glm-5.3" \
    --env "ANTHROPIC_DEFAULT_OPUS=glm-5.3" \
    --env "ANTHROPIC_DEFAULT_SONNET=glm-5.3" \
    --env "ANTHROPIC_DEFAULT_HAIKU=glm-5.3-fast" \
    --env "CLAUDE_CODE_MAX_CONTEXT_TOKENS=200000"

SETTINGS="$FAKE_HOME/settings.json"

# The three required values still land alongside the aliases.
assert_eq "ANTHROPIC_BASE_URL lands" \
  "https://glm.example.com/anthropic" "$(env_get "$SETTINGS" ANTHROPIC_BASE_URL)"
assert_eq "ANTHROPIC_AUTH_TOKEN lands" \
  "test-token-12345" "$(env_get "$SETTINGS" ANTHROPIC_AUTH_TOKEN)"
assert_eq "ANTHROPIC_MODEL lands" \
  "glm-5.3" "$(env_get "$SETTINGS" ANTHROPIC_MODEL)"

# The aliases and harness settings -- the actual subject of AC-1.
assert_eq "ANTHROPIC_DEFAULT_OPUS lands" \
  "glm-5.3" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_OPUS)"
assert_eq "ANTHROPIC_DEFAULT_SONNET lands" \
  "glm-5.3" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_SONNET)"
assert_eq "ANTHROPIC_DEFAULT_HAIKU lands" \
  "glm-5.3-fast" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_HAIKU)"
assert_eq "CLAUDE_CODE_MAX_CONTEXT_TOKENS lands" \
  "200000" "$(env_get "$SETTINGS" CLAUDE_CODE_MAX_CONTEXT_TOKENS)"

# === Test 2 (AC-2): flags-only with none supplied writes exactly three keys ===
echo ""
echo "=== Test 2 (AC-2): flags-only without --env writes exactly three keys ==="

FAKE_HOME="$(fresh_home ac2)"

assert_exit_code "bootstrap --apply without --env succeeds" 0 \
  run_bootstrap bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://glm.example.com/anthropic" \
    --auth-token "test-token-12345" \
    --model "glm-5.3"

SETTINGS="$FAKE_HOME/settings.json"

assert_eq "exactly the three keys, sorted" \
  "ANTHROPIC_AUTH_TOKEN
ANTHROPIC_BASE_URL
ANTHROPIC_MODEL" "$(env_keys "$SETTINGS")"

# === Test 3 (AC-3): stale keys from a previous provider do not survive ===
echo ""
echo "=== Test 3 (AC-3): stale provider keys are replaced wholesale ==="

FAKE_HOME="$(fresh_home ac3)"
SETTINGS="$FAKE_HOME/settings.json"

# First provider: "mimo", carrying its own aliases and harness setting.
assert_exit_code "bootstrap mimo home succeeds" 0 \
  run_bootstrap bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://mimo.example.com/anthropic" \
    --auth-token "mimo-token-12345" \
    --model "mimo-1" \
    --env "ANTHROPIC_DEFAULT_OPUS=mimo-opus" \
    --env "ANTHROPIC_DEFAULT_SONNET=mimo-sonnet" \
    --env "CLAUDE_CODE_MAX_CONTEXT_TOKENS=100000"

assert_eq "mimo opus alias lands before the switch" \
  "mimo-opus" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_OPUS)"

# Second provider: GLM, no aliases supplied. Nothing mimo may outlive this.
assert_exit_code "re-bootstrap as GLM succeeds" 0 \
  run_bootstrap bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://glm.example.com/anthropic" \
    --auth-token "glm-token-12345" \
    --model "glm-5.3"

assert_eq "GLM base url replaces mimo" \
  "https://glm.example.com/anthropic" "$(env_get "$SETTINGS" ANTHROPIC_BASE_URL)"
assert_eq "GLM model replaces mimo" \
  "glm-5.3" "$(env_get "$SETTINGS" ANTHROPIC_MODEL)"
assert_eq "stale mimo opus alias is gone" \
  "(absent)" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_OPUS)"
assert_eq "stale mimo sonnet alias is gone" \
  "(absent)" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_SONNET)"
assert_eq "stale mimo harness setting is gone" \
  "(absent)" "$(env_get "$SETTINGS" CLAUDE_CODE_MAX_CONTEXT_TOKENS)"
assert_eq "no mimo key of any name remains" \
  "ANTHROPIC_AUTH_TOKEN
ANTHROPIC_BASE_URL
ANTHROPIC_MODEL" "$(env_keys "$SETTINGS")"

# === Test 4 (AC-4): missing base url / token / model refuses and writes nothing
echo ""
echo "=== Test 4 (AC-4): incomplete provider env refuses and writes nothing ==="

FAKE_HOME="$(fresh_home ac4)"
SETTINGS="$FAKE_HOME/settings.json"

# A pre-existing home whose contents must survive the refusal byte-for-byte.
cat > "$SETTINGS" <<'EOF'
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://previous.example.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "previous-token",
    "ANTHROPIC_MODEL": "previous-model"
  }
}
EOF
cp -p "$SETTINGS" "$SETTINGS.before"

assert_exit_code "no provider flags -> exit 3" 3 \
  run_bootstrap bash "$BOOTSTRAP" --apply --home "$FAKE_HOME"

assert_output_contains "no provider flags -> names all three missing fields" \
  "Missing:" \
  run_bootstrap bash "$BOOTSTRAP" --apply --home "$FAKE_HOME"

assert_file_unchanged "settings.json untouched by the refusal" \
  "$SETTINGS.before" "$SETTINGS"

assert_exit_code "missing --model only -> exit 3" 3 \
  run_bootstrap bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://glm.example.com/anthropic" \
    --auth-token "glm-token-12345"

assert_file_unchanged "settings.json untouched when --model is missing" \
  "$SETTINGS.before" "$SETTINGS"

assert_exit_code "--env ANTHROPIC_MODEL is refused as a usage error" 2 \
  run_bootstrap bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://glm.example.com/anthropic" \
    --auth-token "glm-token-12345" \
    --env "ANTHROPIC_MODEL=glm-5.3"

assert_output_contains "--env ANTHROPIC_MODEL points at --model" \
  "use --model" \
  run_bootstrap bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://glm.example.com/anthropic" \
    --auth-token "glm-token-12345" \
    --env "ANTHROPIC_MODEL=glm-5.3"

assert_file_unchanged "settings.json untouched when --env ANTHROPIC_MODEL is refused" \
  "$SETTINGS.before" "$SETTINGS"

# --model is the only source of truth for the model: the refusal above means
# --env can never fill in for a missing --model.
assert_exit_code "missing --model is still fail-closed with --env aliases present" 3 \
  run_bootstrap bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://glm.example.com/anthropic" \
    --auth-token "glm-token-12345" \
    --env "ANTHROPIC_DEFAULT_OPUS=glm-5.3"

assert_file_unchanged "settings.json untouched by the fail-closed refusal" \
  "$SETTINGS.before" "$SETTINGS"

# === Test 5 (AC-5): non-provider keys are preserved ===
echo ""
echo "=== Test 5 (AC-5): hooks, theme, permissions survive a provider write ==="

FAKE_HOME="$(fresh_home ac5)"
SETTINGS="$FAKE_HOME/settings.json"

cat > "$SETTINGS" <<'EOF'
{
  "theme": "dark",
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "true"
          }
        ]
      }
    ]
  },
  "permissions": {
    "allow": [
      "Bash"
    ]
  },
  "env": {
    "PRESERVED_KEY": "keep-me"
  }
}
EOF

assert_exit_code "bootstrap over a themed home succeeds" 0 \
  run_bootstrap bash "$BOOTSTRAP" --apply \
    --home "$FAKE_HOME" \
    --base-url "https://glm.example.com/anthropic" \
    --auth-token "glm-token-12345" \
    --model "glm-5.3" \
    --env "ANTHROPIC_DEFAULT_OPUS=glm-5.3"

assert_eq "theme preserved" "dark" \
  "$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('theme','(absent)'))" "$SETTINGS")"
assert_eq "hooks preserved" "true" \
  "$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['hooks']['PreToolUse'][0]['hooks'][0]['command'])" "$SETTINGS")"
assert_eq "permissions preserved" "Bash" \
  "$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['permissions']['allow'][0])" "$SETTINGS")"
assert_eq "non-provider env key preserved" \
  "keep-me" "$(env_get "$SETTINGS" PRESERVED_KEY)"
assert_eq "and the --env alias still lands" \
  "glm-5.3" "$(env_get "$SETTINGS" ANTHROPIC_DEFAULT_OPUS)"

# === Test 6 (AC-6): --from-ccswitch output is byte-identical to the golden ===
echo ""
echo "=== Test 6 (AC-6): --from-ccswitch output matches the pinned golden ==="

# The import path is entered through ccswitch_import.py, which reads
# $HOME/.cc-switch/cc-switch.db. HOME is the seam: no live CCSwitch store is
# touched and no real secret is required.
FAKE_USER="$SCRATCH/user"
CCSWITCH_DIR="$FAKE_USER/.cc-switch"
CC_HOME="$(fresh_home ac6)"
mkdir -p "$CCSWITCH_DIR"

python3 - "$CCSWITCH_DIR/cc-switch.db" <<'PY'
import json, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute(
    "CREATE TABLE providers ("
    "id TEXT PRIMARY KEY, app_type TEXT, name TEXT, settings_config TEXT, "
    "website_url TEXT, category TEXT, is_current BOOLEAN, provider_type TEXT)"
)
# Key order here is the golden's env order: normalize_for_worker emits the
# three required keys first, then extra_env in this order (minus any key
# outside ccswitch_import._EXTRA_ENV_KEYS).
settings_config = {
    "env": {
        "ANTHROPIC_BASE_URL": "https://ccswitch.example.com/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "sk-ccswitch-token-abc123",
        "ANTHROPIC_MODEL": "glm-5.3",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.3-fast",
    }
}
conn.execute(
    "INSERT INTO providers VALUES (?, 'claude', ?, ?, NULL, 'custom', 1, NULL)",
    ("golden-provider", "Golden Provider", json.dumps(settings_config)),
)
conn.commit()
conn.close()
PY

cat > "$CC_HOME/settings.json" <<'EOF'
{
  "theme": "dark",
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash"
      }
    ]
  },
  "permissions": {
    "allow": [
      "Bash"
    ]
  },
  "env": {
    "PRESERVED_KEY": "keep-me",
    "ANTHROPIC_DEFAULT_OPUS": "stale-opus"
  }
}
EOF

assert_exit_code "bootstrap --from-ccswitch --apply succeeds" 0 \
  run_bootstrap HOME="$FAKE_USER" bash "$BOOTSTRAP" --apply \
    --home "$CC_HOME" \
    --from-ccswitch \
    --provider "Golden Provider"

# Byte-identity, not subset: every key, every value, and the serialized form
# (json.dump indent=2 + trailing newline) are pinned. --env is not involved
# here -- this guards the import path against any change at all.
set +e
py_out="$(python3 - "$CC_HOME/settings.json" <<'PY'
import json, sys

path = sys.argv[1]
expected = {
    "theme": "dark",
    "hooks": {"PreToolUse": [{"matcher": "Bash"}]},
    "permissions": {"allow": ["Bash"]},
    "env": {
        "PRESERVED_KEY": "keep-me",
        "ANTHROPIC_BASE_URL": "https://ccswitch.example.com/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "sk-ccswitch-token-abc123",
        "ANTHROPIC_MODEL": "glm-5.3",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.3-fast",
    },
}
want = json.dumps(expected, indent=2) + "\n"
got = open(path).read()
if got == want:
    raise SystemExit(0)
print("--- expected (golden) ---")
print(want)
print("--- actual ---")
print(got)
raise SystemExit(1)
PY
)"
py_rc=$?
set -e
if [[ $py_rc -eq 0 ]]; then
  echo "  PASS: --from-ccswitch settings.json is byte-identical to the golden"
  pass=$((pass + 1))
else
  echo "  FAIL: --from-ccswitch settings.json differs from the golden"
  echo "$py_out"
  fail=$((fail + 1))
fi

# The stale alias from the seeded home is an ANTHROPIC_* key, so it is
# replaced wholesale; the import's own opus alias takes its place.
assert_eq "stale alias does not survive --from-ccswitch" \
  "(absent)" "$(env_get "$CC_HOME/settings.json" ANTHROPIC_DEFAULT_OPUS)"
assert_eq "imported opus alias lands" \
  "glm-5.3" "$(env_get "$CC_HOME/settings.json" ANTHROPIC_DEFAULT_OPUS_MODEL)"

# === Results ===
echo ""
echo "=== Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
  exit 1
fi
exit 0
