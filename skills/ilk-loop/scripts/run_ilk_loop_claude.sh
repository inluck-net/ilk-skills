#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ilk-loop runner (Claude Code) — bash port of run_ilk_loop_claude.ps1
# =============================================================================
# Contract: same JSONL fields, same last-exit.json schema, same stop-condition
# semantics as the Win PowerShell source.
#
# Stream-json rendering is delegated to _stream_json_render.py (Python helper
# shared across platforms) so the bash script stays focused on orchestration.
# =============================================================================

# ----- Defaults & globals ----------------------------------------------------

# Populated by argument parsing in main().
PROJECT_PATH=""
MAX_ITERATIONS=30
ITERATION_TIMEOUT_MIN=30
LOOP_STATUS_SCRIPT=""
LOG_DIR=""
PROMPT="/ilk please continue the active plan"
MAX_BUDGET_USD=0
MODEL=""
RUN_LOCAL_CHECKS=false
LOCAL_CHECKS_TIMEOUT_SEC=180
LOCAL_CHECKS_SCRIPT=""
MCP_CONFIG_PATH=""

# Internal state
RUN_ID=""
RUN_LOG_DIR=""
JSONL_LOG=""
SETTINGS_HAS_ENV=0
REPOS=()

# ----- Argument parsing ------------------------------------------------------

parse_args() {
  : # TODO: step 1 — long-flag arg parsing with defaults and --help
}

# ----- Pre-flight checks -----------------------------------------------------

preflight() {
  : # TODO: step 2 — gtimeout check, claude check, settings.json env detection
}

# ----- Helpers ---------------------------------------------------------------

discover_git_repos() {
  : # TODO: step 3 — resolve project root, call ilk_paths.py, build REPOS array
}

get_repo_heads() {
  : # TODO: step 5 — snapshot HEAD of every repo in REPOS
}

get_new_commit_count() {
  : # TODO: step 5 — git rev-list --count Before..After
}

get_local_check_targets() {
  : # TODO: step 7 — scan new commits for [plan:<slug>#step-N] tags
}

get_ilk_runtime_dir() {
  : # TODO: step 7 — resolve external runtime dir via ilk_paths.py
}

write_ilk_sentinel() {
  : # TODO: step 7 — atomic write of last-exit.json (temp + mv)
}

invoke_local_checks() {
  : # TODO: step 7 — run run_local_checks.py per target with outer timeout
}

test_all_shipped() {
  : # TODO: step 5 — run loop_status.py, return 0 if all shipped
}

get_plans_dir() {
  : # TODO: step 3 — resolve active plans dir via ilk_paths.py or walk-up
}

get_subplan_slug() {
  : # TODO: helper — read plan: frontmatter
}

get_subplan_repo_name() {
  : # TODO: helper — read repo: frontmatter
}

get_meta_info() {
  : # TODO: step 3 — cached meta-project lookup via ilk_paths.py
}

resolve_subplan_repo_dir() {
  : # TODO: helper — map sub-plan repo: to absolute member path
}

get_subplan_ci_timeout() {
  : # TODO: helper — read ci_timeout_minutes frontmatter
}

find_shipped_subplans_pending_gates() {
  : # TODO: step 6+ — scan plans dir for shipped plans without ship-reports
}

invoke_quality_gates_for_subplan() {
  : # TODO: step 6+ — wait_ci + reviewer + ship_report pipeline
}

invoke_quality_gates_if_needed() {
  : # TODO: step 6+ — gate orchestration after productive iterations
}

write_jsonl_record() {
  : # TODO: step 6 — append compact JSON to JSONL log
}

invoke_claude_iteration() {
  : # TODO: step 5 — gtimeout claude -p ... with stream-json tee to renderer
}

# ----- Startup banner --------------------------------------------------------

print_banner() {
  : # TODO: step 3 — print project/repos/max-iters/timeout/model/budget/MCP config
}

# ----- Main ------------------------------------------------------------------

main() {
  parse_args "$@"
  preflight
  discover_git_repos
  print_banner

  # Sentinel setup (state=running)
  # TODO: step 7

  # Initial check: already shipped?
  # TODO: step 5

  # Main loop: for i in 1..MAX_ITERATIONS
  #   - snapshot HEADs
  #   - invoke_claude_iteration
  #   - snapshot HEADs again, diff
  #   - stall detection (3 consecutive zero-commit iters)
  #   - optional local_checks
  #   - write JSONL record
  #   - optional quality gates
  #   - check stop conditions
  # TODO: steps 5-6

  # Final report
  # TODO: step 6

  # Sentinel teardown (state=<stop_reason>)
  # TODO: step 7
}

main "$@"
