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

# ----- Skill root resolution -------------------------------------------------

source "$(dirname "${BASH_SOURCE[0]}")/_ilk_skill_root.sh"
_SKILL_ROOT="$(ilk_skill_root)"

# ----- Defaults & globals ----------------------------------------------------

# Populated by argument parsing in main().
PROJECT_PATH=""
MAX_ITERATIONS=30
ITERATION_TIMEOUT_MIN=30
LOOP_STATUS_SCRIPT="${_SKILL_ROOT}/ilk-loop/scripts/loop_status.py"
LOG_DIR="${_SKILL_ROOT}/ilk-loop/logs"
PROMPT="/ilk please continue the active plan"
MAX_BUDGET_USD=0
MODEL=""
RUN_LOCAL_CHECKS=false
LOCAL_CHECKS_TIMEOUT_SEC=180
LOCAL_CHECKS_SCRIPT="${_SKILL_ROOT}/ilk-loop/scripts/run_local_checks.py"
MCP_CONFIG_PATH=""

# Internal state
RUN_ID=""
RUN_LOG_DIR=""
JSONL_LOG=""
SETTINGS_HAS_ENV=0
PROJECT_KEY=""
REPOS=()
ITER_COMPLETED=0
ITER_EXIT_CODE=0
ITER_BUDGET_EXHAUSTED=0

# ----- Argument parsing ------------------------------------------------------

usage() {
  cat <<'EOF'
Usage: run_ilk_loop_claude.sh [OPTIONS]

Run the ilk-loop autonomously using Claude Code (`claude`) as the agent CLI,
until all sub-plans ship, max iterations hit, or progress stalls.

Options:
  --project-path PATH              Project root containing docs/plans/MASTER-*.md
                                   and one or more git repos. (required)
  --max-iterations N               Hard cap on iterations. Default: 30
  --iteration-timeout-min N        Per-iteration wall-clock timeout, in minutes.
                                   Default: 30
  --loop-status-script PATH        Path to loop_status.py.
                                   Default: <skill-root>/ilk-loop/scripts/loop_status.py
  --log-dir PATH                   Where to write per-iteration logs and the JSONL summary.
                                   Default: <skill-root>/ilk-loop/logs
  --prompt TEXT                    The prompt sent to claude.
                                   Default: "/ilk please continue the active plan"
  --max-budget-usd N               Optional per-iteration --max-budget-usd cap.
                                   Default: 0 (no cap)
  --model MODEL                    Optional --model override.
                                   Default: "" (claude reads ANTHROPIC_MODEL from env)
  --run-local-checks               After each productive iteration, scan new commit
                                   messages for [plan:<slug>#step-N] tags and run
                                   the matching sub-plan's local_checks.
  --local-checks-timeout-sec N     Outer wall-clock cap for local_checks per iteration.
                                   Default: 180
  --local-checks-script PATH       Path to run_local_checks.py.
                                   Default: <skill-root>/ilk-loop/scripts/run_local_checks.py
  --mcp-config-path PATH           Path to a JSON file with {"mcpServers": {...}} to
                                   pass to every `claude -p` invocation via
                                   --mcp-config and --strict-mcp-config.
  -h, --help                       Show this help message and exit.

Examples:
  # Smoke test on a project (subscription, no $$ cap)
  bash run_ilk_loop_claude.sh --project-path /path/to/your/project --max-iterations 1

  # Overnight on subscription endpoint
  bash run_ilk_loop_claude.sh --project-path /path/to/your/project \
      --max-iterations 30 --iteration-timeout-min 30

  # Metered endpoint with $5 hard stop per iter
  bash run_ilk_loop_claude.sh --project-path /path/to/your/project \
      --max-iterations 30 --max-budget-usd 5
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --project-path)
        PROJECT_PATH="$2"
        shift 2
        ;;
      --max-iterations)
        MAX_ITERATIONS="$2"
        shift 2
        ;;
      --iteration-timeout-min)
        ITERATION_TIMEOUT_MIN="$2"
        shift 2
        ;;
      --loop-status-script)
        LOOP_STATUS_SCRIPT="$2"
        shift 2
        ;;
      --log-dir)
        LOG_DIR="$2"
        shift 2
        ;;
      --prompt)
        PROMPT="$2"
        shift 2
        ;;
      --max-budget-usd)
        MAX_BUDGET_USD="$2"
        shift 2
        ;;
      --model)
        MODEL="$2"
        shift 2
        ;;
      --run-local-checks)
        RUN_LOCAL_CHECKS=true
        shift
        ;;
      --local-checks-timeout-sec)
        LOCAL_CHECKS_TIMEOUT_SEC="$2"
        shift 2
        ;;
      --local-checks-script)
        LOCAL_CHECKS_SCRIPT="$2"
        shift 2
        ;;
      --mcp-config-path)
        MCP_CONFIG_PATH="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done

  if [[ -z "$PROJECT_PATH" ]]; then
    echo "Error: --project-path is required." >&2
    usage >&2
    exit 1
  fi
}

