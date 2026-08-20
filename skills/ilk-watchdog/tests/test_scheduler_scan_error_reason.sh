#!/usr/bin/env bash
# The scheduler must not log `all-queues-empty` when the scan could not look.
#
# Regression guard for 2026-08-20: `scheduler_scan.py` raised a TypeError on two
# of nine projects, `scan_projects` swallowed it per-project by design, and the
# scheduler then logged the generic `idle (all-queues-empty)` on every 5-minute
# poll for over three hours. "No work" and "could not look" were indistinguish-
# able in the only artifact an operator reads.
#
# Method: shim `python3` on PATH so the scan invocation emits an empty project
# list on stdout AND a `[scan-error] <key>: ...` line on stderr, exactly as the
# real scanner now does. All other python calls delegate to the real
# interpreter. Then assert the scheduler's own log line names the project.
#
# Hermetic: no live scheduler, no dispatch (--dry-run --once), no network.
# Exit 0 = green, exit 1 = red.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SCHEDULER="$REPO_ROOT/skills/ilk-watchdog/scripts/scheduler.sh"

REAL_PYTHON="$(command -v python3)"
[[ -n "$REAL_PYTHON" ]] || { echo "FAIL: no python3 on PATH" >&2; exit 1; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/sched-scan-err-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

fail=0
passes=0
check() {
  local name="$1" cond="$2"
  if [[ "$cond" == "0" ]]; then echo "  PASS: $name"; passes=$((passes+1))
  else echo "  FAIL: $name" >&2; fail=$((fail+1)); fi
}

# --- shim: intercept only the scheduler_scan.py invocation -------------------
mkdir -p "$TMP/bin"
cat > "$TMP/bin/python3" <<SHIM
#!/usr/bin/env bash
# \$1 is scheduler_scan.py when the scheduler is scanning; everything else
# (the json -c one-liners, blacklist_status, ...) goes to the real python3.
for arg in "\$@"; do
  case "\$arg" in
    *scheduler_scan.py)
      if [[ -n "\${SHIM_SCAN_ERROR:-}" ]]; then
        echo "[scan-error] \$SHIM_SCAN_ERROR: TypeError: can't compare offset-naive and offset-aware datetimes @ scheduler_scan.py:394" >&2
      fi
      echo "[]"
      exit 0
      ;;
  esac
done
exec "$REAL_PYTHON" "\$@"
SHIM
chmod +x "$TMP/bin/python3"

run_once() {
  # $1 = value for SHIM_SCAN_ERROR ("" = a clean scan)
  #
  # HOME must be isolated: scheduler.sh resolves SCHEDULER_LOG_DIR (and its
  # pidfile) from ${HOME}, NOT from ILK_DATA_HOME, so without this the test
  # writes decision lines into the operator's real journal. ILK_SKILL_HOME must
  # be set too, or scheduler_scan.py cannot resolve its skill root under the
  # fake HOME. (Both learned the hard way on 2026-08-20.)
  local run_home="$TMP/home-$1-$RANDOM"
  mkdir -p "$run_home"
  SHIM_SCAN_ERROR="$1" \
  HOME="$run_home" \
  ILK_SKILL_HOME="$REPO_ROOT/skills" \
  ILK_DATA_HOME="$run_home/data" \
  PATH="$TMP/bin:$PATH" \
    bash "$SCHEDULER" --dry-run --once 2>"$TMP/stderr.txt"
  echo "---LOG---"
  cat "$run_home/.ilk-data/logs/scheduler.log" 2>/dev/null
}

# --- AC-1: an unscannable project gets its own reason ------------------------
out=$(run_once "users-chad-projects-github-inluck-net-gh-resolve")
check "unscannable -> decision JSON carries skip-scan-error" \
  "$(grep -q 'skip-scan-error' <<<"$out" && echo 0 || echo 1)"
check "unscannable -> reason names the project key" \
  "$(grep -q 'users-chad-projects-github-inluck-net-gh-resolve' <<<"$out" && echo 0 || echo 1)"
check "unscannable -> scheduler.log line is NOT all-queues-empty" \
  "$(sed -n '/---LOG---/,$p' <<<"$out" | grep -q 'all-queues-empty' && echo 1 || echo 0)"
check "unscannable -> scheduler.log line records skip-scan-error" \
  "$(sed -n '/---LOG---/,$p' <<<"$out" | grep -q 'skip-scan-error' && echo 0 || echo 1)"

# --- AC-2: a genuinely empty queue keeps the original reason ------------------
out=$(run_once "")
check "clean scan, 0 projects -> all-queues-empty preserved" \
  "$(grep -q 'all-queues-empty' <<<"$out" && echo 0 || echo 1)"
check "clean scan -> no spurious skip-scan-error" \
  "$(grep -q 'skip-scan-error' <<<"$out" && echo 1 || echo 0)"

echo
if [[ $fail -eq 0 ]]; then echo "ALL PASS ($passes assertions)"; exit 0; fi
echo "$fail FAILED of $((fail+passes)) assertions" >&2
exit 1
