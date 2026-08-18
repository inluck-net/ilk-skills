#!/usr/bin/env bash
# wait_for_background_output.sh — poll a backgrounded command's .output file
# for its exit marker, and report the recorded exit code once it appears.
#
# Usage:
#   wait_for_background_output.sh <file> [--timeout SECONDS] [--poll-ms MS]
#
# The harness's Bash tool auto-backgrounds long commands rather than killing
# them. Their .output files accumulate stdout + stderr and end with a line
# like:
#   [exited with code 0]
#
# This helper waits for that marker rather than trusting an early read of
# the file. It is the correct replacement for the broken idiom:
#   while ps aux | grep -q "pytest" | grep -v grep; do sleep 10; done
# (which exits instantly because grep -q closes the pipe).
#
# Exit codes:
#   0..N  — the recorded exit code from the marker
#   125   — inconclusive: bound expired without finding the marker
#   126   — usage error (missing file argument)
#
# Context: gh-resolve run 20260818-154347, ~24 of 43 iteration minutes
# burned re-running a suite whose result was already written.
#
# macOS-compatible: no flock(1), no pgrep -c.

set -euo pipefail

# ── defaults ──────────────────────────────────────────────────────────────────
TIMEOUT_SEC=600    # matches the harness's BASH_DEFAULT_TIMEOUT_MS / 1000
POLL_MS=500

# ── parse args ────────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo "usage: wait_for_background_output.sh <file> [--timeout SECONDS] [--poll-ms MS]" >&2
    exit 126
fi

OUTPUT_FILE="$1"; shift

while [[ $# -gt 0 ]]; do
    case "$1" in
        --timeout)
            TIMEOUT_SEC="$2"; shift 2 ;;
        --poll-ms)
            POLL_MS="$2"; shift 2 ;;
        *)
            echo "unknown option: $1" >&2
            exit 126 ;;
    esac
done

if [[ ! -e "$OUTPUT_FILE" ]]; then
    echo "wait_for_background_output: file not found: $OUTPUT_FILE" >&2
    exit 126
fi

# ── poll ──────────────────────────────────────────────────────────────────────
POLL_SEC=$(awk "BEGIN {printf \"%.3f\", $POLL_MS / 1000}")
ITERATIONS=0
MAX_ITERS=$(( TIMEOUT_SEC * 1000 / POLL_MS ))

while (( ITERATIONS < MAX_ITERS )); do
    # Look for the exit marker in the file (may be anywhere, but usually last line)
    if grep -q '\[exited with code [0-9]\+\]' "$OUTPUT_FILE" 2>/dev/null; then
        # Extract the exit code from the marker
        EXIT_CODE=$(grep -o '\[exited with code [0-9]\+\]' "$OUTPUT_FILE" | tail -1 | sed 's/.*code \([0-9]*\).*/\1/')
        echo "$EXIT_CODE"
        exit "$EXIT_CODE"
    fi

    sleep "$POLL_SEC"
    ITERATIONS=$(( ITERATIONS + 1 ))
done

# ── bound expired ─────────────────────────────────────────────────────────────
echo "wait_for_background_output: inconclusive — no exit marker found in ${TIMEOUT_SEC}s (bound: ${TIMEOUT_SEC}s)" >&2
exit 125
