#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# test_runner_gate_discovery_fallback.sh
# =============================================================================
# The local_checks gate discovers its targets by scanning new commit messages
# for [plan:<slug>#step-N] trailers. The commit-trailer policy deliberately
# STRIPS those trailers on a shared remote (classify_remote → .ilk-remote-type),
# so on every shared-remote project the declared gate silently never ran and the
# sub-plan still shipped carrying `verification_tier: loop-verified`.
#
# Observed on a real run (issue #2340, consumer repo on a shared remote): one
# commit, gates enabled (`--run-local-checks`), zero `[local_checks` markers in
# the iteration log, no `local_checks` key in the JSONL record, sub-plan and
# master both `shipped`. The tests were green because neither the trailer suite
# nor the gate suite crosses the other's boundary.
#
# AC coverage:
#   AC-1: a trailerless commit yields NO targets from trailer scanning
#         (the precondition — documents why a fallback is needed at all)
#   AC-2: the fallback resolves the active (unshipped) sub-plan as "<slug> <step>"
#   AC-3: an all-shipped master yields NO fallback target (no invented gate)
#   AC-4: a non-numeric current_step degrades to step 0 rather than emitting junk
#   AC-5: the gate block calls the fallback when trailer scanning is empty, and
#         says so loudly when neither source resolves (structural)
#   AC-6: run_ilk_loop_claude.ps1 has the mirrored fallback (structural — no pwsh)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNNER="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.sh"
RUNNER_PS1="$REPO_ROOT/skills/ilk-loop/scripts/run_ilk_loop_claude.ps1"

PASS=0
FAIL=0

TEST_TMPDIR="$(mktemp -d)"
cleanup() { rm -rf "$TEST_TMPDIR"; }
trap cleanup EXIT

# AC-7/AC-8 call the runner's preflight() directly, but only to exercise
# run_id / --log-dir resolution. preflight() also refuses to continue unless
# `gtimeout`, `claude`, and `python3` are on PATH — presence checks that are
# incidental to what those two cases assert, and that made this test depend on
# a fully provisioned dev machine (it failed on CI with "gtimeout not found",
# then "Claude Code 'claude' not on PATH").
#
# Stub the two external binaries so the test is hermetic everywhere. This does
# not weaken the assertions: neither stub is ever invoked, because preflight
# only checks that the names resolve. python3 is a genuine test dependency and
# is intentionally NOT stubbed.
STUB_BIN="$TEST_TMPDIR/stub-bin"
mkdir -p "$STUB_BIN"
for _stub in claude gtimeout; do
  if ! command -v "$_stub" >/dev/null 2>&1; then
    printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB_BIN/$_stub"
    chmod +x "$STUB_BIN/$_stub"
  fi
done
PATH="$STUB_BIN:$PATH"
export PATH

ok()   { PASS=$((PASS + 1)); echo "  PASS: $1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

check_eq() {
  local desc="$1" got="$2" want="$3"
  if [[ "$got" == "$want" ]]; then ok "$desc"; else bad "$desc (got '$got', want '$want')"; fi
}

check_contains() {
  local desc="$1" hay="$2" needle="$3"
  case "$hay" in *"$needle"*) ok "$desc" ;; *) bad "$desc (missing: $needle)" ;; esac
}

# ----- fixture ---------------------------------------------------------------

# Build a project with docs/plans holding one MASTER + one sub-plan that
# declares local_checks. `status` is a parameter so the same fixture covers the
# active and all-shipped cases.
make_project() {
  local dir="$1" subplan_status="$2" current_step="${3:-0}"
  local plans="$dir/docs/plans"
  mkdir -p "$plans"
  cat > "$plans/MASTER-2026-08-03-gate.md" <<'EOF'
---
master_plan: 2026-08-03-gate
batch_date: 2026-08-03
status: active
---

# MASTER plan: Gate

## Sub-plan registry

| # | Sub-plan | Status |
|---|---|---|
| 1 | [2026-08-03-gate-work.md](./2026-08-03-gate-work.md) | pending |
EOF
  cat > "$plans/2026-08-03-gate-work.md" <<EOF
---
plan: gate-work
status: ${subplan_status}
current_step: ${current_step}
estimated_steps: 2
last_updated: 2026-08-03
verification_tier: loop-verified
local_checks:
  - command: echo "gate ran"
    timeout: 30
---

# Sub-plan: Gate work

Declares a gate. On a shared remote the commit carries no trailer.
EOF
}

# Source the runner for its functions without running main, then point the
# globals the fallback reads at the fixture.
source_runner_for() {
  local project="$1"
  export ILK_DOTSOURCE_ONLY=1
  _SKILL_ROOT="$REPO_ROOT/skills"
  # shellcheck disable=SC1090
  source "$RUNNER"
  unset ILK_DOTSOURCE_ONLY
  PROJECT_PATH="$project"
  REPOS=("$project")
  LOOP_STATUS_SCRIPT="$REPO_ROOT/skills/ilk-loop/scripts/loop_status.py"
}

# ----- AC-1: a trailerless commit yields no trailer targets ------------------