# ----- Pre-flight checks -----------------------------------------------------

preflight() {
  # gtimeout is required for per-iteration wall-clock caps on macOS
  if ! command -v gtimeout >/dev/null 2>&1; then
    echo "Error: gtimeout not found. Install with: brew install coreutils  # provides gtimeout" >&2
    exit 1
  fi

  if ! command -v claude >/dev/null 2>&1; then
    echo "Error: Claude Code 'claude' not on PATH." >&2
    exit 1
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 not on PATH (needed by loop_status.py)." >&2
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "Error: git not on PATH." >&2
    exit 1
  fi

  if [[ ! -d "$PROJECT_PATH" ]]; then
    echo "Error: Project path does not exist: $PROJECT_PATH" >&2
    exit 1
  fi
  # Resolve to absolute path so downstream git -C calls are unambiguous
  PROJECT_PATH="$(cd "$PROJECT_PATH" && pwd)"

  if [[ ! -f "$LOOP_STATUS_SCRIPT" ]]; then
    echo "Error: loop_status.py not found at: $LOOP_STATUS_SCRIPT" >&2
    exit 1
  fi

  # Settings.json env detection — same "non-empty env block => authoritative"
  # semantics as the post-dc24c67 Win version. Empty {} does NOT count.
  local settings_json="${HOME}/.claude/settings.json"
  SETTINGS_HAS_ENV=0
  if [[ -f "$settings_json" ]]; then
    if jq -e '.env | type == "object" and length > 0' "$settings_json" >/dev/null 2>&1; then
      SETTINGS_HAS_ENV=1
      echo "Detected ~/.claude/settings.json env block -- it will be the sole auth source."
    fi
  fi

  if [[ "$SETTINGS_HAS_ENV" -eq 0 ]]; then
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      echo "Warning: ANTHROPIC_API_KEY not set. claude will fall back to interactive auth." >&2
    fi
  fi

  mkdir -p "$LOG_DIR"
  RUN_ID="$(date +%Y%m%d-%H%M%S)"
  RUN_LOG_DIR="${LOG_DIR}/ilk-claude-${RUN_ID}"
  mkdir -p "$RUN_LOG_DIR"
  JSONL_LOG="${LOG_DIR}/.ilk-loop.log"
}

# ----- Helpers ---------------------------------------------------------------

