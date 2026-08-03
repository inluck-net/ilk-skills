#!/usr/bin/env bash
set -uo pipefail

# =============================================================================
# test_runner_gate_e2e.sh — the declared gate must actually EXECUTE
# =============================================================================
# Runs the REAL run_ilk_loop_claude.sh end to end against a throwaway project,
# with a scripted `claude` on PATH standing in for the agent: it commits, then
# marks the sub-plan `shipped`, exactly as a real iteration does.
#
# The gate's declared command writes a MARKER FILE. "The gate ran" is therefore
# proven by an artifact only the gate command could have created — not by
# grepping a log, and not by asserting the shape of a call.
#
# This test exists because the unit-level version of the same fix PASSED while
# the real run still skipped the gate. The fallback read the active sub-plan
# *after* the iteration, by which time the agent had already flipped it to
# `shipped`, so "first unshipped sub-plan" resolved to nothing. Only an
# end-to-end run surfaced that ordering; a test that stubs the ordering cannot.
#
# AC coverage:
#   AC-1: shared remote + trailerless commit → the gate still EXECUTES (marker)
#   AC-2: the gate result is recorded structurally in the run summary
#   AC-3: run_id in the summary equals its own logs/runs directory name
#   AC-4: the MASTER registry row is reconciled to the sub-plan's real status
#   AC-5: personal remote + trailered commit → trailer discovery still used
#         (the fallback stays silent, no behaviour change on that path)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNNER="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

# The runner hard-requires gtimeout (macOS) and python3; skip rather than fail
# on a box without them.
for dep in gtimeout python3 git jq; do
  if ! command -v "$dep" >/dev/null 2>&1; then
    echo "SKIP: $dep not on PATH (runner prerequisite)"
    exit 0
  fi
done

# Bound the run: a wedged loop here would otherwise hang the whole suite.
TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-300}"
_SELF_PID=$$
if [[ "$TEST_TIMEOUT_SEC" -gt 0 ]]; then
  (
    sleep "$TEST_TIMEOUT_SEC"
    kill -0 "$_SELF_PID" 2>/dev/null || exit 0
    echo "" >&2
    echo "TIMEOUT: test_runner_gate_e2e.sh exceeded ${TEST_TIMEOUT_SEC}s — aborting." >&2
    kill -TERM "$_SELF_PID" 2>/dev/null
  ) &
  _WATCHER=$!
  trap 'kill "$_WATCHER" 2>/dev/null || true' EXIT
fi

# ---- one end-to-end run -----------------------------------------------------
# run_case <remote_mode: shared|personal> <trailer_mode: trailer|notrailer>
# Exports: CASE_WORK, CASE_MARKER, CASE_JSONL, CASE_PROJECT, CASE_RUN_DIR
run_case() {
  local remote_mode="$1" trailer_mode="$2"

  CASE_WORK="$(mktemp -d)"
  CASE_PROJECT="$CASE_WORK/project"
  CASE_MARKER="$CASE_PROJECT/GATE_RAN.marker"
  CASE_JSONL="$CASE_WORK/logs/.ilk-loop.log"
  CASE_RUN_ID="20260803-171604"   # fixed, so run_id adoption is observable
  CASE_RUN_DIR="$CASE_WORK/logs/runs/$CASE_RUN_ID"

  mkdir -p "$CASE_PROJECT/docs/plans" "$CASE_WORK/bin" "$CASE_WORK/logs" "$CASE_WORK/home"

  git init -q "$CASE_PROJECT"
  git -C "$CASE_PROJECT" config user.email e2e@example.com
  git -C "$CASE_PROJECT" config user.name E2E
  if [[ "$remote_mode" == "personal" ]]; then
    # A personal-namespace URL → classify_remote returns "personal" → the agent
    # is told to keep [plan:...#step-N] trailers. No network access occurs: only
    # the URL string is read (no branch: block in this fixture).
    git -C "$CASE_PROJECT" remote add origin \
      https://github.com/inluck-net/gh-resolve-canary.git
  else
    # Anything outside that namespace → "shared" → trailers are STRIPPED. This
    # is the condition every shared-remote consumer project runs under.
    git init -q --bare "$CASE_WORK/remote.git"
    git -C "$CASE_PROJECT" remote add origin "$CASE_WORK/remote.git"
  fi

  cat > "$CASE_PROJECT/docs/plans/MASTER-2026-08-03-e2e.md" <<'MASTER'
---
master_plan: 2026-08-03-e2e
batch_date: 2026-08-03
status: active
---

# MASTER plan: e2e

## Sub-plan registry

| # | Sub-plan | Status |
|---|---|---|
| 1 | [2026-08-03-e2e-work.md](./2026-08-03-e2e-work.md) | pending |
MASTER

  cat > "$CASE_PROJECT/docs/plans/2026-08-03-e2e-work.md" <<SUBPLAN
---
plan: e2e-work
status: in-progress
current_step: 1
estimated_steps: 1
last_updated: 2026-08-03
verification_tier: loop-verified
local_checks:
  - command: bash -c 'echo GATE_RAN > "${CASE_MARKER}"'
    timeout: 30
---

# Sub-plan: e2e work

The gate writes a marker file, so its execution is provable.
SUBPLAN

  git -C "$CASE_PROJECT" add -A >/dev/null
  git -C "$CASE_PROJECT" commit -q -m "init plans"

  # Scripted agent: commit (trailer per mode), then mark the sub-plan shipped —
  # the ordering that broke the first version of the fallback.
  cat > "$CASE_WORK/bin/claude" <<AGENT
#!/usr/bin/env bash
set -uo pipefail
P="$CASE_PROJECT"
echo "work" >> "\$P/src.txt"
git -C "\$P" add -A >/dev/null 2>&1
if [[ "$trailer_mode" == "trailer" ]]; then
  git -C "\$P" commit -q -m "feat: do the work [plan:e2e-work#step-1]" >/dev/null 2>&1
else
  git -C "\$P" commit -q -m "feat: do the work" >/dev/null 2>&1
fi
python3 - "\$P/docs/plans/2026-08-03-e2e-work.md" <<'PY'
import sys, re
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
open(p, "w", encoding="utf-8").write(
    re.sub(r"^status: .*\$", "status: shipped", s, count=1, flags=re.M))
PY
printf '%s\n' '{"type":"system","subtype":"init"}'
printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}'
printf '%s\n' '{"type":"result","subtype":"success","is_error":false,"result":"done"}'
exit 0
AGENT
  chmod +x "$CASE_WORK/bin/claude"

  HOME="$CASE_WORK/home" \
  PATH="$CASE_WORK/bin:$PATH" \
  ILK_SKILL_HOME="$REPO_ROOT/skills" \
  ILK_DATA_HOME="$CASE_WORK/ilk-data" \
  bash "$RUNNER" \
    --project-path "$CASE_PROJECT" \
    --max-iterations 1 \
    --iteration-timeout-min 2 \
    --run-local-checks \
    --log-dir "$CASE_RUN_DIR" \
    --jsonl-log "$CASE_JSONL" \
    > "$CASE_WORK/runner.out" 2>&1
}

