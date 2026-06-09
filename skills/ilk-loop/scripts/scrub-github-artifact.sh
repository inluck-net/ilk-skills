#!/usr/bin/env bash
# scrub-github-artifact.sh — denylist gate for reviewer-facing GitHub artifacts.
#
# Reads a file (arg) or stdin.  Checks every line for toolchain vocabulary
# tokens that should never appear in PR/issue bodies or commit messages
# destined for shared repos.
#
# Exit 0  — clean (no hits)
# Exit 1  — one or more tokens found; offending lines printed to stderr
# Exit 2  — usage / arg error
#
# Flags:
#   --skip-list FILE   one regex per line; matches whose *word* is in this
#                       list are suppressed (false-positive control).
#   --verbose           print each token check to stderr (debug).

set -euo pipefail

# ── denylist ─────────────────────────────────────────────────────────────────
# Each entry is an extended regex; the script wraps it with \b before use.
DENY_TOKENS=(
  # core toolchain names
  'ilk'
  'ilk-plan'
  'ilk-skills'
  'ilk-loop'
  'ilk-launcher'
  'ilk-run'
  # plan vocabulary
  'MASTER plan'
  'sub-plan'
  'sub plan'
  # runner / launcher flags
  '\-RunLocalChecks'
  'RunLocalChecks'
  'local_checks'
  # decomposition doc
  'decomposition-principles'
)

# trailer regex — [plan:<anything>]
# ERE: [^]]+ matches one or more chars that are not ']'
TRAILER_RE='\[plan:[^]]+\]'

# ── built-in skip list ──────────────────────────────────────────────────────
# Common English words that contain "ilk" as a substring but are NOT toolchain
# references.  Each entry is a full-word regex (anchored with ^…$ at check).
BUILTIN_SKIP=(
  'silk'
  'milk'
  'bilk'
  'milks'
  'silks'
  'silky'
  'milky'
  'bilks'
)

# ── arg parsing ──────────────────────────────────────────────────────────────
INPUT_FILE=""
USER_SKIP_FILE=""
VERBOSE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-list)
      [[ $# -ge 2 ]] || { echo "error: --skip-list requires a FILE arg" >&2; exit 2; }
      USER_SKIP_FILE="$2"; shift 2 ;;
    --verbose) VERBOSE=1; shift ;;
    -h|--help)
      sed -n '2,/^$/s/^# \?//p' "$0"; exit 0 ;;
    -*)
      echo "error: unknown flag: $1" >&2; exit 2 ;;
    *)
      [[ -z "$INPUT_FILE" ]] || { echo "error: unexpected extra arg: $1" >&2; exit 2; }
      INPUT_FILE="$1"; shift ;;
  esac
done

# ── build combined skip list ─────────────────────────────────────────────────
SKIP_LIST=("${BUILTIN_SKIP[@]}")
if [[ -n "$USER_SKIP_FILE" ]]; then
  while IFS= read -r line; do
    [[ -n "$line" ]] && SKIP_LIST+=("$line")
  done < "$USER_SKIP_FILE"
fi

# ── helpers ──────────────────────────────────────────────────────────────────

# Check if a matched word should be skipped (false-positive).
# $1 = the matched word/text
should_skip() {
  local match_lower
  match_lower=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  for skip in "${SKIP_LIST[@]}"; do
    # exact case-insensitive word match
    if [[ "$match_lower" == "${skip}" ]]; then
      return 0  # skip
    fi
  done
  return 1  # don't skip
}

# Build grep pattern: wrap token with \b for word-boundary matching.
# Use single-quoted '\b' — '\\b' would double-escape the backslash.
build_pattern() {
  local token="$1"
  printf '%s' '\b'"${token}"'\b'
}

# ── main ─────────────────────────────────────────────────────────────────────
violations=0
declare -a VIOLATION_LINES=()

# Read input line by line.
while IFS= read -r line || [[ -n "$line" ]]; do
  lineno=$(( ${lineno:-0} + 1 ))

  # Check each deny token.
  for token in "${DENY_TOKENS[@]}"; do
    pattern=$(build_pattern "$token")
    # Use grep -oP to extract the matched word; then check skip list.
    if matched=$(echo "$line" | grep -oiE "$pattern" 2>/dev/null | head -1); then
      if ! should_skip "$matched"; then
        VIOLATION_LINES+=("${INPUT_FILE:-stdin}:${lineno}: ${token}")
        violations=$((violations + 1))
        [[ "$VERBOSE" -eq 1 ]] && echo "  HIT: token='${token}' word='${matched}'" >&2
        break  # one violation per line is enough
      else
        [[ "$VERBOSE" -eq 1 ]] && echo "  SKIP: token='${token}' word='${matched}' (false-positive)" >&2
      fi
    fi
  done

  # Check trailer pattern separately (not word-boundary — it's a regex).
  if echo "$line" | grep -qE "$TRAILER_RE" 2>/dev/null; then
    if ! should_skip "$(echo "$line" | grep -oE "$TRAILER_RE" | head -1)"; then
      VIOLATION_LINES+=("${INPUT_FILE:-stdin}:${lineno}: [plan:…] trailer")
      violations=$((violations + 1))
    fi
  fi

done < "${INPUT_FILE:-/dev/stdin}"

# ── report ───────────────────────────────────────────────────────────────────
if [[ "$violations" -gt 0 ]]; then
  echo "scrub-github-artifact: FAIL — ${violations} violation(s) found:" >&2
  for v in "${VIOLATION_LINES[@]}"; do
    echo "  $v" >&2
  done
  exit 1
else
  echo "scrub-github-artifact: PASS" >&2
  exit 0
fi
