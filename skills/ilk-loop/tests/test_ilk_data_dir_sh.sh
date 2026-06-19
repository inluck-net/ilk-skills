#!/usr/bin/env bash
# Test ilk_data_dir precedence: ILK_DATA_HOME > ILK_DATA_DIR > ~/.ilk-data.
#
# Run: bash skills/ilk-loop/tests/test_ilk_data_dir_sh.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
HELPER="$SCRIPT_DIR/../scripts/_ilk_data_dir.sh"

fail=0
assert() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: $desc — got '$actual', expected '$expected'" >&2
    fail=1
  fi
}

# Source the helper
source "$HELPER"

# Save and clear env vars for clean testing.
saved_home="${ILK_DATA_HOME:-}"
saved_dir="${ILK_DATA_DIR:-}"
unset ILK_DATA_HOME 2>/dev/null || true
unset ILK_DATA_DIR  2>/dev/null || true

# Case 1: ILK_DATA_HOME set → returns ILK_DATA_HOME
export ILK_DATA_HOME="/test-home"
unset ILK_DATA_DIR 2>/dev/null || true
result="$(ilk_data_dir)"
assert "case 1: ILK_DATA_HOME set" "/test-home" "$result"

# Case 2: Only ILK_DATA_DIR set → returns ILK_DATA_DIR
unset ILK_DATA_HOME 2>/dev/null || true
export ILK_DATA_DIR="/test-dir"
result="$(ilk_data_dir)"
assert "case 2: only ILK_DATA_DIR set" "/test-dir" "$result"

# Case 3: Neither set → returns ~/.ilk-data
unset ILK_DATA_HOME 2>/dev/null || true
unset ILK_DATA_DIR  2>/dev/null || true
result="$(ilk_data_dir)"
assert "case 3: neither set" "$HOME/.ilk-data" "$result"

# Case 4: Both set → ILK_DATA_HOME wins
export ILK_DATA_HOME="/test-home"
export ILK_DATA_DIR="/test-dir"
result="$(ilk_data_dir)"
assert "case 4: both set" "/test-home" "$result"

# Restore env vars
if [[ -n "$saved_home" ]]; then export ILK_DATA_HOME="$saved_home"; else unset ILK_DATA_HOME 2>/dev/null || true; fi
if [[ -n "$saved_dir"  ]]; then export ILK_DATA_DIR="$saved_dir";   else unset ILK_DATA_DIR  2>/dev/null || true; fi

if [[ "$fail" -ne 0 ]]; then
  echo "RED: ilk_data_dir precedence is incorrect" >&2
  exit 1
fi
echo "PASS: ilk_data_dir — all precedence cases correct (AC-1/AC-3)"
exit 0