discover_git_repos() {
  local resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  local json
  json="$(python3 "$resolver" --start "$PROJECT_PATH" 2>/dev/null)" || {
    echo "Error: ilk_paths.py failed to resolve project." >&2
    exit 1
  }

  PROJECT_KEY="$(echo "$json" | jq -r '.project_key // empty')"
  local kind
  kind="$(echo "$json" | jq -r '.project_kind // "single"')"

  if [[ "$kind" == "meta" ]]; then
    local count
    count="$(echo "$json" | jq '.meta_members | length')"
    if [[ "$count" -eq 0 ]]; then
      echo "Error: meta project resolved but no member repos found." >&2
      exit 1
    fi
    local i
    for ((i = 0; i < count; i++)); do
      local member_path
      member_path="$(echo "$json" | jq -r ".meta_members[$i].path")"
      REPOS+=("$member_path")
    done
  else
    local root
    root="$(echo "$json" | jq -r '.project_root // empty')"
    if [[ -z "$root" ]]; then
      echo "Error: Could not resolve project root from ilk_paths.py." >&2
      exit 1
    fi
    REPOS=("$root")
  fi

  if [[ ${#REPOS[@]} -eq 0 ]]; then
    echo "Error: No git repos found at or under $PROJECT_PATH" >&2
    exit 1
  fi
}

get_repo_heads() {
  local out_file="$1"
  : > "$out_file"
  local r
  for r in "${REPOS[@]}"; do
    local sha
    sha=$(git -C "$r" rev-parse HEAD 2>/dev/null) || sha="(unknown)"
    printf '%s=%s\n' "$r" "$sha" >> "$out_file"
  done
}

get_new_commit_count() {
  local repo="$1"
  local before_file="$2"
  local after_file="$3"
  local before after
  before=$(grep -F "$repo=" "$before_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
  after=$(grep -F "$repo=" "$after_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
  if [[ "$before" == "$after" || "$before" == "(unknown)" || "$after" == "(unknown)" ]]; then
    echo 0
    return
  fi
  git -C "$repo" rev-list --count "${before}..${after}" 2>/dev/null || echo 0
}

get_local_check_targets() {
  local repo="$1"
  local before="$2"
  local after="$3"

  if [[ "$before" == "$after" || "$before" == "(unknown)" || "$after" == "(unknown)" ]]; then
    return
  fi

  local msgs
  msgs=$(git -C "$repo" log "${before}..${after}" --pretty=format:"%s%n%b" 2>/dev/null) || return
  [[ -z "$msgs" ]] && return

  # Extract [plan:<slug>#step-<N>] tags and keep max step per slug
  echo "$msgs" | grep -oE '\[plan:[^#]+#step-[0-9]+\]' | \
    sed -E 's/\[plan:([^#]+)#step-([0-9]+)\]/\1 \2/' | \
    sort -t' ' -k1,1 -k2,2nr | \
    awk '!seen[$1]++ {print $1, $2}'
}

get_ilk_runtime_dir() {
  local resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then
    return 1
  fi
  local json
  json="$(python3 "$resolver" --start "$PROJECT_PATH" 2>/dev/null)" || return 1
  echo "$json" | jq -r '.external_runtime_dir // empty'
}

write_ilk_sentinel() {
  local dir="$1"
  local data="$2"
  local target="${dir}/last-exit.json"
  local tmp="${target}.tmp"

  mkdir -p "$dir" 2>/dev/null || {
    echo "  ! sentinel write failed: cannot create directory $dir" >&2
    return
  }

  printf '%s' "$data" > "$tmp" && mv -f "$tmp" "$target" || {
    echo "  ! sentinel write failed: cannot write $target" >&2
    rm -f "$tmp" 2>/dev/null
    return
  }
}

invoke_local_checks() {
  local project="$1"
  local targets_file="$2"
  local helper_script="$3"
  local outer_timeout_sec="${4:-180}"
  local results_file="$5"

  : > "$results_file"

  if [[ ! -f "$targets_file" || ! -s "$targets_file" ]]; then
    return
  fi
  if [[ ! -f "$helper_script" ]]; then
    return
  fi

  local deadline
  deadline=$(($(date +%s) + outer_timeout_sec))

  local slug step
  while read -r slug step; do
    local now
    now=$(date +%s)
    if [[ "$now" -ge "$deadline" ]]; then
      echo "{\"slug\":\"$slug\",\"step\":$step,\"outcome\":\"skipped\",\"error\":\"outer timeout reached\"}" >> "$results_file"
      continue
    fi

    local remain_sec=$((deadline - now))
    if [[ "$remain_sec" -lt 5 ]]; then
      remain_sec=5
    fi

    local tmp_out
    tmp_out=$(mktemp)

    local check_exit=0
    gtimeout "${remain_sec}s" python3 "$helper_script" --project "$project" --slug "$slug" --step "$step" > "$tmp_out" 2>&1 || check_exit=$?

    local outcome
    case "$check_exit" in
      0) outcome="pass" ;;
      1) outcome="fail" ;;
      124) outcome="error" ;;
      *) outcome="error" ;;
    esac

    local tag
    case "$outcome" in
      pass) tag="OK" ;;
      fail) tag="FAIL" ;;
      *) tag="ERR" ;;
    esac
    echo "  [local_checks $tag] $slug step $step -> $outcome"

    echo "{\"slug\":\"$slug\",\"step\":$step,\"outcome\":\"$outcome\",\"exit_code\":$check_exit}" >> "$results_file"

    rm -f "$tmp_out"
  done < "$targets_file"
}

test_all_shipped() {
  (cd "$PROJECT_PATH" && python3 "$LOOP_STATUS_SCRIPT" >/dev/null 2>&1)
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
  python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)))" <<< "$1" >> "$JSONL_LOG"
}