ac1_trailerless_commit() {
  local REPO before after targets
  REPO="$TEST_TMPDIR/ac1"
  make_project "$REPO" "in-progress" 1
  git -C "$REPO" init -q
  git -C "$REPO" config user.email t@example.com
  git -C "$REPO" config user.name Test
  git -C "$REPO" add -A
  git -C "$REPO" commit -q -m "init"
  before=$(git -C "$REPO" rev-parse HEAD)
  echo "change" > "$REPO/file.txt"
  git -C "$REPO" add file.txt
  # A shared-remote commit: no [plan:...#step-N] trailer, by policy.
  git -C "$REPO" commit -q -m "fix(convex): avoid a throw for absent keys"
  after=$(git -C "$REPO" rev-parse HEAD)

  source_runner_for "$REPO"
  targets=$(get_local_check_targets "$REPO" "$before" "$after" || true)
  check_eq "trailer scanning finds nothing on a trailerless commit" "${targets:-<empty>}" "<empty>"
}

# ----- AC-2/AC-3/AC-4: the fallback ------------------------------------------

ac2_active_subplan() {
  local REPO got
  REPO="$TEST_TMPDIR/ac2"
  make_project "$REPO" "in-progress" 1
  source_runner_for "$REPO"
  got=$(get_active_subplan_targets || true)
  check_eq "active sub-plan resolved as '<slug> <step>'" "$got" "gate-work 1"
}

ac3_all_shipped() {
  local REPO got defined
  REPO="$TEST_TMPDIR/ac3"
  make_project "$REPO" "shipped" 2
  source_runner_for "$REPO"
  # Assert the function EXISTS before asserting it returns nothing: an absent
  # function also returns nothing, so the emptiness check alone passes vacuously
  # against a runner that never grew the fallback.
  if declare -F get_active_subplan_targets >/dev/null 2>&1; then
    defined=yes
  else
    defined=no
  fi
  check_eq "fallback function is defined" "$defined" "yes"
  got=$(get_active_subplan_targets 2>/dev/null || true)
  check_eq "no target invented when every sub-plan is shipped" "${got:-<empty>}" "<empty>"
}

ac4_non_numeric_step() {
  local REPO got
  REPO="$TEST_TMPDIR/ac4"
  make_project "$REPO" "in-progress" "?"
  source_runner_for "$REPO"
  got=$(get_active_subplan_targets || true)
  check_eq "non-numeric step becomes 0" "$got" "gate-work 0"
}

# ----- AC-5/AC-6: wiring + parity (structural) -------------------------------

echo "=== AC-1: trailerless commit → no targets from trailer scanning ==="
ac1_trailerless_commit
echo "=== AC-2: fallback resolves the active sub-plan ==="
ac2_active_subplan
echo "=== AC-3: all-shipped master yields no fallback target ==="
ac3_all_shipped
echo "=== AC-4: non-numeric current_step degrades to 0 ==="
ac4_non_numeric_step

ac7_run_id_matches_log_dir() {
  # The launcher generates a run id for logs/runs/<id> and passes it as
  # --log-dir; the runner then generated a SECOND one for the terminal record.
  # When the two `date` calls straddled a second boundary the record disagreed
  # with the directory holding that run's logs (observed: run_id
  # 20260803-171605 against logs/runs/20260803-171604), so correlating a run to
  # its own logs needed fuzzy timestamp matching.
  local REPO
  REPO="$TEST_TMPDIR/ac7"
  make_project "$REPO" "in-progress" 1
  source_runner_for "$REPO"

  LOG_DIR="$TEST_TMPDIR/ac7-logs/runs/20260803-171604"
  JSONL_LOG_PATH="$TEST_TMPDIR/ac7-logs/.ilk-loop.log"
  RUN_ID=""
  preflight
  check_eq "run_id adopts the --log-dir basename" "$RUN_ID" "20260803-171604"
  check_eq "RUN_LOG_DIR is the supplied --log-dir" "$RUN_LOG_DIR" "$LOG_DIR"
}

ac8_run_id_generated_when_log_dir_unshaped() {
  # A --log-dir that is not a run-id directory must not be adopted as the id.
  local REPO
  REPO="$TEST_TMPDIR/ac8"
  make_project "$REPO" "in-progress" 1
  source_runner_for "$REPO"

  LOG_DIR="$TEST_TMPDIR/ac8-logs/not-a-run-id"
  JSONL_LOG_PATH="$TEST_TMPDIR/ac8-logs/.ilk-loop.log"
  RUN_ID=""
  preflight
  if [[ "$RUN_ID" =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
    ok "run_id falls back to a generated timestamp"
  else
    bad "run_id falls back to a generated timestamp (got '$RUN_ID')"
  fi
  check_eq "an unshaped --log-dir is not adopted as the id" \
    "$([[ "$RUN_ID" == "not-a-run-id" ]] && echo adopted || echo rejected)" "rejected"
}

echo "=== AC-7: run_id equals its own logs/runs directory name ==="
ac7_run_id_matches_log_dir
echo "=== AC-8: unshaped --log-dir still yields a generated run_id ==="
ac8_run_id_generated_when_log_dir_unshaped

echo "=== AC-5: the gate block uses the fallback and warns loudly ==="
sh_src="$(cat "$RUNNER")"
check_contains "gate block calls the fallback" "$sh_src" 'get_active_subplan_targets >> "$all_targets_file"'
check_contains "loud warning when nothing resolves" "$sh_src" 'NO gate target could be resolved'

echo "=== AC-6: ps1 parity ==="
ps1_src="$(cat "$RUNNER_PS1")"
check_contains "ps1 defines the fallback"      "$ps1_src" 'function Get-ActiveSubplanTarget'
check_contains "ps1 gate block calls it"       "$ps1_src" 'Get-ActiveSubplanTarget -Project $ProjectPath'
check_contains "ps1 warns loudly"              "$ps1_src" 'NO gate target could be resolved'

echo
echo "PASS: $PASS  FAIL: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "ALL PASS"
