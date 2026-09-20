#!/usr/bin/env bash
# =============================================================================
# Regression (2026-09-20): the worker home's settings.json env block must be
# EXPORTED into the agent subprocess.
#
# The runner used to strip the ambient ANTHROPIC_* vars and trust the CLI to
# apply settings.json's env — it never did, so every scheduler-driven worker
# ran the CLI default (Anthropic official, opus-5) while records claimed the
# configured model. The account's five-hour window burned to exhaustion with
# the configured token-plan endpoint never reached (operator-confirmed).
#
# This test pins BOTH halves: the export generator actually exports the
# settings env, and the runner no longer carries the env -u strip.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RUNNER="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"

# --- 1. the export generator exports what settings.json declares ----------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/cfg"
cat > "$TMP/cfg/settings.json" <<'EOF'
{"env": {"ANTHROPIC_BASE_URL": "https://example.test/api",
         "ANTHROPIC_MODEL": "glm-5.3",
         "ANTHROPIC_AUTH_TOKEN": "tok-en"}}
EOF

exports=$(SETTINGS_JSON="$TMP/cfg/settings.json" python3 -c '
import json, os, shlex
try:
    d = json.load(open(os.environ["SETTINGS_JSON"]))
except Exception:
    raise SystemExit(0)
for k, v in sorted(d.get("env", {}).items()):
    print("export %s=%s;" % (k, shlex.quote(str(v))))
')

eval "$exports"
[[ "$ANTHROPIC_BASE_URL" == "https://example.test/api" ]] \
  || { echo "FAIL: ANTHROPIC_BASE_URL not exported" >&2; exit 1; }
[[ "$ANTHROPIC_MODEL" == "glm-5.3" ]] \
  || { echo "FAIL: ANTHROPIC_MODEL not exported" >&2; exit 1; }
[[ "$ANTHROPIC_AUTH_TOKEN" == "tok-en" ]] \
  || { echo "FAIL: ANTHROPIC_AUTH_TOKEN not exported" >&2; exit 1; }

# --- 2. the runner applies the exports and dropped the strip ---------------
grep -q 'settings_env_exports' "$RUNNER" \
  || { echo "FAIL: runner no longer exports the settings env" >&2; exit 1; }
if grep -q 'env -u ANTHROPIC_API_KEY' "$RUNNER"; then
  echo "FAIL: runner still strips ANTHROPIC_* instead of applying settings env" >&2
  exit 1
fi

bash -n "$RUNNER"

echo "PASS: worker settings env is applied to the agent subprocess"