invoke_claude_iteration() {
  local cwd="$1"
  local iter_log="$2"
  local prompt_text="$3"
  local timeout_sec="$4"
  local budget_usd="${5:-0}"
  local model_override="${6:-}"

  local jsonl_log="${iter_log}.jsonl"
  local renderer="${_SKILL_ROOT}/ilk-loop/scripts/_stream_json_render.py"

  # Build claude args array
  local claude_args=(
    "-p"
    "--dangerously-skip-permissions"
    "--output-format" "stream-json"
    "--verbose"
    "--include-partial-messages"
  )

  if [[ "$budget_usd" -gt 0 ]]; then
    claude_args+=("--max-budget-usd" "$budget_usd")
  fi
  if [[ -n "$model_override" ]]; then
    claude_args+=("--model" "$model_override")
  fi
  if [[ -n "$MCP_CONFIG_PATH" ]]; then
    claude_args+=("--mcp-config" "$MCP_CONFIG_PATH" "--strict-mcp-config")
  fi

  claude_args+=("$prompt_text")

  # Run claude with optional env clear and gtimeout.
  # The subshell (cd ...) keeps the cwd change local.
  local exit_code=0
  if [[ "$SETTINGS_HAS_ENV" -eq 1 ]]; then
    (cd "$cwd" && env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL -u ANTHROPIC_MODEL \
      gtimeout "${timeout_sec}s" claude "${claude_args[@]}") \
      | tee "$jsonl_log" | python3 "$renderer" | tee "$iter_log" \
      || exit_code=$?
  else
    (cd "$cwd" && gtimeout "${timeout_sec}s" claude "${claude_args[@]}") \
      | tee "$jsonl_log" | python3 "$renderer" | tee "$iter_log" \
      || exit_code=$?
  fi

  # Detect budget-exhausted via the terminal result's terminal_reason field only.
  # Phrase-based patterns ("budget exhausted") match agent thinking/output that
  # describes budget concepts — only terminal_reason is the authoritative signal.
  local budget_exhausted=0
  if [[ -f "$jsonl_log" ]] \
     && grep -qE '"terminal_reason"\s*:\s*"budget_exhausted"' "$jsonl_log" 2>/dev/null; then
    budget_exhausted=1
  fi

  # gtimeout returns 124 on timeout
  local completed=1
  if [[ "$exit_code" -eq 124 ]]; then
    completed=0
    exit_code=-1
  fi

  ITER_COMPLETED=$completed
  ITER_EXIT_CODE=$exit_code
  ITER_BUDGET_EXHAUSTED=$budget_exhausted
}

# ----- Startup banner --------------------------------------------------------

print_banner() {
  echo ""
  echo "=== ilk-loop runner (Claude Code) ==="
  echo "Project:        $PROJECT_PATH"
  echo "Repos found:    ${#REPOS[@]}"
  local r
  for r in "${REPOS[@]}"; do
    echo "  - $r"
  done
  echo "Max iterations: $MAX_ITERATIONS"
  echo "Iter timeout:   $ITERATION_TIMEOUT_MIN min"
  if [[ -n "$MODEL" ]]; then
    echo "Model:          $MODEL"
  else
    echo "Model:          ${ANTHROPIC_MODEL:-} (from env)"
  fi
  echo "API base:       ${ANTHROPIC_BASE_URL:-}"
  if [[ "$MAX_BUDGET_USD" -gt 0 ]]; then
    echo "Per-iter budget: \$${MAX_BUDGET_USD}"
  else
    echo "Per-iter budget: unlimited"
  fi
  if [[ -n "$MCP_CONFIG_PATH" ]]; then
    echo "MCP config:     $MCP_CONFIG_PATH (strict -- worker sees only what's listed)"
  else
    echo "MCP config:     (default -- worker sees user's full MCP registry)"
  fi
  echo "Run logs:       $RUN_LOG_DIR"
  echo "JSONL summary:  $JSONL_LOG"
  echo ""
}

# ----- Main ------------------------------------------------------------------

