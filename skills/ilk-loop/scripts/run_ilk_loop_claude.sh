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
LOG_DIR=""
JSONL_LOG_PATH=""
PROMPT="/ilk please continue the active plan"
MAX_BUDGET_USD=0
MODEL=""
RUN_LOCAL_CHECKS=false
LOCAL_CHECKS_TIMEOUT_SEC=180
LOCAL_CHECKS_SCRIPT="${_SKILL_ROOT}/ilk-loop/scripts/run_local_checks.py"
MCP_CONFIG_PATH=""

# Branch config (parsed from MASTER frontmatter branch: block)
BRANCH_CREATE_FROM=""
BRANCH_NAME=""
BRANCH_MERGE_BACK=false

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
  --log-dir PATH                   Per-run artifact directory (iter logs, heads files).
                                   Default: ~/.ilk-data/projects/<key>/logs/runs/<run-id>
  --jsonl-log PATH                 Path to the stable project-level JSONL summary file.
                                   Default: ~/.ilk-data/projects/<key>/logs/.ilk-loop.log
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
      --jsonl-log)
        JSONL_LOG_PATH="$2"
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
  local cfg_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  local settings_json="${cfg_dir}/settings.json"
  echo "[runner] CLAUDE_CONFIG_DIR=$cfg_dir"
  SETTINGS_HAS_ENV=0
  if [[ -f "$settings_json" ]]; then
    if jq -e '.env | type == "object" and length > 0' "$settings_json" >/dev/null 2>&1; then
      SETTINGS_HAS_ENV=1
      echo "Detected ${settings_json} env block -- it will be the sole auth source."
    fi
  fi

  if [[ "$SETTINGS_HAS_ENV" -eq 0 ]]; then
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      echo "Warning: ANTHROPIC_API_KEY not set. claude will fall back to interactive auth." >&2
    fi
  fi

  RUN_ID="$(date +%Y%m%d-%H%M%S)"

  # Resolve external log paths via ilk_paths.py unless explicitly provided
  local legacy_log_dir="${_SKILL_ROOT}/ilk-loop/logs"
  if [[ -z "$LOG_DIR" || -z "$JSONL_LOG_PATH" ]]; then
    local resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
    local ext_logs=""
    if [[ -f "$resolver" ]]; then
      ext_logs=$(python3 -c "
import sys; sys.path.insert(0, '$(dirname "$resolver")')
from ilk_paths import find_project_root, project_key, external_logs_dir
from pathlib import Path
root, _ = find_project_root(Path('$PROJECT_PATH'))
if root: print(external_logs_dir(project_key(root)))
" 2>/dev/null) || ext_logs=""
    fi
    if [[ -z "$LOG_DIR" ]]; then
      if [[ -n "$ext_logs" ]]; then
        LOG_DIR="${ext_logs}/runs/${RUN_ID}"
      else
        LOG_DIR="${legacy_log_dir}/runs/${RUN_ID}"
      fi
    fi
    if [[ -z "$JSONL_LOG_PATH" ]]; then
      if [[ -n "$ext_logs" ]]; then
        JSONL_LOG_PATH="${ext_logs}/.ilk-loop.log"
      else
        JSONL_LOG_PATH="${legacy_log_dir}/.ilk-loop.log"
      fi
    fi
  fi

  mkdir -p "$LOG_DIR"
  RUN_LOG_DIR="$LOG_DIR"
  JSONL_LOG="$JSONL_LOG_PATH"
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

parse_master_branch_block() {
  # Parse the branch: block from the active MASTER plan's YAML frontmatter.
  # Sets BRANCH_CREATE_FROM, BRANCH_NAME, BRANCH_MERGE_BACK.
  # No-op (all stay empty/default) when no branch: block exists.
  local resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then
    return 0
  fi

  local plans_dir
  plans_dir=$(python3 "$resolver" --start "$PROJECT_PATH" 2>/dev/null | jq -r '.external_plans_dir // empty') || plans_dir=""
  if [[ -z "$plans_dir" || ! -d "$plans_dir" ]]; then
    return 0
  fi

  # Resolve the ACTIVE master the same way the loop does — via loop_status.py
  # (registry/queue order), NOT filesystem-enumeration order. `find | head -n1`
  # returns an arbitrary master and diverges from the executing one whenever a
  # project has >1 master, so branch setup would read a stale, possibly-shipped
  # master's branch block (see ilk-runner-branch-setup-bugs handoff, bug #1).
  local master_file status_json master_name status_plans_dir
  # NOTE: --json exits 1 when there is pending work (and 2 on error) — both
  # still print valid JSON to stdout, so keep the captured output and let the
  # jq guards below decide. `|| true` only prevents set -e from tripping.
  status_json=$(cd "$PROJECT_PATH" && python3 "$LOOP_STATUS_SCRIPT" --json 2>/dev/null) || true
  master_name=$(echo "$status_json" | jq -r '.master // empty' 2>/dev/null)
  status_plans_dir=$(echo "$status_json" | jq -r '.plans_dir // empty' 2>/dev/null)
  # Prefer loop_status.py's plans_dir so master + dir come from one source.
  if [[ -n "$status_plans_dir" && -d "$status_plans_dir" ]]; then
    plans_dir="$status_plans_dir"
  fi
  if [[ -z "$master_name" ]]; then
    return 0
  fi
  master_file="$plans_dir/$master_name"
  if [[ ! -f "$master_file" ]]; then
    return 0
  fi

  # Parse branch: block via the standalone parser script (single source of
  # truth shared with the ps1 runner; avoids inline-heredoc quote/encoding
  # fragility). See skills/ilk-loop/scripts/parse_branch_block.py.
  local parsed branch_script
  branch_script="$(dirname "${BASH_SOURCE[0]}")/parse_branch_block.py"
  parsed=$(python3 "$branch_script" "$master_file") || parsed="{}"

  if [[ "$parsed" == "{}" || -z "$parsed" ]]; then
    return 0
  fi

  BRANCH_CREATE_FROM=$(echo "$parsed" | jq -r '.create_from // empty')
  BRANCH_NAME=$(echo "$parsed" | jq -r '.name // empty')
  local merge_raw
  merge_raw=$(echo "$parsed" | jq -r '.merge_back // empty')
  if [[ -n "$merge_raw" && "$merge_raw" == "true" ]]; then
    BRANCH_MERGE_BACK=true
  else
    BRANCH_MERGE_BACK=false
  fi

  # Default create_from to HEAD if branch block exists but create_from is missing
  if [[ -n "$BRANCH_NAME" && -z "$BRANCH_CREATE_FROM" ]]; then
    BRANCH_CREATE_FROM="HEAD"
  fi

  if [[ -n "$BRANCH_NAME" ]]; then
    echo "[runner] branch block parsed: create_from=$BRANCH_CREATE_FROM name=$BRANCH_NAME merge_back=$BRANCH_MERGE_BACK"
  fi
}

# ----- Remote classification (Gap 5) ------------------------------------------
#
# classify_remote REMOTE
#
# Classifies a git remote as "shared" or "personal" based on its URL.
# Used to decide whether commit trailers ([plan:…#step-N]) should be stripped.
#
# Heuristic:
#   - Personal: remote URL contains a personal namespace pattern
#     (e.g. inluck-net/*, github.com/inluck-net/*, gitee.com/inluck-net/*)
#   - Shared: everything else (organization repos, team repos, public repos)
#   - Default: "shared" when unsure (safer to strip trailers on shared repos)
#
# Globals read: none
# Globals modified: none
# Prints: "shared" or "personal"
# Returns: 0 always

classify_remote() {
  local remote="$1"
  local repo="${REPOS[0]}"

  if [[ -z "$remote" || -z "$repo" ]]; then
    echo "shared"
    return 0
  fi

  # Get the remote URL
  local url
  url=$(git -C "$repo" remote get-url "$remote" 2>/dev/null) || url=""
  if [[ -z "$url" ]]; then
    echo "shared"
    return 0
  fi

  # Personal namespace patterns (case-insensitive match)
  # Matches: inluck-net/* on any host (github.com, gitee.com, gitlab.com, etc.)
  # Also matches SSH-style: git@github.com:inluck-net/*
  local lower_url
  lower_url=$(echo "$url" | tr '[:upper:]' '[:lower:]')

  # Check for personal namespace pattern: host/username or host:username
  # Pattern: (github.com|gitee.com|gitlab.com)[/:]inluck-net/
  if [[ "$lower_url" =~ (github\.com|gitee\.com|gitlab\.com)[/:]inluck-net/ ]]; then
    echo "personal"
    return 0
  fi

  # Check for generic personal pattern: any host with /inluck-net/ in path
  if [[ "$lower_url" =~ /inluck-net/ ]]; then
    echo "personal"
    return 0
  fi

  # Default to shared (safer: strip trailers)
  echo "shared"
  return 0
}

# ----- Branch setup (Gap 2) ---------------------------------------------------
#
# setup_branch
#
# If the MASTER has a branch: block, this function:
#   1. Parses create_from into remote/branch components
#   2. Fetches the base ref from the remote
#   3. Runs the freshness preflight (ensure_fresh_base_ref)
#   4. Guards against dirty working tree
#   5. Checks out -B <name> <create_from>
#
# No-op when BRANCH_NAME is empty (no branch: block in MASTER).
# Globals read: BRANCH_NAME, BRANCH_CREATE_FROM, PROJECT_PATH, REPOS
# Globals modified: none
# Returns: 0 on success, 1 on error

# Create/checkout the policy branch in ONE repo. Returns:
#   0 = branched, 2 = skip (non-fatal: not a git repo / dirty / base ref missing),
#   1 = hard fail (merge/rebase in progress, fetch/checkout failed).
_setup_branch_one_repo() {
  local repo="$1"
  local git_dir remote branch candidate

  git_dir="$(git -C "$repo" rev-parse --git-dir 2>/dev/null)" || {
    echo "  ! $repo is not a git repo — skipping branch setup there" >&2
    return 2
  }
  if [[ -d "$git_dir/MERGE_HEAD" ]]; then
    echo "Error: a merge is in progress in $repo (abort/commit before running)." >&2
    return 1
  fi
  if [[ -d "$git_dir/rebase-merge" || -d "$git_dir/rebase-apply" ]]; then
    echo "Error: a rebase is in progress in $repo (abort/finish before running)." >&2
    return 1
  fi
  # Dirty tree -> skip (non-fatal): only clean repos host the branch. A single
  # dirty repo yields branched==0 -> setup_branch fails (preserves the guard).
  if ! git -C "$repo" diff --quiet 2>/dev/null || \
     ! git -C "$repo" diff --cached --quiet 2>/dev/null; then
    echo "  ! working tree dirty in $repo — skipping branch setup there" >&2
    return 2
  fi

  # Parse create_from into remote/branch (per-repo: depends on configured remotes).
  if [[ "$BRANCH_CREATE_FROM" == */* ]]; then
    candidate="${BRANCH_CREATE_FROM%%/*}"
    if git -C "$repo" remote | grep -qx "$candidate"; then
      remote="$candidate"; branch="${BRANCH_CREATE_FROM#*/}"
    else
      remote=""; branch="$BRANCH_CREATE_FROM"
    fi
  else
    remote=""; branch="$BRANCH_CREATE_FROM"
  fi

  if [[ -n "$remote" ]]; then
    echo "[runner] fetching ${remote} ${branch} in $repo..."
    git -C "$repo" fetch "$remote" "$branch" >/dev/null 2>&1 || {
      echo "Error: git fetch ${remote} ${branch} failed in $repo." >&2
      return 1
    }
    ensure_fresh_base_ref "$remote" "$branch" || {
      echo "Error: base-ref freshness check failed in $repo." >&2
      return 1
    }
  fi

  # Base ref must resolve in THIS repo; if not, this repo isn't a target -> skip.
  if ! git -C "$repo" rev-parse "$BRANCH_CREATE_FROM" >/dev/null 2>&1; then
    echo "  ! base ref '$BRANCH_CREATE_FROM' not found in $repo — skipping" >&2
    return 2
  fi

  git -C "$repo" checkout -B "$BRANCH_NAME" "$BRANCH_CREATE_FROM" >/dev/null 2>&1 || {
    echo "Error: git checkout -B $BRANCH_NAME failed in $repo." >&2
    return 1
  }
  echo "[runner] $repo now on branch: $(git -C "$repo" branch --show-current 2>/dev/null)"
  return 0
}

setup_branch() {
  if [[ -z "$BRANCH_NAME" ]]; then
    return 0
  fi
  if [[ ${#REPOS[@]} -eq 0 || -z "${REPOS[0]}" ]]; then
    echo "Error: no git repo resolved for branch setup." >&2
    return 1
  fi
  if [[ -z "${BRANCH_CREATE_FROM// /}" ]]; then
    echo "Error: create_from is empty — cannot branch off nothing." >&2
    return 1
  fi

  echo ""
  echo "[runner] === Branch setup ==="
  echo "[runner] target: checkout -B $BRANCH_NAME from $BRANCH_CREATE_FROM across ${#REPOS[@]} repo(s)"

  local branched=0 rc
  for repo in "${REPOS[@]}"; do
    [[ -z "$repo" ]] && continue
    _setup_branch_one_repo "$repo"; rc=$?
    if [[ $rc -eq 1 ]]; then return 1; fi
    if [[ $rc -eq 0 ]]; then branched=$((branched + 1)); fi
  done

  if [[ $branched -eq 0 ]]; then
    echo "Error: could not create '$BRANCH_NAME' in any repo (base ref missing or all dirty)." >&2
    return 1
  fi
  echo "[runner] branched $branched repo(s)."
  echo "[runner] === Branch setup complete ==="
  echo ""
  return 0
}

# ----- Base-ref freshness (Gap 3) ---------------------------------------------
#
# ensure_fresh_base_ref REMOTE BRANCH
#
# Compares the local remote-tracking ref (<remote>/<branch>) against the true
# remote tip via `git ls-remote`. On mismatch, force-refreshes the local ref.
# Aborts with a clear message on failure.
#
# Globals read: PROJECT_PATH (first REPOS entry used as the working tree)
# Globals modified: none
# Returns: 0 on success (local ref is now fresh), 1 on unrecoverable error

ensure_fresh_base_ref() {
  local remote="$1"
  local branch="$2"

  local repo="${REPOS[0]}"
  if [[ -z "$repo" ]]; then
    echo "Error: no git repo resolved for base-ref freshness check." >&2
    return 1
  fi

  echo "[runner] freshness preflight: ${remote}/${branch}"

  # 1. Get the local remote-tracking ref SHA
  local local_sha
  local_sha=$(git -C "$repo" rev-parse "refs/remotes/${remote}/${branch}" 2>/dev/null) || local_sha=""
  if [[ -z "$local_sha" ]]; then
    echo "[runner]   local ref refs/remotes/${remote}/${branch} not found (will fetch)"
    local_sha="(none)"
  else
    echo "[runner]   local  ${local_sha:0:12}"
  fi

  # 2. Get the true remote tip via ls-remote
  local ls_output
  ls_output=$(git -C "$repo" ls-remote "$remote" "refs/heads/${branch}" 2>/dev/null) || {
    echo "Error: git ls-remote ${remote} refs/heads/${branch} failed." >&2
    echo "       Check that the remote is reachable and the branch exists." >&2
    return 1
  }

  if [[ -z "$ls_output" ]]; then
    echo "Error: branch '${branch}' not found on remote '${remote}'." >&2
    echo "       ls-remote returned empty — the branch may not exist upstream." >&2
    return 1
  fi

  local remote_sha
  remote_sha=$(echo "$ls_output" | head -n1 | awk '{print $1}')
  echo "[runner]   remote ${remote_sha:0:12}"

  # 3. Compare — if they match, we're done
  if [[ "$local_sha" == "$remote_sha" ]]; then
    echo "[runner]   OK — local ref is up to date"
    return 0
  fi

  # 4. Mismatch — force-refresh
  echo "[runner]   STALE — local ${local_sha:0:12} != remote ${remote_sha:0:12}"
  echo "[runner]   force-refreshing ${remote}/${branch}..."

  git -C "$repo" fetch "$remote" "${branch}:refs/remotes/${remote}/${branch}" 2>&1 || {
    echo "Error: force-refresh fetch failed for ${remote}/${branch}." >&2
    echo "       The remote may have changed again, or the ref is locked." >&2
    return 1
  }

  # Verify the refresh took effect
  local refreshed_sha
  refreshed_sha=$(git -C "$repo" rev-parse "refs/remotes/${remote}/${branch}" 2>/dev/null) || refreshed_sha=""
  if [[ "$refreshed_sha" == "$remote_sha" ]]; then
    echo "[runner]   refreshed OK — now at ${refreshed_sha:0:12}"
    return 0
  else
    echo "Error: after fetch, local ref is ${refreshed_sha:0:12} but expected ${remote_sha:0:12}." >&2
    echo "       Possible race or refspec mismatch." >&2
    return 1
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
  if [[ -n "$BRANCH_NAME" ]]; then
    echo "Branch policy:  checkout -B $BRANCH_NAME from $BRANCH_CREATE_FROM (merge_back=$BRANCH_MERGE_BACK)"
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
  parse_master_branch_block
  print_banner
  setup_branch || exit 1

  # Determine remote type for commit trailer policy
  # Write to .ilk-remote-type so the agent knows whether to include trailers
  local remote_type="shared"  # default
  local remote_for_branch=""
  if [[ -n "$BRANCH_NAME" ]]; then
    # Branch was just set up; get its upstream remote
    remote_for_branch=$(git -C "${REPOS[0]}" config --get "branch.${BRANCH_NAME}.remote" 2>/dev/null) || remote_for_branch=""
  fi
  if [[ -n "$remote_for_branch" ]]; then
    remote_type=$(classify_remote "$remote_for_branch")
  else
    # No branch block or no upstream; check origin as fallback
    remote_type=$(classify_remote "origin")
  fi
  echo "[runner] remote type: $remote_type (remote: ${remote_for_branch:-origin})"

  # Write remote type to file for agent consumption
  echo "$remote_type" > "${PROJECT_PATH}/.ilk-remote-type"

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

      # B2 enforcement: a gate that ERRORED or FAILED must BLOCK. Do NOT break
      # here — set iter_stop_reason and let the post-record check below (after
      # the JSONL write) break. Otherwise a gate-stopped run is never written
      # to .ilk-loop.log and collect.py / ilk-feedback go blind (falling back
      # to a stale run -> the misclassification cascade).
      if [[ -s "$local_checks_results" ]]; then
        if grep -qE '"outcome":"(error|fail)"' "$local_checks_results"; then
          iter_stop_reason="local_checks_failed"
          echo "Loop stopped: local_checks not passing (B2 enforcement)" >&2
        fi
      fi
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

    # Remove the launcher's running.pid so the scheduler does not see a
    # stale sentinel and log skip-busy forever.  Best-effort + idempotent.
    rm -f "${runtime_dir}/launcher/running.pid"
  fi
}

# Dot-source guard: when ILK_DOTSOURCE_ONLY=1, functions are defined but
# main() does not execute. Lets tests source this script to call internal
# functions without starting the iteration loop.
if [[ "${ILK_DOTSOURCE_ONLY:-}" != "1" ]]; then
  main "$@"
fi