jsonl_field() {
  # jsonl_field <file> <key> — read <key> off the last record.
  python3 - "$1" "$2" <<'PY'
import json, sys
try:
    lines = [l for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
    v = json.loads(lines[-1]).get(sys.argv[2])
    print("" if v is None else (json.dumps(v) if not isinstance(v, str) else v))
except Exception:
    print("")
PY
}

# ===== Case 1: shared remote, trailerless commit =============================

echo "=== AC-1..4: shared remote + trailerless commit ==="
run_case shared notrailer

echo "  (remote-type: $(cat "$CASE_PROJECT/.ilk-remote-type" 2>/dev/null || echo '?'))"

if [[ -f "$CASE_MARKER" ]]; then
  ok "the declared gate EXECUTED (marker file written by its command)"
else
  bad "the declared gate never executed (no marker) — see $CASE_WORK/runner.out"
fi

lc="$(jsonl_field "$CASE_JSONL" local_checks)"
case "$lc" in
  *'"outcome": "pass"'*|*'"outcome":"pass"'*)
    ok "gate result recorded structurally in the run summary" ;;
  "") bad "no local_checks in the run summary (gate result unrecorded)" ;;
  *)  bad "local_checks present but not a pass: $lc" ;;
esac

rid="$(jsonl_field "$CASE_JSONL" run_id)"
if [[ "$rid" == "$CASE_RUN_ID" ]]; then
  ok "run_id equals its own logs/runs directory name"
else
  bad "run_id '$rid' != log dir '$CASE_RUN_ID'"
fi

row="$(grep -m1 'e2e-work.md' "$CASE_PROJECT/docs/plans/MASTER-2026-08-03-e2e.md" 2>/dev/null || true)"
case "$row" in
  *"| shipped |"*) ok "MASTER registry row reconciled to the sub-plan's real status" ;;
  *) bad "registry row still stale: $row" ;;
esac
rm -rf "$CASE_WORK"

# ===== Case 2: personal remote, trailered commit =============================

echo "=== AC-5: personal remote + trailered commit (no behaviour change) ==="
run_case personal trailer

if [[ -f "$CASE_MARKER" ]]; then
  ok "the declared gate EXECUTED on the trailer path too"
else
  bad "gate did not execute on the trailer path — see $CASE_WORK/runner.out"
fi

if grep -q "no commit trailers found" "$CASE_WORK/runner.out" 2>/dev/null; then
  bad "fallback fired even though the commit carried a trailer"
else
  ok "fallback stayed silent — trailer discovery was used"
fi
rm -rf "$CASE_WORK"

echo
echo "PASS: $PASS  FAIL: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "ALL PASS"