main() {
  parse_args "$@"
  preflight
  discover_git_repos
  print_banner

  # Sentinel setup (state=running)
  local runtime_dir
  runtime_dir=$(get_ilk_runtime_dir) || runtime_dir=""
  local loop_started_at
  loop_started_at=$(date +%Y-%m-%dT%H:%M:%S%z)
  local iter_counter=0

  if [[ -n "$runtime_dir" ]]; then
    mkdir -p "$runtime_dir"
    python3 -c "import json; print(json.dumps({
      'state': 'running',
      'pid': $$,
      'run_id': '$RUN_ID',
      'started_at': '$loop_started_at',
      'project_path': '$PROJECT_PATH',
      'cli': 'claude'
    }))" > "${runtime_dir}/last-exit.json.tmp" && mv -f "${runtime_dir}/last-exit.json.tmp" "${runtime_dir}/last-exit.json"
    echo "Sentinel: ${runtime_dir}/last-exit.json (state=running)"
  else
    echo "Sentinel: skipped (no runtime dir resolved)"
  fi

  # Initial check: already shipped?
  if test_all_shipped; then
    echo "All sub-plans already shipped. Nothing to do."
    local ts
    ts=$(date +%Y-%m-%dT%H:%M:%S%z)
    write_jsonl_record "{\"run_id\":\"$RUN_ID\",\"cli\":\"claude\",\"iteration\":0,\"timestamp\":\"$ts\",\"project\":\"$PROJECT_PATH\",\"stop_reason\":\"already-shipped\"}"
    return 0
  fi

  # Main loop
  local i
  local no_progress_streak=0
  local stop_reason=""

  for ((i = 1; i <= MAX_ITERATIONS; i++)); do
    iter_counter=$i
    echo ""
    echo "--- Iteration $i / $MAX_ITERATIONS ---"

    local iter_start
    iter_start=$(date +%s)

    local heads_before_file heads_after_file
    heads_before_file="${RUN_LOG_DIR}/heads-before-${i}.tmp"
    heads_after_file="${RUN_LOG_DIR}/heads-after-${i}.tmp"

    get_repo_heads "$heads_before_file"

    local iter_log
    iter_log="${RUN_LOG_DIR}/iter-$(printf '%02d' $i).log"

    local timeout_sec
    timeout_sec=$((ITERATION_TIMEOUT_MIN * 60))

    invoke_claude_iteration "$PROJECT_PATH" "$iter_log" "$PROMPT" "$timeout_sec" "$MAX_BUDGET_USD" "$MODEL"

    local iter_end iter_dur_sec
    iter_end=$(date +%s)
    iter_dur_sec=$((iter_end - iter_start))

    get_repo_heads "$heads_after_file"

    # Compute new commits per repo
    local total_new=0
    local new_commits_file
    new_commits_file=$(mktemp)
    local r
    for r in "${REPOS[@]}"; do
      local count
      count=$(get_new_commit_count "$r" "$heads_before_file" "$heads_after_file")
      total_new=$((total_new + count))
      if [[ "$count" -gt 0 ]]; then
        echo "$r $count" >> "$new_commits_file"
      fi
    done

    echo ""
    echo "  duration: ${iter_dur_sec}s  exit: $ITER_EXIT_CODE  new commits: $total_new"
    if [[ -s "$new_commits_file" ]]; then
      while read -r repo_line count_line; do
        echo "    $repo_line : +$count_line"
      done < "$new_commits_file"
    fi

    # Stall detection
    local iter_stop_reason=""
    if [[ "$ITER_COMPLETED" -eq 0 ]]; then
      iter_stop_reason="timeout"
    elif [[ "$ITER_BUDGET_EXHAUSTED" -eq 1 ]]; then
      iter_stop_reason="budget-exhausted"
    elif [[ "$total_new" -eq 0 ]]; then
      no_progress_streak=$((no_progress_streak + 1))
      if [[ "$no_progress_streak" -ge 3 ]]; then
        iter_stop_reason="no-progress"
      elif [[ "$ITER_EXIT_CODE" -ne 0 ]]; then
        echo "  ! agent exited $ITER_EXIT_CODE (likely transient upstream API error). Streak: $no_progress_streak/3. Continuing." >&2
      fi
    else
      no_progress_streak=0
    fi

    # Optional local_checks
    local local_checks_results=""
    if [[ "$RUN_LOCAL_CHECKS" == true && "$total_new" -gt 0 ]]; then
      local all_targets_file
      all_targets_file=$(mktemp)
      for r in "${REPOS[@]}"; do
        local before after
        before=$(grep -F "$r=" "$heads_before_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
        after=$(grep -F "$r=" "$heads_after_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
        get_local_check_targets "$r" "$before" "$after" >> "$all_targets_file"
      done
      # Merge by slug (max step wins)
      local merged_targets_file
      merged_targets_file=$(mktemp)
      if [[ -s "$all_targets_file" ]]; then
        sort -t' ' -k1,1 -k2,2nr "$all_targets_file" | awk '!seen[$1]++ {print $1, $2}' > "$merged_targets_file"
        local_checks_results=$(mktemp)
        invoke_local_checks "$PROJECT_PATH" "$merged_targets_file" "$LOCAL_CHECKS_SCRIPT" "$LOCAL_CHECKS_TIMEOUT_SEC" "$local_checks_results"
      fi
      rm -f "$all_targets_file" "$merged_targets_file"
    fi

    # Build new_commits JSON
    local new_commits_json="{}"
    if [[ -s "$new_commits_file" ]]; then
      new_commits_json=$(python3 -c "
import json, sys
d = {}
for line in sys.stdin:
  parts = line.strip().rsplit(' ', 1)
  if len(parts) == 2:
    d[parts[0]] = int(parts[1])
print(json.dumps(d))
" < "$new_commits_file")
    fi
    rm -f "$new_commits_file"

    # Build local_checks JSON
    local local_checks_json="[]"
    if [[ -n "$local_checks_results" && -s "$local_checks_results" ]]; then
      local_checks_json=$(python3 -c "
import json, sys
print(json.dumps([json.loads(l) for l in sys.stdin]))
" < "$local_checks_results")
      rm -f "$local_checks_results"
    fi

    # Write JSONL record via Python to avoid bash JSON escaping issues
    local ts
    ts=$(date +%Y-%m-%dT%H:%M:%S%z)

    PROJECT_PATH="$PROJECT_PATH" \
    MODEL="$MODEL" \
    MAX_BUDGET_USD="$MAX_BUDGET_USD" \
    RUN_ID="$RUN_ID" \
    _ITER="$i" \
    _TS="$ts" \
    _DUR="$iter_dur_sec" \
    _EXIT="$ITER_EXIT_CODE" \
    _NEW_TOTAL="$total_new" \
    _STOP_REASON="$iter_stop_reason" \
    _NEW_COMMITS_JSON="$new_commits_json" \
    _LOCAL_CHECKS_JSON="$local_checks_json" \
    python3 -c "
import json, os
d = {
  'run_id': os.environ['RUN_ID'],
  'cli': 'claude',
  'iteration': int(os.environ['_ITER']),
  'timestamp': os.environ['_TS'],
  'project': os.environ['PROJECT_PATH'],
  'model': os.environ.get('MODEL', '') or os.environ.get('ANTHROPIC_MODEL', ''),
  'base_url': os.environ.get('ANTHROPIC_BASE_URL', ''),
  'max_budget_usd': float(os.environ.get('MAX_BUDGET_USD', 0)),
  'duration_sec': int(os.environ['_DUR']),
  'exit_code': int(os.environ['_EXIT']),
  'new_commits_total': int(os.environ['_NEW_TOTAL']),
}
nc = os.environ.get('_NEW_COMMITS_JSON', '')
if nc and nc != '{}':
  d['new_commits'] = json.loads(nc)
sr = os.environ.get('_STOP_REASON', '')
if sr:
  d['stop_reason'] = sr
lc = os.environ.get('_LOCAL_CHECKS_JSON', '')
if lc and lc != '[]':
  d['local_checks'] = json.loads(lc)
print(json.dumps(d))
" >> "$JSONL_LOG"

    # Quality gates
    # TODO: step 6+ (invoke_quality_gates_if_needed)

    if [[ -n "$iter_stop_reason" ]]; then
      stop_reason="$iter_stop_reason"
      break
    fi

    if test_all_shipped; then
      stop_reason="all-shipped"
      break
    fi
  done

  if [[ -z "$stop_reason" ]]; then
    stop_reason="max-iterations"
  fi

  # Final report
  echo ""
  echo "=== Loop ended: $stop_reason ==="
  echo "Run logs: $RUN_LOG_DIR"
  echo "JSONL:    $JSONL_LOG"
  echo ""
  echo "Final loop_status:"
  python3 "$LOOP_STATUS_SCRIPT" 2>&1 || true

  # Sentinel teardown (state=<stop_reason>)
  if [[ -n "$runtime_dir" ]]; then
    local ended_at
    ended_at=$(date +%Y-%m-%dT%H:%M:%S%z)
    python3 -c "import json; print(json.dumps({
      'state': '$stop_reason',
      'pid': $$,
      'run_id': '$RUN_ID',
      'started_at': '$loop_started_at',
      'ended_at': '$ended_at',
      'iterations': $iter_counter,
      'project_path': '$PROJECT_PATH',
      'cli': 'claude',
      'jsonl_log': '$JSONL_LOG'
    }))" > "${runtime_dir}/last-exit.json.tmp" && mv -f "${runtime_dir}/last-exit.json.tmp" "${runtime_dir}/last-exit.json"
    echo "Sentinel: ${runtime_dir}/last-exit.json (state=$stop_reason, iters=$iter_counter)"
  fi
}

main "$@"
