#!/usr/bin/env bash
set -Eeuo pipefail

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

# Steer-hook functions (invoke_steer_hook) — operator interjections + pause gate.
source "$(dirname "${BASH_SOURCE[0]}")/steer_hook.sh"

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

# ERR-trap context: set by record_err_context on any set -e exit; included
# in finalize_sentinel's stopped_reason so interrupted sentinels self-identify.
_LAST_ERR_CONTEXT=""
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

  # Resolve the project's ship.suite.path_prelude ONCE and apply it to the
  # AGENT's environment, not only to gate commands.
  #
  # run_local_checks.py:515 prepends this prelude to every `local_checks`
  # command, so a correctly-configured project's GATES resolve their toolchain
  # while the agent's own shell does not: the driver exports no PATH, and a
  # non-login subprocess inherits `getconf PATH` (/usr/bin:/bin:/usr/sbin:/sbin).
  # Measured on kira-cloudflare 2026-09-15: `bunx` lives at ~/.bun/bin and the
  # project's .ilk-launch.json path_prelude already named it, yet the agent's
  # `git commit` could not run the pre-commit hook, guessed /opt/homebrew/bin,
  # failed, and reached for `--no-verify` instead. A missing tool must be an
  # environment fault to report, never a gate to skip.
  #
  # Deliberately NOT a hardcoded PATH: node here resolves through
  # fnm_multishells/<pid>/bin, which is per-shell and ephemeral, so a literal
  # copy of an interactive PATH would rot. The project's own config is the
  # single source of truth, and an unconfigured project gets today's behaviour.
  PATH_PRELUDE="$(python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
try:
    from pathlib import Path
    from run_local_checks import _read_path_prelude
    sys.stdout.write(_read_path_prelude(Path(sys.argv[2])) or "")
except Exception:
    pass
' "$(dirname "${BASH_SOURCE[0]}")" "$PROJECT_PATH" 2>/dev/null || true)"
  if [[ -n "$PATH_PRELUDE" ]]; then
    echo "[ilk] path_prelude applied to the agent environment: $PATH_PRELUDE"
  fi

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

  # Adopt the run id from an explicitly supplied --log-dir when its basename is
  # already one (runs/<YYYYmmdd-HHMMSS>).  The launcher generates a run id of
  # its own for that directory (launch.sh: run_id="$(date +%Y%m%d-%H%M%S)"), so
  # generating a second one here made the terminal record disagree with the
  # directory holding that run's logs whenever the two `date` calls straddled a
  # second boundary — observed as run_id 20260803-171605 against
  # logs/runs/20260803-171604, which forces fuzzy timestamp matching to
  # correlate a run to its own logs.
  RUN_ID=""
  if [[ -n "$LOG_DIR" ]]; then
    local log_dir_base
    log_dir_base="$(basename "$LOG_DIR")"
    if [[ "$log_dir_base" =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
      RUN_ID="$log_dir_base"
    fi
  fi
  if [[ -z "$RUN_ID" ]]; then
    RUN_ID="$(date +%Y%m%d-%H%M%S)"
  fi

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

# ----- Selfmod isolation (DP-2) ----------------------------------------------

# Resolve the toolkit clone from _SKILL_ROOT.
# Two layouts exist:
#   1. Direct install (tests, dev): _SKILL_ROOT lives inside the real repo
#      (e.g. .../ilk-skills/skills/).  git -C finds the root immediately.
#   2. Symlink farm (production): _SKILL_ROOT is ~/.claude-worker/skills/ with
#      symlinks into the real repo.  Follow one child symlink to the repo.
_resolve_toolkit_clone() {
  local skill_root_resolved
  skill_root_resolved="$(readlink -f "$_SKILL_ROOT" 2>/dev/null \
                    || realpath "$_SKILL_ROOT" 2>/dev/null \
                    || echo "$_SKILL_ROOT")"
  # Case 1: _SKILL_ROOT (or its resolved path) is inside the git repo.
  local git_root
  git_root="$(git -C "$skill_root_resolved" rev-parse --show-toplevel 2>/dev/null)" && {
    echo "$git_root"
    return 0
  }
  # Case 2: symlink farm — follow a child symlink into the real repo.
  local candidate
  for candidate in "$_SKILL_ROOT"/*; do
    [[ -L "$candidate" ]] || continue
    local real_target
    real_target="$(readlink -f "$candidate" 2>/dev/null \
                || realpath "$candidate" 2>/dev/null)" || continue
    git_root="$(git -C "$real_target" rev-parse --show-toplevel 2>/dev/null)" || continue
    echo "$git_root"
    return 0
  done
  # Last resort — will cause the comparison to fail (correct behaviour:
  # the toolkit clone is genuinely unresolvable).
  echo "$skill_root_resolved"
}

# Returns 0 (true) when PROJECT_PATH resolves to the toolkit clone — the
# clone that the installed skill symlinks point at.  Compares resolved
# paths (via readlink -f / realpath + git rev-parse), never bare strings.
#
# Emits one line on stdout when isolation is required, naming the resolved
# clone.  A silent behaviour change to where the loop executes is not
# acceptable.
selfmod_isolation_required() {
  local project_resolved toolkit_resolved

  project_resolved="$(readlink -f "$PROJECT_PATH" 2>/dev/null \
                    || realpath "$PROJECT_PATH" 2>/dev/null \
                    || echo "$PROJECT_PATH")"
  toolkit_resolved="$(_resolve_toolkit_clone)"

  if [[ "$project_resolved" == "$toolkit_resolved" ]]; then
    echo "[selfmod] isolation required: project=$project_resolved matches toolkit clone"
    return 0
  fi
  return 1
}

# Create the selfmod worktree and switch the iteration's working directory
# into it.  Resolves the worktree path from the external runtime dir via
# ilk_paths.py (never inside the project tree).  Honours
# $SELFMOD_WORKTREE_PATH override for testing.
#
# On creation failure, exits non-zero — silently continuing in the clone
# is the hazard this function exists to prevent.
create_selfmod_worktree() {
  local wt_path="${SELFMOD_WORKTREE_PATH:-}"
  if [[ -z "$wt_path" ]]; then
    local runtime_dir
    runtime_dir="$(get_ilk_runtime_dir)" || {
      echo "[selfmod] ERROR: cannot resolve runtime dir — aborting." >&2
      exit 1
    }
    wt_path="${runtime_dir}/worktrees/selfmod-batch"
  fi

  local toolkit_clone
  toolkit_clone="$(_resolve_toolkit_clone)"

  local sw_script="${_SKILL_ROOT}/ilk-loop/scripts/selfmod_worktree.py"
  if [[ ! -f "$sw_script" ]]; then
    echo "[selfmod] ERROR: selfmod_worktree.py not found at $sw_script" >&2
    exit 1
  fi

  python3 "$sw_script" create "$toolkit_clone" "$wt_path" || {
    echo "[selfmod] ERROR: worktree creation failed at $wt_path — aborting." >&2
    exit 1
  }

  PROJECT_PATH="$wt_path"
  echo "[selfmod] working directory switched to worktree: $wt_path"
}

# Merge the selfmod worktree back into the clone.
# Called after converge_ship_transition when SELFMOD_ISOLATED=1.
# On success: removes the worktree, restores PROJECT_PATH.
# On failure: leaves the worktree intact and reports the reason.
# Exit codes (from selfmod_worktree.py merge CLI):
#   0 = merged, 2 = live loop blocked, 3 = branch moved,
#   4 = broken probe (fail-closed), 5 = lock contention
merge_selfmod_worktree() {
  local wt_path="${SELFMOD_WORKTREE_PATH:-}"
  local orig_path="${SELFMOD_ORIGINAL_PROJECT_PATH:-}"
  local lock_path="${SELFMOD_MERGE_LOCK_PATH:-}"

  if [[ -z "$wt_path" || -z "$orig_path" ]]; then
    echo "[selfmod] ERROR: merge called but worktree/original path not set" >&2
    return 1
  fi

  local sw_script="${_SKILL_ROOT}/ilk-loop/scripts/selfmod_worktree.py"
  if [[ ! -f "$sw_script" ]]; then
    echo "[selfmod] ERROR: selfmod_worktree.py not found at $sw_script" >&2
    return 1
  fi

  local cmd=(python3 "$sw_script" merge "$orig_path" "$wt_path")
  if [[ -n "$lock_path" ]]; then
    cmd+=(--lock "$lock_path")
  fi

  local merge_output merge_rc=0
  merge_output=$("${cmd[@]}" 2>&1) || merge_rc=$?

  case $merge_rc in
    0)
      echo "[selfmod] merge complete: $merge_output"
      # Restore PROJECT_PATH to the original clone.
      PROJECT_PATH="$orig_path"
      # Remove the worktree — all commits are now in the clone.
      python3 "$sw_script" remove "$orig_path" "$wt_path" 2>/dev/null || true
      ;;
    2)
      echo "[selfmod] MERGE BLOCKED: live loop(s) detected." >&2
      echo "$merge_output" >&2
      echo "[selfmod] worktree left at $wt_path (holds unmerged work)" >&2
      ;;
    3)
      echo "[selfmod] MERGE REFUSED: clone HEAD moved since worktree creation." >&2
      echo "$merge_output" >&2
      echo "[selfmod] worktree left at $wt_path (holds unmerged work)" >&2
      ;;
    4)
      echo "[selfmod] MERGE BLOCKED: liveness probe broken (fail-closed)." >&2
      echo "$merge_output" >&2
      echo "[selfmod] worktree left at $wt_path (holds unmerged work)" >&2
      ;;
    5)
      echo "[selfmod] MERGE FAILED: lock contention." >&2
      echo "$merge_output" >&2
      echo "[selfmod] worktree left at $wt_path (holds unmerged work)" >&2
      ;;
    *)
      echo "[selfmod] MERGE FAILED: unexpected exit code $merge_rc." >&2
      echo "$merge_output" >&2
      echo "[selfmod] worktree left at $wt_path (holds unmerged work)" >&2
      ;;
  esac

  return $merge_rc
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
  # Dirty tree guard: defer until we know whether a branch switch is needed.
  # If the repo is already on $BRANCH_NAME and it's ahead of base, a dirty tree
  # is fine (resume-with-dirty-tree, AC-4). We only block dirty trees when a
  # branch *switch* or *create* is required.
  local _tree_dirty=0
  if ! git -C "$repo" diff --quiet 2>/dev/null || \
     ! git -C "$repo" diff --cached --quiet 2>/dev/null; then
    _tree_dirty=1
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
    # Use explicit refspec so the tracking ref updates even when
    # remote.origin.fetch is narrowed (e.g. main-only).  Without this,
    # `git fetch origin <branch>` only writes FETCH_HEAD and leaves
    # refs/remotes/origin/<branch> stale → freshness check aborts.
    git -C "$repo" fetch "$remote" "+refs/heads/$branch:refs/remotes/$remote/$branch" >/dev/null 2>&1 || {
      echo "Error: git fetch ${remote} refs/heads/${branch} failed in $repo." >&2
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

  # Three-way branch logic (SP4 — reuse existing branch ahead of base):
  #   1. Branch exists AND base is its ancestor → reuse (no reset).
  #   2. Branch exists but base is NOT ancestor (diverged) → abort, no loss.
  #   3. Branch absent → create from base (existing behavior).
  local _branch_exists=0
  if git -C "$repo" rev-parse "$BRANCH_NAME" >/dev/null 2>&1; then
    _branch_exists=1
  fi

  local _current_branch
  _current_branch=$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null) || _current_branch=""

  if [[ "$_branch_exists" -eq 1 ]]; then
    # Branch exists — check if base is an ancestor.
    if git -C "$repo" merge-base --is-ancestor "$BRANCH_CREATE_FROM" "$BRANCH_NAME" 2>/dev/null; then
      # Branch is at-or-ahead of base → reuse it (AC-1: preserve prior-run commits).
      # If already on the branch, even a dirty tree is fine (AC-4: resume-with-dirty).
      if [[ "$_current_branch" != "$BRANCH_NAME" ]]; then
        # Need to switch branches — auto-stash dirty tree to unblock.
        if [[ "$_tree_dirty" -eq 1 ]]; then
          echo "  ! working tree dirty in $repo — auto-stashed dirty tree (branch switch to $BRANCH_NAME)" >&2
          git -C "$repo" stash push -u -m "ilk auto-stash (branch setup)" >/dev/null 2>&1
        fi
        if ! git -C "$repo" checkout "$BRANCH_NAME" >/dev/null 2>&1; then
          echo "Error: git checkout $BRANCH_NAME failed in $repo." >&2
          return 1
        fi
      fi
      local _ahead_count
      _ahead_count=$(git -C "$repo" rev-list --count "$BRANCH_CREATE_FROM".."$BRANCH_NAME" 2>/dev/null) || _ahead_count="?"
      echo "[runner] reusing existing branch $BRANCH_NAME (ahead of $BRANCH_CREATE_FROM by $_ahead_count commits)"
    else
      # Branch exists but diverged — reuse it (no reset, would lose work).
      # Base reconciliation deferred to PR/merge time.
      if [[ "$_current_branch" != "$BRANCH_NAME" ]]; then
        # Need to switch branches — auto-stash dirty tree to unblock.
        if [[ "$_tree_dirty" -eq 1 ]]; then
          echo "  ! working tree dirty in $repo — auto-stashed dirty tree (branch switch to $BRANCH_NAME)" >&2
          git -C "$repo" stash push -u -m "ilk auto-stash (branch setup)" >/dev/null 2>&1
        fi
        if ! git -C "$repo" checkout "$BRANCH_NAME" >/dev/null 2>&1; then
          echo "Error: git checkout $BRANCH_NAME failed in $repo." >&2
          return 1
        fi
      fi
      local _ahead_count _behind_count
      _ahead_count=$(git -C "$repo" rev-list --count "$BRANCH_CREATE_FROM".."$BRANCH_NAME" 2>/dev/null) || _ahead_count="?"
      _behind_count=$(git -C "$repo" rev-list --count "$BRANCH_NAME".."$BRANCH_CREATE_FROM" 2>/dev/null) || _behind_count="?"
      echo "WARNING: reusing diverged branch $BRANCH_NAME in $repo (ahead $_ahead_count / behind $_behind_count of $BRANCH_CREATE_FROM). Base reconciliation deferred to PR/merge." >&2
    fi
  else
    # Branch absent — create from base. Auto-stash dirty tree to unblock.
    if [[ "$_tree_dirty" -eq 1 ]]; then
      echo "  ! working tree dirty in $repo — auto-stashed dirty tree (branch create $BRANCH_NAME)" >&2
      git -C "$repo" stash push -u -m "ilk auto-stash (branch setup)" >/dev/null 2>&1
    fi
    if ! git -C "$repo" checkout -B "$BRANCH_NAME" "$BRANCH_CREATE_FROM" >/dev/null 2>&1; then
      # Non-zero exit may be a benign post-checkout hook failure (lefthook/husky).
      # Verify the actual outcome before aborting: if HEAD is on the target branch
      # AND HEAD SHA equals the resolved base SHA, the checkout landed despite the
      # hook noise — warn and continue.
      local _actual_branch _actual_sha _target_sha
      _actual_branch=$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null) || _actual_branch=""
      _actual_sha=$(git -C "$repo" rev-parse HEAD 2>/dev/null) || _actual_sha=""
      _target_sha=$(git -C "$repo" rev-parse "$BRANCH_CREATE_FROM" 2>/dev/null) || _target_sha=""

      if [[ "$_actual_branch" == "$BRANCH_NAME" && -n "$_actual_sha" && "$_actual_sha" == "$_target_sha" ]]; then
        echo "WARNING: git checkout -B $BRANCH_NAME exited non-zero in $repo, but checkout landed despite post-checkout hook failure (HEAD is on $BRANCH_NAME at ${_actual_sha:0:12}). Continuing." >&2
      else
        echo "Error: git checkout -B $BRANCH_NAME failed in $repo (HEAD=$_actual_branch at ${_actual_sha:0:12}, expected branch=$BRANCH_NAME at ${_target_sha:0:12})." >&2
        return 1
      fi
    fi
  fi
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

# Resolve the path whose git history describes THIS iteration's work.
#
# While a selfmod batch runs isolated, the iteration commits into the worktree.
# The clone's HEAD does not move until merge_selfmod_worktree runs, which is
# ~290 lines AFTER the gate block -- so a clone-based read sees `before ==
# after`, finds no trailers, and the gate falls through to PRE_ITER_TARGET.
#
# Measured 2026-09-17, run 20260917-115455: the fallback resolved
# `an-isolated-batch-lands-or-reports 4` on a sub-plan whose steps are 0..3,
# that check errored, and ship_integrity reverted a sub-plan whose work was
# complete and merged back cleanly seconds later.
#
# The KEY stays the repo path so heads-before/heads-after lookups still match;
# only the path read from changes.
selfmod_effective_repo() {
  local r="$1"
  if [[ "${SELFMOD_ISOLATED:-0}" -eq 1 \
        && -n "${SELFMOD_WORKTREE_PATH:-}" \
        && "$r" == "${SELFMOD_ORIGINAL_PROJECT_PATH:-}" \
        && -d "${SELFMOD_WORKTREE_PATH}" ]]; then
    printf '%s\n' "$SELFMOD_WORKTREE_PATH"
    return
  fi
  printf '%s\n' "$r"
}

get_repo_heads() {
  local out_file="$1"
  : > "$out_file"
  local r
  for r in "${REPOS[@]}"; do
    local sha src
    src="$(selfmod_effective_repo "$r")"
    sha=$(git -C "$src" rev-parse HEAD 2>/dev/null) || sha="(unknown)"
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

head_before_sha() {
  local repo="$1" before_file="$2" sha
  sha=$(grep -F "$repo=" "$before_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
  if [[ "$sha" == "(unknown)" ]]; then
    sha=""
  fi
  echo "$sha"
}

head_after_sha() {
  local repo="$1" after_file="$2" sha
  sha=$(grep -F "$repo=" "$after_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
  if [[ "$sha" == "(unknown)" ]]; then
    sha=""
  fi
  echo "$sha"
}

get_active_subplan_targets() {
  # Emit "<slug> <step>" for the sub-plan the loop is currently executing.
  #
  # Fallback for gate discovery when commit trailers are unavailable. Trailer
  # scanning (get_local_check_targets) is the primary source, but it CANNOT work
  # on a shared remote: the trailer policy deliberately strips
  # [plan:<slug>#step-N] from commit messages there (classify_remote →
  # .ilk-remote-type), so on every shared-remote project a declared
  # local_checks gate silently never ran while the sub-plan still shipped as
  # `verification_tier: loop-verified`. Two features that each pass their own
  # tests, mutually exclusive when combined.
  #
  # Uses loop_status.py — the same source branch setup uses to resolve the
  # active master — so the gate targets the sub-plan the loop is actually
  # working, not an arbitrary one.
  local status_json line slug step
  status_json=$(cd "$PROJECT_PATH" && python3 "$LOOP_STATUS_SCRIPT" --json 2>/dev/null) || true
  [[ -n "$status_json" ]] || return 0
  # --json exits non-zero when work is pending; the payload is still valid.
  line=$(echo "$status_json" | jq -r '
    [ (.subplans // [])[] | select(((.status // "") | ascii_downcase) != "shipped") ][0]
    | if . == null then empty else "\(.slug)\t\(.current_step)" end
  ' 2>/dev/null) || return 0
  [[ -n "$line" ]] || return 0
  slug="${line%%$'\t'*}"
  step="${line#*$'\t'}"
  # current_step can be "?" when the front-matter omits it.
  [[ "$step" =~ ^[0-9]+$ ]] || step=0
  [[ -n "$slug" && "$slug" != "null" ]] || return 0
  printf '%s %s\n' "$slug" "$step"
}

get_all_subplan_steps() {
  # Emit "<slug> <step>" for EVERY not-yet-shipped sub-plan, read BEFORE the
  # iteration.  This is the ledger writer's baseline, and only its.
  #
  # get_active_subplan_targets above emits exactly ONE line -- `[0]` of the
  # non-shipped sub-plans -- so PRE_ITER_TARGET is a single-slug list by
  # construction.  write_ship_proof_records keys its entire slug universe off
  # that list, so an iteration could never write more than one ledger row no
  # matter how many sub-plans it advanced.  A sub-plan the agent advanced INTO
  # got no row, and its commits were swept into the row for whichever slug was
  # active at the start.  Measured on gh-resolve #4829: one iteration, one row
  # claiming all three commits, no row for the batch-verification slug -- which
  # is exactly the slug the consumer's reap refused with `ship-proof-missing`.
  # The inversion is the point: the FASTER run was the less publishable one,
  # and permanently so, because the iteration that would have written the
  # second row is over.  Runs that took two iterations wrote two rows and
  # published fine.
  #
  # The gate's fallback target deliberately stays PRE_ITER_TARGET: widening
  # THAT would point the gate at sub-plans the iteration never touched.
  local status_json
  # --json exits non-zero when work is pending (loop_status.py:786); the
  # payload is still valid, so the exit code cannot be the predicate here.
  # stderr stays attached: a baseline that never answered is not a baseline of
  # zero sub-plans, and the difference is a missing row nobody would see.
  status_json=$(cd "$PROJECT_PATH" && python3 "$LOOP_STATUS_SCRIPT" --json) || true
  if [[ -z "$status_json" ]]; then
    echo "  ! [ship-proof] loop_status --json gave no pre-iteration baseline -- a sub-plan advanced INTO this iteration will get no ledger row." >&2
    return 0
  fi
  echo "$status_json" | jq -r '
    (.subplans // [])[]
    | select(((.status // "") | ascii_downcase) != "shipped")
    | select(.slug != null and .slug != "null")
    | "\(.slug) \(.current_step)"
  ' 2>/dev/null || true
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

get_ledger_check_targets() {
  # Emit "<slug> <max_step>" from the ship-proof ledger for a given iteration.
  #
  # On a shared remote, trailer scanning returns nothing and the pre-iteration
  # capture (PRE_ITER_TARGET) gives the step the iteration STARTED on — not the
  # step it REACHED.  The ledger records the actual step_to, so this function
  # resolves the highest step the iteration committed for each slug.
  #
  # $1 = run_id   $2 = iteration
  local run_id="$1" iteration="$2"

  local ledger_dir
  # Do NOT silence stderr: get_ilk_runtime_dir reports a missing resolver or a
  # failed resolve there, and a swallowed probe failure is not data -- it reads
  # identically to "no ledger dir configured". AC-4 of
  # test_sentinel_path_agreement.py asserts this.
  ledger_dir=$(get_ilk_runtime_dir) || return 0
  [[ -n "$ledger_dir" ]] || return 0
  local ledger="${ledger_dir}/ship-proof.jsonl"
  [[ -f "$ledger" ]] || return 0

  python3 -c "
import json, sys
run_id, iteration = sys.argv[1], int(sys.argv[2])
by_slug = {}
for line in open(sys.argv[3], encoding='utf-8-sig'):
    line = line.strip()
    if not line:
        continue
    try:
        rec = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        continue
    if not isinstance(rec, dict):
        continue
    if rec.get('run_id') != run_id or rec.get('iteration') != iteration:
        continue
    slug = rec.get('slug')
    try:
        step_to = int(rec['step_to'])
    except (KeyError, TypeError, ValueError):
        continue
    if slug and slug != 'null':
        by_slug[slug] = max(by_slug.get(slug, 0), step_to - 1)
for slug, step in sorted(by_slug.items()):
    print(f'{slug} {step}')
" "$run_id" "$iteration" "$ledger" 2>/dev/null || true
}

write_ship_proof_records() {
  # Write one ship-proof ledger record per worked sub-plan for this iteration.
  #
  # The ledger is a JSONL sidecar at <external_launcher_dir>/ship-proof.jsonl.
  # Each record attributes a range of commits [step_from, step_to) to a slug,
  # so ship_audit can prove steps even when commit trailers are absent (the
  # shared-remote case).  See detached-component-contracts.md.
  #
  # $1 = heads_before_file   $2 = heads_after_file   $3 = iteration
  local heads_before="$1" heads_after="$2" iteration="$3"

  # Parse PRE_ITER_TARGET: one "<slug> <step>" per line.  Use parallel
  # arrays (slug_list / step_list) because macOS bash 3.2 lacks declare -A.
  local slug_list=() step_list=()
  local line
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    local s="${line%% *}"
    local st="${line#* }"
    [[ -n "$s" && "$s" != "null" ]] || continue
    [[ "$st" =~ ^[0-9]+$ ]] || st=0
    slug_list+=("$s")
    step_list+=("$st")
  done <<< "${PRE_ITER_TARGET:-}"

  # Add every OTHER sub-plan that existed before the iteration, so one the
  # agent advanced INTO can still get a row.  PRE_ITER_TARGET names a single
  # slug by construction (see get_all_subplan_steps), so without this the
  # universe is capped at one row per iteration.
  #
  # An added slug's step_from is its REAL pre-iteration step, and that is not a
  # stylistic preference -- do not simplify it to 0.  ship_audit's
  # check_step_commits (ship_audit.py:176-178) counts a step as committed if
  # any record covers it in [step_from, step_to), so a floor of 0 would mark
  # every earlier step of a sub-plan the loop had partially worked and returned
  # to as proven.  That exact shape was measured on batch 2026-09-08c: a record
  # claiming step_from 0, step_to 4 attributed step 2 for a sub-plan with no
  # step-2 commit, and missing_steps came back [] for work never done
  # (ship_audit.py:234-240).  The consumer's reap reads presence only, so
  # nothing downstream of it would catch an invented floor.
  #
  # These carry a STRICTER emission rule than the PRE_ITER_TARGET slug, tracked
  # in the parallel `advanced_only` array: the loop TARGETED that slug, so its
  # row stands on the targeting alone; these stand only on having actually
  # advanced.  Without the distinction, every untouched sub-plan would get a
  # row claiming this iteration's commits -- attribution to a slug nothing
  # worked, which is the forgery the ledger exists to make impossible.
  local advanced_only=() i0 j dup s2 st2
  for (( i0=0; i0<${#slug_list[@]}; i0++ )); do
    advanced_only+=("0")
  done
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    s2="${line%% *}"
    st2="${line#* }"
    [[ -n "$s2" && "$s2" != "null" ]] || continue
    [[ "$st2" =~ ^[0-9]+$ ]] || st2=0
    dup=0
    for (( j=0; j<${#slug_list[@]}; j++ )); do
      if [[ "${slug_list[$j]}" == "$s2" ]]; then dup=1; break; fi
    done
    (( dup )) && continue
    slug_list+=("$s2")
    step_list+=("$st2")
    advanced_only+=("1")
  done <<< "${PRE_ITER_ALL_STEPS:-}"

  [[ ${#slug_list[@]} -gt 0 ]] || return 0

  # Resolve current_step for each slug from loop_status (the post-iteration
  # state — the agent may have advanced the sub-plan during this iteration).
  local status_json to_list=()
  # Do NOT swallow this probe.  `|| true` + `2>/dev/null` discarded both the
  # exit status and the reason, so a probe that never answered was
  # indistinguishable from one that answered "no advance": `to_val` kept its
  # pre-iteration value and the writer emitted a record asserting
  # step_to == step_from -- zero progress attributed on evidence that does not
  # exist.  On a shared remote that row is the ONLY evidence (the ledger union
  # at ship_audit.py:204 is gated on the slug having no trailers), so the false
  # negative is what an unattended publisher reads before refusing work that
  # shipped fine.  Same rule as the ledger_dir resolve below: a swallowed probe
  # failure is not data.  stderr is left attached so the reason is reported.
  #
  # The exit CODE cannot be the predicate here: loop_status returns 1 at
  # loop_status.py:786 to mean "a next sub-plan exists", which is the normal
  # productive case, 0 at :763 for all-shipped, and only >=2 (:616, :657) for
  # a genuine error.  Gating on `rc != 0` would suppress the ledger on every
  # productive iteration.  The honest predicate is whether the probe actually
  # ANSWERED -- i.e. whether stdout parses as JSON.
  local probe_rc=0
  # Anchor to the recorded pre-isolation root: under selfmod isolation
  # $PROJECT_PATH is the worktree, whose project key has no plans dir, so the
  # probe exits 2 on every productive iteration (measured 20260920-133041:
  # "probe did not answer (exit 2) -- writing no ledger rows"). Same recorded
  # root merge_selfmod_worktree restores from; :- fallback keeps non-selfmod
  # runs probing $PROJECT_PATH exactly as before.
  status_json=$(cd "${SELFMOD_ORIGINAL_PROJECT_PATH:-$PROJECT_PATH}" && python3 "$LOOP_STATUS_SCRIPT" --json) || probe_rc=$?
  if (( probe_rc >= 2 )) || [[ -z "$status_json" ]] \
     || ! printf '%s' "$status_json" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
    echo "  ! [ship-proof] loop_status --json probe did not answer (exit ${probe_rc}) -- writing no ledger rows for iteration ${iteration}. A row asserting zero progress would be a false negative, not a safe default." >&2
    return 0
  fi
  local si
  for (( si=0; si<${#slug_list[@]}; si++ )); do
    local s="${slug_list[$si]}"
    # Empty means "not resolved".  Deliberately NOT the pre-iteration step:
    # falling back to it is what manufactured the zero-progress row above.
    local to_val=""
    if [[ -n "$status_json" ]]; then
      local looked
      looked=$(echo "$status_json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for sp in (d.get('subplans') or []):
    if sp.get('slug') == sys.argv[1]:
        cs = sp.get('current_step')
        try:
            print(int(cs))
        except (TypeError, ValueError):
            pass
        break
" "$s" 2>/dev/null) || true
      if [[ -n "$looked" && "$looked" =~ ^[0-9]+$ ]]; then
        to_val="$looked"
      fi
    fi
    to_list+=("$to_val")
  done

  # Resolve the ledger path once.
  local ledger_dir
  # Do NOT silence stderr: get_ilk_runtime_dir reports a missing resolver or a
  # failed resolve there, and a swallowed probe failure is not data -- it reads
  # identically to "no ledger dir configured". AC-4 of
  # test_sentinel_path_agreement.py asserts this.
  ledger_dir=$(get_ilk_runtime_dir) || return 0
  [[ -n "$ledger_dir" ]] || return 0
  local ledger="${ledger_dir}/ship-proof.jsonl"
  mkdir -p "$ledger_dir" 2>/dev/null || return 0

  local r
  for r in "${REPOS[@]}"; do
    local before after
    before=$(grep -F "$r=" "$heads_before" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
    after=$(grep -F "$r=" "$heads_after" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
    [[ -n "$before" && -n "$after" && "$before" != "$after" ]] || continue

    local new_shas
    new_shas=$(git -C "$r" rev-list "${before}..${after}" 2>/dev/null) || continue
    [[ -n "$new_shas" ]] || continue

    local si
    for (( si=0; si<${#slug_list[@]}; si++ )); do
      local slug="${slug_list[$si]}"
      local step_from="${step_list[$si]}"
      local step_to="${to_list[$si]}"
      # An unresolved current_step is not zero progress -- it is no answer.
      # Attributing [step_from, step_from) would be a row claiming the
      # iteration advanced nothing, which on a trailerless remote is the only
      # thing the audit gets to read.  Skip and say so.
      if [[ -z "$step_to" ]]; then
        echo "  ! [ship-proof] no current_step resolved for '${slug}' -- skipping its row for iteration ${iteration} rather than attributing zero progress." >&2
        continue
      fi
      # A slug that was not this iteration's target earns a row only by having
      # advanced.  Unchanged current_step means the iteration did not work it,
      # and a row would attribute these commits to it anyway.  No log line:
      # on any real batch this is the quiet majority of sub-plans.
      if [[ "${advanced_only[$si]}" == "1" && "$step_to" -le "$step_from" ]]; then
        continue
      fi
      local shas_json
      shas_json=$(printf '%s\n' "$new_shas" | jq -R . | jq -sc .)

      local record
      record=$(python3 -c "
import json, sys
print(json.dumps({
    'run_id': sys.argv[1],
    'iteration': int(sys.argv[2]),
    'slug': sys.argv[3],
    'repo': sys.argv[4],
    'step_from': int(sys.argv[5]),
    'step_to': int(sys.argv[6]),
    'commits': json.loads(sys.argv[7]),
    # Who wrote this row.  The driver can only speak for itself: a
    # hand-executed batch writes no rows at all, so without this field an
    # empty ledger is ambiguous between 'nobody ran the loop' and 'the
    # writer is broken'.  Readers ignore unknown fields.
    'provenance': 'loop-executed',
}, separators=(',', ':')))" "$RUN_ID" "$iteration" "$slug" "$r" "$step_from" "$step_to" "$shas_json" 2>/dev/null) || continue

      # Terminate a neighbour's unterminated line before appending.
      #
      # `printf '%s\n'` ends OUR row but says nothing about the row already
      # there.  A foreign writer (a worker hand-appending) that omits its
      # trailing newline makes our append land on ITS line, and a line-based
      # reader then loses both.  Measured 2026-09-18, gh-resolve run 12cd8693:
      # 4 rows, 3 newlines, and the pair lost included the only loop-executed
      # row attributing that sub-plan.
      if [[ -s "$ledger" ]] && [[ -n "$(tail -c 1 "$ledger")" ]]; then
        printf '\n' >> "$ledger"
      fi
      printf '%s\n' "$record" >> "$ledger"
    done
  done
}

get_ilk_runtime_dir() {
  local resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then
    echo "get_ilk_runtime_dir: resolver not found at $resolver" >&2
    return 1
  fi
  local json
  json="$(python3 "$resolver" --start "$PROJECT_PATH")" || {
    echo "get_ilk_runtime_dir: resolver failed for $PROJECT_PATH" >&2
    return 1
  }
  echo "$json" | jq -r '.external_launcher_dir // empty'
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

record_err_context() {
  # Called by the ERR trap: captures the failing line number and command
  # so finalize_sentinel can include them in stopped_reason.
  _LAST_ERR_CONTEXT="line $1: $2"
}

finalize_sentinel() {
  # On EXIT (signal, error, or normal), if the sentinel is still state=running,
  # rewrite it to a terminal state so stale-running sentinels never survive.
  # Safe to call multiple times — idempotent (no-op when state != running).
  # `runtime_dir` is local to main(); when this EXIT trap fires after main
  # returns it is out of scope, so default-expand to stay safe under `set -u`
  # (otherwise the quick all-shipped exit path errors: "runtime_dir: unbound").
  [[ -z "${runtime_dir:-}" ]] && return 0
  local target="${runtime_dir}/last-exit.json"
  [[ -f "$target" ]] || return 0

  local cur_state
  cur_state=$(python3 -c "import json; print(json.load(open('$target')).get('state',''))" 2>/dev/null) || return 0
  [[ "$cur_state" == "running" ]] || return 0

  local ended_at
  ended_at=$(date +%Y-%m-%dT%H:%M:%S%z)
  local stopped_reason="runner exited without a terminal state"
  if [[ -n "${_LAST_ERR_CONTEXT:-}" ]]; then
    stopped_reason="runner exited without a terminal state (${_LAST_ERR_CONTEXT})"
  fi
  ILK_STOPPED_REASON="$stopped_reason" python3 -c "
import json, os
print(json.dumps({
    'state': 'interrupted',
    'pid': None,
    'run_id': '$RUN_ID',
    'started_at': json.load(open('$target')).get('started_at',''),
    'ended_at': '$ended_at',
    'project_path': '$PROJECT_PATH',
    'cli': 'claude',
    'stopped_reason': os.environ['ILK_STOPPED_REASON']
}))
" > "${target}.tmp" && mv -f "${target}.tmp" "$target" || true
  echo "[runner] finalize_sentinel: wrote terminal state (interrupted)" >&2
}

# Read declared per-check timeout(s) from a sub-plan step's local_checks.
# Mirrors the PowerShell Get-StepDeclaredTimeout; returns sum of timeout:
# values or 0 if not found.
get_step_declared_timeout() {
  local project="$1" slug="$2" step="$3"
  local resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ ! -f "$resolver" ]]; then echo 0; return; fi
  local plans_dir
  plans_dir=$(python3 "$resolver" --start "$project" 2>/dev/null | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('resolved_plans_dir') or '')" 2>/dev/null)
  if [[ -z "$plans_dir" || ! -d "$plans_dir" ]]; then echo 0; return; fi
  # Find sub-plan file by slug
  local f
  for f in "$plans_dir"/*.md; do
    [[ -f "$f" ]] || continue
    local plan_slug
    plan_slug=$(grep -m1 '^plan:' "$f" 2>/dev/null | sed 's/^plan:\s*//')
    if [[ "$plan_slug" == "$slug" ]]; then
      # Extract step heading → fenced yaml → timeout: values
      local sum
      sum=$(awk -v step="$step" '
        BEGIN { in_step=0; in_fence=0; in_lc=0; sum=0 }
        /^### Step / && $3 == step { in_step=1; next }
        /^### Step / && in_step { in_step=0 }
        in_step && /^```/ { in_fence=!in_fence; next }
        in_fence && /^local_checks:/ { in_lc=1; next }
        in_fence && in_lc && /^\s*( - )?timeout:\s*[0-9]+/ {
          match($0, /timeout:\s*([0-9]+)/, a); sum += a[1]
        }
        in_fence && in_lc && /^[^ ]/ && !/^\s/ { in_lc=0 }
        END { print sum+0 }
      ' "$f" 2>/dev/null)
      echo "$sum"
      return
    fi
  done
  echo 0
}

# ----- Gate-first fast path ---------------------------------------------------
#
# A step may declare `gate_first: true` beside its `local_checks` in the
# per-step yaml fence.  When the CURRENT step of the active sub-plan carries
# the marker, the driver runs that step's gate BEFORE dispatching an agent:
#
#   green -> an empty marker commit carrying [plan:<slug>#step-N],
#            current_step advances, and the model is never invoked.
#   red   -> fall through to the agent exactly as today.  The agent is needed
#            when the suite cannot start at all (typecheck/compile failure)
#            and when there are attributed failures to fix.
#
# Opt-in only: a step without the marker is untouched.  Motivated by the
# batch-verification step 0, whose gate IS the work
# (`verification_record.py --run-suite`) -- measured 2026-09-23 across both
# hosts at 89 iterations / 22.8h of agent wall-clock spent dispatching a
# model before the one command that does the work.
#
# NOTE: the inline python below lives inside single-quoted bash strings on
# purpose.  A dollar sign followed by a digit inside a double-quoted string
# under `set -u` expands to an unbound positional parameter and kills the
# whole command substitution -- see the warning in classify_loop_status.

# Resolve the plans dir the gate-first helpers read.  Anchors to the recorded
# pre-isolation root, same as invoke_local_checks: under selfmod isolation
# PROJECT_PATH is the worktree, whose own project key has no plans dir.
# Captured by callers -- stdout is the return value, nothing else.
_gate_first_plans_dir() {
  local plans_root="${SELFMOD_ORIGINAL_PROJECT_PATH:-$PROJECT_PATH}"
  local resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  [[ -f "$resolver" ]] || return 1
  local plans_dir
  plans_dir=$(python3 "$resolver" --start "$plans_root" 2>/dev/null | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('resolved_plans_dir') or '')" 2>/dev/null) || return 1
  [[ -n "$plans_dir" && -d "$plans_dir" ]] || return 1
  printf '%s\n' "$plans_dir"
}

# Locate a sub-plan file by its frontmatter `plan:` slug.  Echoes the path.
find_subplan_file_by_slug() {
  local plans_dir="$1" slug="$2"
  [[ -n "$plans_dir" && -d "$plans_dir" ]] || return 1
  local f plan_slug
  for f in "$plans_dir"/*.md; do
    [[ -f "$f" ]] || continue
    plan_slug=$(grep -m1 '^plan:' "$f" 2>/dev/null | sed 's/^plan:[[:space:]]*//')
    if [[ "$plan_slug" == "$slug" ]]; then
      printf '%s\n' "$f"
      return 0
    fi
  done
  return 1
}

# Does the step's fenced yaml declare `gate_first: true`?  Exit 0 = declared.
# Mirrors run_local_checks.extract_step_local_checks' heading + fence window
# (that parser reads `local_checks:` out of the same fence; this reads the
# `gate_first:` key beside it).  A marker in frontmatter is NOT read -- the
# unit is the step, and a step that opts in is a step whose gate is the work.
step_declares_gate_first() {
  local slug="$1" step="$2"
  local plans_dir sub_file
  plans_dir=$(_gate_first_plans_dir) || return 1
  sub_file=$(find_subplan_file_by_slug "$plans_dir" "$slug") || return 1
  python3 -c '
import re, sys
body = open(sys.argv[1], encoding="utf-8").read()
step = sys.argv[2]
pat = re.compile(r"^###\s+Step\s+" + re.escape(step) + r"(?![0-9])", re.MULTILINE)
m = pat.search(body)
if not m:
    raise SystemExit(1)
after = body[m.end():]
next_heading = re.search(r"^###\s+", after, re.MULTILINE)
region = after[: next_heading.start()] if next_heading else after
fence = re.search(r"^```(?:yaml|yml)?\s*\n(.*?)^```", region, re.MULTILINE | re.DOTALL)
if not fence:
    raise SystemExit(1)
for line in fence.group(1).splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    if re.match(r"gate_first:\s*(true|yes|1)\s*$", stripped, re.IGNORECASE):
        raise SystemExit(0)
raise SystemExit(1)
' "$sub_file" "$step"
}

# Green iff the Contract 2b results file carries at least one `pass` record
# naming its command.  The command check is load-bearing: `all([])` over zero
# declared checks reports all_passed=True, and a pass record naming no command
# is a gate that never ran -- Contract 2b's own distinction.  Neither is green.
gate_first_results_are_green() {
  local results_file="$1"
  [[ -s "$results_file" ]] || return 1
  python3 -c '
import json, sys
path = sys.argv[1]
recs = []
try:
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                recs.append(json.loads(raw))
            except ValueError:
                raise SystemExit(1)
except OSError:
    raise SystemExit(1)
if not recs:
    raise SystemExit(1)
for r in recs:
    if r.get("outcome") != "pass":
        raise SystemExit(1)
    if not r.get("command"):
        raise SystemExit(1)
raise SystemExit(0)
' "$results_file"
}

# The green path's marker: an empty commit carrying the step trailer.  The
# verification record lands outside the repo by design, so the commit is the
# only in-repo proof that the step was discharged (Contract 9).  Not captured;
# git's own stdout is silenced so a success line cannot leak into a caller.
commit_gate_first_marker() {
  local slug="$1" step="$2"
  local repo="${REPOS[0]:-$PROJECT_PATH}"
  [[ -n "$repo" ]] || repo="$PROJECT_PATH"
  local msg="chore(loop): gate-first marker [plan:${slug}#step-${step}]"
  if git -C "$repo" commit --allow-empty -m "$msg" >/dev/null 2>&1; then
    echo "[gate-first] marker commit for ${slug} step ${step}"
    return 0
  fi
  echo "  ! [gate-first] marker commit failed for ${slug} step ${step} -- falling through to the agent" >&2
  return 1
}

# Bump current_step from $step to step+1, but only when the pointer still
# reads $step.  A pointer that moved underneath us is not ours to guess at
# (same refusal as test_ship_integrity's revert: never invent a step).
advance_subplan_current_step() {
  local slug="$1" step="$2"
  local plans_dir sub_file
  plans_dir=$(_gate_first_plans_dir) || return 1
  sub_file=$(find_subplan_file_by_slug "$plans_dir" "$slug") || return 1
  python3 -c '
import re, sys
from pathlib import Path
p = Path(sys.argv[1])
step = int(sys.argv[2])
body = p.read_text(encoding="utf-8")
m = re.search(r"^current_step:[ \t]*(\d+)[ \t]*$", body, re.MULTILINE)
if not m:
    raise SystemExit(1)
if int(m.group(1)) != step:
    raise SystemExit(1)
p.write_text(body[:m.start()] + "current_step: " + str(step + 1) + body[m.end():], encoding="utf-8")
raise SystemExit(0)
' "$sub_file" "$step"
}

# The whole fast path.  Returns 0 only when the gate passed AND the step was
# advanced with no agent invocation.  Any other outcome returns 1 and leaves
# durable state untouched (the results file is the caller's to discard), so
# the caller falls through to the agent exactly as today.
attempt_gate_first_fast_path() {
  local results_file="$1"
  : > "$results_file"

  local target_line slug step
  target_line="${PRE_ITER_TARGET:-}"
  [[ -n "$target_line" ]] || return 1
  slug="${target_line%% *}"
  step="${target_line#* }"
  [[ -n "$slug" && "$step" =~ ^[0-9]+$ ]] || return 1

  step_declares_gate_first "$slug" "$step" || return 1

  echo "[gate-first] $slug step $step declares gate_first: true -- running its gate before the agent"

  local tf
  tf=$(mktemp)
  printf '%s %s\n' "$slug" "$step" > "$tf"
  invoke_local_checks "$PROJECT_PATH" "$tf" "$LOCAL_CHECKS_SCRIPT" "$LOCAL_CHECKS_TIMEOUT_SEC" "$results_file"
  rm -f "$tf"

  gate_first_results_are_green "$results_file" || return 1

  commit_gate_first_marker "$slug" "$step" || return 1
  advance_subplan_current_step "$slug" "$step" || return 1
  return 0
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

  # Plans-dir resolution anchors to the recorded pre-isolation root: under
  # selfmod isolation $project is the worktree, whose own project key has no
  # plans dir — find_subplan returns None and the gate errors out as a
  # harness failure (measured 2026-09-20, runs 131919/132454/133041: every
  # post-iteration gate of the batch died "sub-plan not found" while the same
  # checks pass from the clone root; B2 confirm-before-block then reproduced
  # the deterministic error and ship-integrity reverted a green, committed
  # ship). The TREE the gate isolates and verifies stays $project — that is
  # where the iteration's commits live; the clone's HEAD is pre-merge at
  # gate time.
  local plans_root="${SELFMOD_ORIGINAL_PROJECT_PATH:-$project}"

  # Derive outer cap from declared per-check timeouts (B2 false-stop fix).
  # Each target's declared timeout is read from the sub-plan; the overall
  # deadline is max(totalDeclared + 60s margin, outer_timeout_sec).
  local total_declared=0
  local s s_step d
  while read -r s s_step; do
    d=$(get_step_declared_timeout "$project" "$s" "$s_step")
    total_declared=$((total_declared + d))
  done < "$targets_file"
  local effective_timeout=$((total_declared + 60))
  if [[ "$effective_timeout" -lt "$outer_timeout_sec" ]]; then
    effective_timeout=$outer_timeout_sec
  fi

  local deadline
  deadline=$(($(date +%s) + effective_timeout))

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
    # Per-target: use declared timeout + margin as floor for remaining time
    local declared
    declared=$(get_step_declared_timeout "$plans_root" "$slug" "$step")
    if [[ "$declared" -gt 0 ]]; then
      local per_target=$((declared + 60))
      if [[ "$per_target" -gt "$remain_sec" ]]; then
        remain_sec=$per_target
      fi
    fi

    local tmp_out
    tmp_out=$(mktemp)

    local check_exit=0
    gtimeout "${remain_sec}s" python3 "$helper_script" --project "$plans_root" --repo-root "$project" --slug "$slug" --step "$step" > "$tmp_out" 2>&1 || check_exit=$?

    local outcome=""
    # gtimeout exits 124 when it kills the process (outer timeout fired).
    # This is a self-inflicted kill, NOT a confirmed blocking failure.
    if [[ "$check_exit" -eq 124 ]]; then
      outcome="inconclusive"
    else
      local all_passed=""
      if [[ -s "$tmp_out" ]]; then
        all_passed=$(python3 -c "
import json, sys
try:
  d = json.load(sys.stdin)
  print(str(d.get('all_passed', '')).lower())
except: pass
" < "$tmp_out" 2>/dev/null || true)
      fi
      outcome=$(local_check_outcome "$all_passed" "$check_exit")
    fi

    local tag
    case "$outcome" in
      pass) tag="OK" ;;
      fail) tag="FAIL" ;;
      inconclusive) tag="INCONCLUSIVE" ;;
      *) tag="ERR" ;;
    esac
    # Include the gate command in the echo so every gate outcome is auditable.
    # Previously only slug/step/outcome were shown — a passing gate was
    # indistinguishable from one that never ran.
    local gate_cmd=""
    if [[ -s "$tmp_out" ]]; then
      gate_cmd=$(python3 -c "
import json, sys
try:
  d = json.load(sys.stdin)
  rs = d.get('results', [])
  if rs: print(rs[0].get('command', ''))
except: pass
" < "$tmp_out" 2>/dev/null || true)
    fi
    if [[ -n "$gate_cmd" ]]; then
      echo "  [local_checks $tag] $slug step $step -> $outcome  cmd: $gate_cmd"
    else
      echo "  [local_checks $tag] $slug step $step -> $outcome"
    fi

    # Emit JSONL record with command + output for failing checks (AC-1..AC-4).
    # Uses emit_jsonl_record.py to avoid hand-interpolated JSON (the quoting trap).
    python3 "${_SKILL_ROOT}/ilk-loop/scripts/emit_jsonl_record.py" \
      "$results_file" "$tmp_out" "$outcome" "$check_exit" "$slug" "$step"

    rm -f "$tmp_out"
  done < "$targets_file"
}

test_all_shipped() {
  (cd "$PROJECT_PATH" && python3 "$LOOP_STATUS_SCRIPT" >/dev/null 2>&1)
}

# Classify the loop status into exactly three outcomes:
#   runnable         — at least one sub-plan can be picked up
#   all-shipped      — every registered sub-plan is shipped
#   blocked-no-runnable — outstanding sub-plans exist but none are runnable
#
# Uses loop_status.py --json to distinguish states that the exit-code
# contract conflates (exit 0 covers both "all shipped" and "blocked, no
# runnable"). Falls back to the exit-code heuristic if --json fails.
#
# Sets CLASSIFIED_STATUS and BLOCKED_SUBPLANS (space-separated fnames).
classify_loop_status() {
  local json_output
  json_output=$(cd "$PROJECT_PATH" && python3 "$LOOP_STATUS_SCRIPT" --json 2>/dev/null) || json_output=""

  if [[ -z "$json_output" ]]; then
    # Fallback: the JSON oracle is unavailable, so proof CANNOT be consulted.
    # That is itself a cannot-prove condition, so it fails closed to
    # shipped-unproven rather than certifying a clean finish on the strength of
    # an instrument that just failed.
    if test_all_shipped; then
      CLASSIFIED_STATUS="shipped-unproven"
      UNPROVEN_SUBPLANS="(loop_status --json unavailable; proof not consulted)"
    else
      CLASSIFIED_STATUS="runnable"
      UNPROVEN_SUBPLANS=""
    fi
    BLOCKED_SUBPLANS=""
    return 0
  fi

  local has_runnable blocked_fnames
  has_runnable=$(python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
subplans = data.get('subplans', [])
runnable = [s for s in subplans if s.get('status') in ('pending', 'in-progress')]
blocked = [s for s in subplans if s.get('status') not in ('shipped', 'pending', 'in-progress')]
# 'stalled' is loop_status's own verdict that outstanding work exists and
# NONE of it is runnable (loop_status.py:558 -- next_pending excludes blocked
# AND blocked-dependent sub-plans, :395-402).  It must win over the per-status
# scan below, which cannot see depends_on: a 'pending' sub-plan whose
# dependency is 'blocked' reads as runnable here while loop_status reports
# 'zero runnable, 1 blocked-dependent'.  On gh-resolve 2026-09-23 that
# disagreement dispatched three agent iterations per run (1010s and about
# 1.7 USD for one of them) that could only conclude 'ask the human', exited
# 'no-progress', and was re-dispatched hourly through the night.
# NOTE: keep dollar-sign characters OUT of this comment -- it lives
# inside a double-quoted bash string under `set -u`, so a dollar
# followed by a digit expands to an unbound positional parameter and
# kills the whole command substitution, silently falling back to
# 'runnable'.  Caught by
# test_classification_is_not_all_shipped_when_a_subplan_is_unproven
# on 2026-09-23, before it shipped.
if data.get('stalled'):
    print('blocked-no-runnable')
elif runnable:
    print('runnable')
elif blocked:
    print('blocked-no-runnable')
else:
    # Every sub-plan is shipped -- but 'shipped' is a SELF-REPORT. Consult the
    # ship-proof ledger before calling it a clean finish. Until 2026-09-08 the
    # loop printed 'SHIP PROOF MISSING: 2 sub-plans shipped without proof' and
    # 'ALL SHIPPED -- nothing to run' in the SAME run: the report existed, and
    # the terminal state never read it.
    unproven = [s for s in subplans
                if s.get('status') == 'shipped' and not s.get('proven', True)]
    print('shipped-unproven' if unproven else 'all-shipped')
" <<<"$json_output") || has_runnable="runnable"

  blocked_fnames=$(python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
subplans = data.get('subplans', [])
blocked = [s['fname'] for s in subplans if s.get('status') not in ('shipped', 'pending', 'in-progress')]
print(' '.join(blocked))
" <<<"$json_output") || blocked_fnames=""

  local unproven_slugs
  unproven_slugs=$(python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
rows = []
for s in data.get('subplans', []):
    if s.get('status') != 'shipped' or s.get('proven', True):
        continue
    rows.append(s.get('slug', s.get('fname', '?'))
                + '(' + s.get('proof_state', 'unproven') + ')')
print(' '.join(rows))
" <<<"$json_output") || unproven_slugs=""

  CLASSIFIED_STATUS="$has_runnable"
  BLOCKED_SUBPLANS="$blocked_fnames"
  UNPROVEN_SUBPLANS="$unproven_slugs"
}

get_plans_dir() {
  # Resolve active plans dir via ilk_paths.py, with legacy walk-up fallback.
  local resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  if [[ -f "$resolver" ]]; then
    local json
    json=$(python3 "$resolver" --start "$PROJECT_PATH" 2>/dev/null) || true
    if [[ -n "$json" ]]; then
      local resolved
      resolved=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('resolved_plans_dir',''))" <<<"$json")
      if [[ -n "$resolved" && -d "$resolved" ]]; then
        echo "$resolved"
        return 0
      fi
    fi
  fi
  # Legacy walk-up
  local cur="$PROJECT_PATH"
  while true; do
    local candidate="$cur/docs/plans"
    if [[ -d "$candidate" && -n "$(ls "$candidate"/MASTER-*.md 2>/dev/null | head -1)" ]]; then
      echo "$candidate"
      return 0
    fi
    local parent
    parent=$(dirname "$cur")
    [[ "$parent" == "$cur" ]] && return 1
    cur="$parent"
  done
}

get_plan_slugs() {
  # Emit one slug per line from all sub-plan files in the given plans dir.
  # Args: $1 = plans_dir (optional, defaults to get_plans_dir)
  local plans_dir="${1:-}"
  if [[ -z "$plans_dir" || ! -d "$plans_dir" ]]; then
    plans_dir=$(get_plans_dir) || return 0
  fi
  [[ -z "$plans_dir" || ! -d "$plans_dir" ]] && return 0

  for f in "$plans_dir"/*.md; do
    [[ "$(basename "$f")" == MASTER* ]] && continue
    local slug
    slug=$(sed -n 's/^plan:\s*//p' "$f" 2>/dev/null | head -1 | sed "s/['\"]//g" | xargs)
    [[ -n "$slug" ]] && echo "$slug"
  done
}

check_trailer_slugs_against_plans() {
  # Compare trailer slugs from the iteration's commits against the plans dir.
  # Report unknown slugs loudly — a trailer naming a slug that has no
  # corresponding sub-plan file is a typo, not missing work.
  #
  # Args: $1 = repo   $2 = before SHA   $3 = after SHA   $4 = plans_dir
  # Returns 0 if all slugs are known, 1 if unknown slugs found.
  local repo="$1" before="$2" after="$3" plans_dir="$4"

  if [[ "$before" == "$after" || "$before" == "(unknown)" || "$after" == "(unknown)" ]]; then
    return 0
  fi

  local msgs
  msgs=$(git -C "$repo" log "${before}..${after}" --pretty=format:"%s%n%b" 2>/dev/null) || return 0
  [[ -z "$msgs" ]] && return 0

  # Extract unique slugs from trailers
  local trailer_slugs
  trailer_slugs=$(echo "$msgs" | grep -oE '\[plan:[^#]+#(step-[0-9]+|ship)\]' | \
    sed -E 's/\[plan:([^#]+)#.*/\1/' | sort -u)
  [[ -z "$trailer_slugs" ]] && return 0

  # Get known plan slugs
  local plan_slugs
  plan_slugs=$(get_plan_slugs "$plans_dir")
  [[ -z "$plan_slugs" ]] && return 0

  # Find unknown slugs (in trailers but not in plans)
  local unknown_slugs
  unknown_slugs=$(comm -23 <(echo "$trailer_slugs" | sort) <(echo "$plan_slugs" | sort))
  [[ -z "$unknown_slugs" ]] && return 0

  # Report unknown slugs with the nearest real slug
  while IFS= read -r bad_slug; do
    [[ -z "$bad_slug" ]] && continue
    # Find nearest real slug (longest common prefix)
    local nearest="" best_len=0
    while IFS= read -r real_slug; do
      [[ -z "$real_slug" ]] && continue
      local common_len=0
      local a="$bad_slug" b="$real_slug"
      local min_len=$(( ${#a} < ${#b} ? ${#a} : ${#b} ))
      for (( j=0; j<min_len; j++ )); do
        if [[ "${a:$j:1}" == "${b:$j:1}" ]]; then
          common_len=$((common_len + 1))
        else
          break
        fi
      done
      if [[ $common_len -gt $best_len ]]; then
        best_len=$common_len
        nearest="$real_slug"
      fi
    done <<< "$plan_slugs"

    # Find commits carrying this slug
    local bad_shas
    bad_shas=$(echo "$msgs" | grep -B1 "$bad_slug" | grep -oE '[0-9a-f]{7,}' | head -3 | tr '\n' ' ')

    if [[ -n "$nearest" ]]; then
      echo "  ! [trailer-slug] unknown slug '$bad_slug' in commit ${bad_shas}(nearest: '$nearest')" >&2
    else
      echo "  ! [trailer-slug] unknown slug '$bad_slug' in commit ${bad_shas}" >&2
    fi
  done <<< "$unknown_slugs"

  return 1
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

converge_ship_transition() {
  # Finish a ship transition this iteration started and did not complete.
  #
  # Shipping is two writes to two stores -- the sub-plan front-matter (outside
  # the repo, under ~/.ilk-data) and the marker commit (inside it) -- and the
  # agent performs both. An iteration killed at the timeout boundary between
  # them leaves the pair half-written, with nothing afterwards able to say
  # which half landed. Measured 2026-09-08 in gh-resolve: MASTER-2026-09-07c
  # read `shipped` while conflict-batch-verify read `in-progress` at step 2,
  # with the [plan:conflict-batch-verify#ship] commit present.
  #
  # --only-interrupted is deliberate and load-bearing: it converges ONLY pairs
  # the intent marker attests to. test_ship_integrity (below) reverts a shipped
  # sub-plan to in-progress when its gate is red, and the marker commit stays
  # in history -- so an unconditional repair would re-ship it on the next run,
  # out-voting a deliberate revert. ship_transition.py writes the intent marker
  # before the first half and clears it only when both are durable, so its
  # presence distinguishes "died mid-write" from "a gate unwound this".
  #
  # Args: $1 = plans_dir (optional, defaults to get_plans_dir)
  # Never fails the iteration: a refusal is reported, not enforced. Enforcement
  # of the un-decidable direction is test_ship_integrity's job.
  local plans_dir="${1:-}"
  if [[ -z "$plans_dir" || ! -d "$plans_dir" ]]; then
    plans_dir=$(get_plans_dir) || return 0
  fi
  [[ -z "$plans_dir" || ! -d "$plans_dir" ]] && return 0

  local script="${_SKILL_ROOT}/ilk-loop/scripts/ship_transition.py"
  [[ ! -f "$script" ]] && return 0

  local out=""
  out=$(python3 "$script" --repair --apply --only-interrupted \
        --plans-dir "$plans_dir" --repo "$PROJECT_PATH" 2>&1) || true
  # Silent when there is nothing to converge -- the common case is one line of
  # "0 diverged pairs", which is noise on every iteration.
  if [[ -n "$out" && "$out" != *"0 diverged pairs"* ]]; then
    echo "  [ship-transition] $out"
  fi
  return 0
}

test_ship_integrity() {
  # Ship-integrity enforcement: a sub-plan must not be "shipped" while its
  # declared local_checks gate is red.
  # Args: $1 = plans_dir (optional, defaults to get_plans_dir)
  #       $2 = local_checks_results_file (JSONL, one line per check)
  # Returns 0 if all clean, 1 if violations found (and prints them to stderr).
  local plans_dir="${1:-}"
  local lc_file="${2:-}"
  if [[ -z "$plans_dir" || ! -d "$plans_dir" ]]; then
    plans_dir=$(get_plans_dir) || return 0
  fi
  [[ -z "$plans_dir" || ! -d "$plans_dir" ]] && return 0

  local ship_integrity_script="${_SKILL_ROOT}/ilk-loop/scripts/ship_integrity.py"
  [[ ! -f "$ship_integrity_script" ]] && return 0

  local violations=0

  # The sub-plans registered in the ACTIVE master. This is the batch boundary,
  # and it has to be explicit.
  #
  # 6010938 replaced the non-verdict `continue` with `skip` so a worker that
  # skipped its gate could not skip step-commit enforcement. The reasoning was
  # right — "does every authored step have a commit" is a question about git
  # history and holds whether or not a gate ran — and the consequence was not:
  # the gate result was ALSO the de-facto batch scope, because the walk below is
  # `for f in "$plans_dir"/*.md` and nothing else narrows it.
  #
  # MEASURED 2026-09-16 on gh-resolve, by the gh-resolve session and confirmed
  # here: 445 plan files, 360 shipped, 358 gated, **71 would revert**, oldest
  # 2026-07-26-corpus-dossier. Not a tail of recent work — the entire July-to-
  # September corpus. That is the 2026-08-20 incident again (69 of 150 then),
  # and it is worse than a status flip: ship_integrity_violation maps to
  # shipped-unverified -> needs-human (detached-component-contracts.md:112),
  # which test_shipped_unverified_never_relaunches.py pins as never-relaunch.
  # The first iteration after that change would halt an unattended pipeline.
  local _active_subplans=""
  _active_subplans=$(python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
try:
    from plan_status import extract_subplan_files
    from loop_status import pick_active_master
    masters = sorted(Path(sys.argv[1]).glob('MASTER-*.md'))
    if masters:
        chosen, _ = pick_active_master(masters, json_mode=True)
        for n in extract_subplan_files(Path(chosen).read_text(encoding='utf-8')):
            print(n)
except Exception:
    pass
" "$plans_dir" "${_SKILL_ROOT}/ilk-loop/scripts" 2>/dev/null)

  for f in "$plans_dir"/*.md; do
    [[ "$(basename "$f")" == MASTER* ]] && continue
    # Check if shipped
    if ! head -20 "$f" | grep -qE '^status:\s*shipped'; then
      continue
    fi
    # NOTE: the gate-declared test lives in the Python block below, which
    # reuses ship_audit.read_subplan_for_audit -- the same reader the audit
    # uses. It was previously `head -20 | grep -qE '^\s*local_checks:\s*'` +
    # end-of-line anchor, which missed BOTH per-step ```yaml gates and
    # `local_checks: []` frontmatter, and also missed any frontmatter longer
    # than 20 lines. A gated sub-plan therefore skipped enforcement entirely
    # (observed 2026-08-21: MASTER-2026-08-21-loop-execution-speed, 3 of 3
    # sub-plans). A detector the driver and the audit disagree on is worse
    # than no detector, so there is exactly one reader now.

    # Call ship_integrity.py with gate-passed (slug + gate lookup in Python).
    # Uses --gate-passed (scalar) to avoid shell JSON quote-mangling.
    local si_out si_exit
    si_exit=0
    si_out=$(python3 -c "
import json, sys, re
from pathlib import Path

f = Path(sys.argv[1])
lc_file = sys.argv[2] if len(sys.argv) > 2 else ''
scripts_dir = sys.argv[3] if len(sys.argv) > 3 else ''

# Gate-declared test, via the audit's own reader so the two cannot disagree.
# Covers frontmatter block form, 'local_checks: []' plus per-step blocks, and
# frontmatter of any length. 'nogate' falls through the caller's skip branch.
if scripts_dir:
    sys.path.insert(0, scripts_dir)
    try:
        from ship_audit import read_subplan_for_audit as _read_sp
        if not _read_sp(f).get('declared_checks'):
            print('nogate')
            raise SystemExit(0)
    except (ImportError, OSError):
        pass

# Extract slug from frontmatter (POSIX-safe, no grep -P)
body = f.read_text()
m = re.search(r'^---\s*\n(.*?)\n---', body, re.DOTALL)
slug = ''
if m:
    for line in m.group(1).splitlines():
        if line.strip().startswith('plan:'):
            slug = line.split(':', 1)[1].strip()
            break

# Look up gate outcome from THIS iteration's local_checks JSONL.
# 'skip' means the slug has no result in this iteration — see the scoping
# guard in the caller. Only 'true'/'false' are real verdicts; this path
# must never emit 'unknown', which ship_integrity.py treats as a violation.
gate_passed = 'skip'
if lc_file and slug:
    try:
        for raw in Path(lc_file).read_text().splitlines():
            rec = json.loads(raw)
            if rec.get('slug') == slug:
                outcome = rec.get('outcome', '')
                if outcome == 'pass':
                    gate_passed = 'true'
                elif outcome in ('fail', 'error'):
                    gate_passed = 'false'
                break
    except (OSError, json.JSONDecodeError):
        pass

print(gate_passed)
" "$f" "$lc_file" "${_SKILL_ROOT}/ilk-loop/scripts" 2>&1) || si_exit=$?

    local gate_passed="$si_out"
    # Scope to THIS iteration's ships only: enforce on sub-plans whose gate
    # actually ran this iteration (present in the local_checks JSONL). A
    # sub-plan shipped in a PRIOR run has no current-iteration gate result --
    # re-litigating it would falsely flag (and revert) already-shipped work on
    # every iteration. Ported from run_ilk_loop_claude.ps1:816-821, whose
    # absence here reverted 69 of 150 sub-plans in one run (2026-08-20).
    #
    # Anything that is not a real verdict is a skip, never 'unknown':
    # ship_integrity.py counts 'unknown' as a violation, so passing it from
    # this enforcement path is what caused the mass revert.
    # A non-verdict is NOT a reason to skip the whole check. The 2026-08-20
    # protection this comment describes is about the GATE half — a prior-run
    # ship has no current-iteration gate result and must never be failed for
    # it. The STEP-COMMIT half is a question about git history and holds
    # regardless of whether a gate ran, so it is enforced either way; passing
    # `skip` tells ship_integrity.py to enforce that half only.
    #
    # Measured 2026-09-16: `continue` here meant a worker that skipped its gate
    # skipped enforcement with it. authored-steps-stop-at-the-findings-section
    # shipped twice with no commit for step 2 — the exact condition this check
    # exists to refuse — because iteration 1 ran no gates at all.
    if [[ "$gate_passed" != "true" && "$gate_passed" != "false" ]]; then
      # Enforce without a gate verdict ONLY for sub-plans in the active batch.
      # Outside it, a non-verdict still means skip-entirely: an archived ship
      # has no current gate result and re-litigating it is the 2026-08-20
      # mass revert.
      if [[ -n "$_active_subplans" ]] \
         && printf '%s\n' "$_active_subplans" | grep -qxF "$(basename "$f")"; then
        gate_passed="skip"
      else
        continue
      fi
    fi

    si_exit=0
    si_out=$(python3 "$ship_integrity_script" --subplan "$f" --gate-passed "$gate_passed" 2>&1) || si_exit=$?
    if [[ $si_exit -ne 0 ]]; then
      local slug
      slug=$(python3 -c "
import re, sys
from pathlib import Path
body = Path(sys.argv[1]).read_text()
m = re.search(r'^---\s*\n(.*?)\n---', body, re.DOTALL)
if m:
    for line in m.group(1).splitlines():
        if line.strip().startswith('plan:'):
            print(line.split(':', 1)[1].strip()); break
" "$f" 2>/dev/null)
      echo "  [ship-integrity VIOLATION] $slug: $si_out" >&2
      # Revert status to in-progress (Python — BSD sed -i requires explicit suffix)
      #
      # The pointer reverts WITH the status. A red gate that moved only
      # `status` leaves the plan recording the red step as done, and a resume
      # starts past it and never revalidates it — measured on kira-cloudflare
      # run 20260915-112812, where step 1's gate went red at 12:19:53 while
      # `current_step: 2` had been written at 12:19:13, 40 seconds earlier.
      #
      # The target is DERIVED from the gate records being enforced — the
      # lowest step of this slug whose gate came back fail/error — never by
      # decrementing. An iteration may have advanced several steps, and
      # `current_step - 1` would silently skip the ones in between.
      #
      # Two deliberate refusals:
      #   - No gate record for the slug (the `skip` path, where the violation
      #     is step-commit-only) means there is no evidence about WHICH step is
      #     bad. Leave the pointer alone rather than guess.
      #   - Never move the pointer forward. A derived step at or past the
      #     current value is not a revert, and writing it would be this bug in
      #     the other direction.
      local revert_out=""
      revert_out=$(python3 -c "
import json, re, sys
from pathlib import Path

subplan, lc_file, slug = sys.argv[1], sys.argv[2], sys.argv[3]
p = Path(subplan)
body = p.read_text()
body = re.sub(r'^(status:\s*)shipped', r'\1in-progress', body, count=1, flags=re.MULTILINE)

failed_steps = []
if lc_file and slug:
    try:
        for raw in Path(lc_file).read_text().splitlines():
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get('slug') != slug:
                continue
            if rec.get('outcome') not in ('fail', 'error'):
                continue
            step = rec.get('step')
            if isinstance(step, bool) or not isinstance(step, int):
                continue
            failed_steps.append(step)
    except OSError:
        pass

note = ''
if failed_steps:
    target = min(failed_steps)
    m = re.search(r'^current_step:\s*(\d+)\s*\$', body, re.MULTILINE)
    if m is None:
        note = 'no current_step field to revert'
    elif int(m.group(1)) <= target:
        note = 'pointer already at or behind step %d' % target
    else:
        body = re.sub(r'^current_step:\s*\d+\s*\$', 'current_step: %d' % target,
                      body, count=1, flags=re.MULTILINE)
        note = 'current_step %s -> %d (gate for step %d was red)' % (
            m.group(1), target, target)
else:
    note = 'no gate record for this slug; pointer left untouched'

p.write_text(body)
print(note)
" "$f" "$lc_file" "$slug" 2>/dev/null)
      echo "  [ship-integrity] reverted $slug to in-progress; ${revert_out:-pointer unchanged}" >&2
      # A rejected gate invalidates the ship intent for the slug it rejects.
      #
      # Contract note (§7h): this adds a new *writer* of the ship-intent file,
      # so per references/detached-component-contracts.md the filename and
      # lifecycle stay owned by ship_transition.py — we call through
      # invalidate_intent() and never unlink by path.
      #
      # Scoped to THIS slug on purpose. converge_ship_transition runs before
      # this enforcement (see the ordering comment at its call site), so an
      # intent naming another slug may describe a transition still in flight;
      # clearing it unconditionally re-creates the bug this closes.
      if [[ -n "$slug" ]]; then
        python3 -c "
import sys
sys.path.insert(0, sys.argv[3])
from ship_transition import invalidate_intent
if invalidate_intent(sys.argv[1], sys.argv[2]):
    print('  [ship-integrity] invalidated ship intent for ' + sys.argv[2])
" "$plans_dir" "$slug" "${_SKILL_ROOT}/ilk-loop/scripts" >&2 || true
      fi
      violations=1
    fi
  done
  return $violations
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

# ── Batch-end gate ───────────────────────────────────────────────────────────
# Runs the project's declared test suite ONCE at batch end (all sub-plans
# shipped).  Persists {verdict, head_sha, invocation, timestamp} to the
# runtime dir so /ilk-ship can verify the record instead of running its own
# suite.
#
# AC-6: a failure inside the gate is reported and the runner still terminates.
#       Never hang, never continue as though it passed.
# Resolve the per-iteration wall-clock bound, in seconds.
#
# Normally ITERATION_TIMEOUT_MIN * 60.  ILK_ITERATION_TIMEOUT_SEC overrides it
# with a raw seconds value — a TEST AFFORDANCE, not an operator control, and
# deliberately env-only: --iteration-timeout-min takes whole minutes, so 60s is
# the floor, and two integration tests that wait one out cost 121s of a 300s
# suite (measured 2026-08-26 from the batch gate's --durations=25; three tests
# were 181s of 299s).  A sub-minute production iteration timeout is never
# wanted, so this stays out of --help.
#
# Contract note (§7h — run_ilk_loop_claude.* is contract-governed): this adds a
# new *reader* of configuration, not a new writer of runtime state.  Per
# references/detached-component-contracts.md's "Adding a new reader or writer"
# checklist, no sentinel, PID file, or record schema is touched, so no
# contract-visible surface changes.  Per references/orchestration-collaboration.md
# the L1-L4 invariants are unaffected: the timeout branch, its exit code, and
# the WIP-preservation path behave identically at any bound.
#
# A malformed value (empty, zero, negative, non-integer) falls back to minutes
# rather than producing a 0 bound that would time out every iteration instantly.
resolve_iteration_timeout_sec() {
  local override="${ILK_ITERATION_TIMEOUT_SEC:-}"
  if [[ "$override" =~ ^[0-9]+$ ]] && (( override > 0 )); then
    echo "$override"
    return 0
  fi
  echo $((ITERATION_TIMEOUT_MIN * 60))
}

invoke_batch_gate() {
  local project_path="$1"
  local runtime_dir="$2"

  # Resolve batch_gate.py from the same skill root
  local batch_gate_script="${_SKILL_ROOT}/ilk-loop/scripts/batch_gate.py"
  if [[ ! -f "$batch_gate_script" ]]; then
    echo "[batch-gate] WARNING: batch_gate.py not found at $batch_gate_script — skipping gate"
    return 0
  fi

  echo "[batch-gate] Running batch-end gate..."
  local gate_output
  local gate_exit=0
  # NOTE: --runtime-dir is deliberately NOT passed.  $runtime_dir here is the
  # *launcher* dir (get_ilk_runtime_dir → ilk_paths.external_launcher_dir),
  # which is per-launch ephemera.  The record is project runtime state that
  # ship_audit reads long after the run, so batch_gate resolves it itself via
  # resolve_runtime_dir().  Passing the launcher dir is what made the gh-resolve
  # verdict unreadable on 2026-08-25: marker and suite output landed in
  # runtime/launcher/ while the audit looked in runtime/.  One resolver only.
  gate_output=$(python3 "$batch_gate_script" \
    --project "$project_path" \
    --run 2>&1) || gate_exit=$?
  if [[ $gate_exit -ne 0 ]]; then
    echo "[batch-gate] Gate exited with code $gate_exit — output:"
    echo "$gate_output"
    echo "[batch-gate] Continuing despite gate failure (AC-6)."
  else
    echo "[batch-gate] Gate completed."
    if [[ -n "$gate_output" ]]; then
      echo "$gate_output"
    fi
  fi

  # Always return 0 — the gate must never prevent the runner from terminating
  return 0
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

  # Run claude with the worker home's settings env applied EXPLICITLY.
  # Measured 2026-09-20: stripping the ambient ANTHROPIC_* vars and trusting
  # the CLI to apply settings.json's env block left every scheduler-driven
  # worker on the CLI default endpoint (Anthropic official, opus-5) while
  # records claimed the configured model — the loops burned the account's
  # five-hour window to exhaustion without ever reaching the configured
  # endpoint (operator-confirmed). Exporting the settings env here makes the
  # endpoint and model actually used the ones configured; the display Model
  # line and reality cannot diverge by this route. Empty exports (no settings
  # env block) is a no-op.
  local exit_code=0
  local settings_env_exports=""
  if [[ "$SETTINGS_HAS_ENV" -eq 1 ]]; then
    settings_env_exports=$(SETTINGS_JSON="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json" python3 -c '
import json, os, shlex
try:
    d = json.load(open(os.environ["SETTINGS_JSON"]))
except Exception:
    raise SystemExit(0)
for k, v in sorted(d.get("env", {}).items()):
    print("export %s=%s;" % (k, shlex.quote(str(v))))
') || settings_env_exports=""
  fi
  (cd "$cwd" && { [[ -z "$PATH_PRELUDE" ]] || eval "$PATH_PRELUDE"; } \
      && eval "$settings_env_exports" \
      && gtimeout "${timeout_sec}s" claude "${claude_args[@]}") \
    | tee "$jsonl_log" | python3 "$renderer" | tee "$iter_log" \
    || exit_code=$?

  # Detect budget-exhausted via the terminal result's terminal_reason field only.
  # Phrase-based patterns ("budget exhausted") match agent thinking/output that
  # describes budget concepts — only terminal_reason is the authoritative signal.
  local budget_exhausted=0
  if [[ -f "$jsonl_log" ]] \
     && grep -qE '"terminal_reason"\s*:\s*"budget_exhausted"' "$jsonl_log" 2>/dev/null; then
    budget_exhausted=1
  fi

  # Detect quota exhaustion via the SP6 classifier (quota_detect.py).
  # The classifier is a pure function over parsed JSON — no file reads, no
  # subprocesses, no clock other than the injected now.
  # Only writes through SP5's writer (provider_state.py) when exhausted;
  # writes nothing when not exhausted.
  local quota_exhausted=0
  if [[ -f "$jsonl_log" ]]; then
    local runtime_dir
    runtime_dir="$(get_ilk_runtime_dir)" || runtime_dir=""
    local provider_state_path=""
    if [[ -n "$runtime_dir" ]]; then
      provider_state_path="${runtime_dir}/provider-state.json"
    fi
    local _quota_result
    _quota_result="$(python3 -c "
import json, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '${_SKILL_ROOT}/ilk-loop/scripts')
from quota_detect import classify

log_path = Path('$jsonl_log')
if not log_path.is_file():
    sys.exit(0)

lines = []
for line in log_path.read_text(encoding='utf-8-sig').splitlines():
    line = line.strip()
    if line:
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            continue

if not lines:
    sys.exit(0)

now = datetime.now(timezone.utc)
result = classify(lines, now=now)
if result['exhausted']:
    print('exhausted')
    # Write through SP5's writer.
    state_path = Path('$provider_state_path')
    if state_path.parent.exists():
        try:
            from provider_state import read_state, write_state, ProviderStateError
            try:
                state = read_state(state_path)
            except ProviderStateError:
                state = {'version': 1, 'homes': {}}
            # Record the exhaustion verdict.
            state['exhausted_provider'] = {
                'reset_at': result.get('reset_at'),
                'detected_at': now.isoformat(),
                'evidence': str(log_path),
            }
            write_state(state_path, state)
        except Exception:
            pass  # Best-effort; do not crash the runner
" 2>/dev/null)" || _quota_result=""
    if [[ "$_quota_result" == "exhausted" ]]; then
      quota_exhausted=1
    fi
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
  ITER_QUOTA_EXHAUSTED=$quota_exhausted
}

# Decide the iteration stop reason from the iteration's outcome.
# Inputs (positional):
#   $1 = completed       (1 = normal exit, 0 = gtimeout boundary kill)
#   $2 = total_new       (new commit count this iteration)
#   $3 = budget_exhausted (1 = hit --max-budget-usd)
#   $4 = no_progress_streak (consecutive zero-commit iterations before this one)
#   $5 = quota_exhausted (1 = provider quota cap detected)
# Prints: the stop reason, or empty string to continue.
_decide_iter_stop_reason() {
  local completed="$1"
  local total_new="$2"
  local budget_exhausted="$3"
  local current_streak="$4"
  local quota_exhausted="${5:-0}"

  if [[ "$completed" -eq 0 && "$total_new" -eq 0 ]]; then
    # Boundary kill with no new commits — a genuine barren timeout.
    echo "timeout"
  elif [[ "$budget_exhausted" -eq 1 ]]; then
    echo "budget-exhausted"
  elif [[ "$quota_exhausted" -eq 1 ]]; then
    echo "quota-exhausted"
  elif [[ "$total_new" -eq 0 ]]; then
    local new_streak=$((current_streak + 1))
    if [[ "$new_streak" -ge 3 ]]; then
      echo "no-progress"
    fi
    # else: empty (continue), streak warning handled by caller
  fi
  # completed=1 and total_new > 0: empty string (continue)
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
  if [[ -n "$RESOLVED_MODEL" ]]; then
    echo "Model:          $RESOLVED_MODEL (from $RESOLVED_MODEL_SOURCE)"
  else
    echo "Model:          (unresolved)"
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

# ----- Local checks outcome mapping ---------------------------------
# Maps helper result + process exit code to an outcome string.
# Usage: local_check_outcome <all_passed_or_empty> <exit_code>
# When first arg is "true"/"false", trust that; otherwise fall back to exit code.
local_check_outcome() {
  local all_passed="$1"
  local exit_code="$2"
  # Prefer all_passed from helper JSON when available
  if [[ "$all_passed" == "true" ]]; then
    echo "pass"
    return
  fi
  if [[ "$all_passed" == "false" ]]; then
    echo "fail"
    return
  fi
  # Fallback: exit-code mapping
  case "$exit_code" in
    0) echo "pass" ;;
    1) echo "fail" ;;
    *) echo "error" ;;
  esac
}

# preserve_dirty_tree_on_timeout
#
# When an iteration is killed by gtimeout (ITER_COMPLETED=0), the working tree
# may hold finished but uncommitted work.  Commit it as a WIP so the next
# iteration resumes from a recoverable state.
#
# The commit is identifiable as WIP by its message prefix and by the
# [wip:timeout] trailer.  No sub-plan status is mutated — this is purely a
# durability measure, not a gate bypass.
#
# AC-1: dirty tree → WIP commit on timeout.
# AC-2: WIP commit identifiable by message shape.
# AC-4: untracked files inside the repo are included (git add -A).
# AC-7: failures inside this function must not abort the run (set +e).
_stamp_reentry_note() {
  # Append a re-entry note to the active sub-plan's Findings section.
  # Called by preserve_dirty_tree_on_timeout when slug+step are resolved.
  # Args: $1=slug $2=step
  local slug="$1" step="$2"

  # Resolve plans dir.
  local resolver="${_SKILL_ROOT}/ilk-loop/scripts/ilk_paths.py"
  local plans_dir
  plans_dir=$(python3 "$resolver" --start "$PROJECT_PATH" 2>/dev/null | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('external_plans_dir') or d.get('plans_dir') or '')" 2>/dev/null) || plans_dir=""
  if [[ -z "$plans_dir" || ! -d "$plans_dir" ]]; then
    # Try in-tree fallback.
    if [[ -d "$PROJECT_PATH/docs/plans" ]]; then
      plans_dir="$PROJECT_PATH/docs/plans"
    else
      return 0  # no plans dir — nothing to stamp
    fi
  fi

  # Find the sub-plan file by slug.
  local sub_file
  sub_file=$(grep -rl "^plan: $slug" "$plans_dir"/*.md 2>/dev/null | head -1) || true
  if [[ -z "$sub_file" || ! -f "$sub_file" ]]; then
    return 0  # sub-plan file not found — nothing to stamp
  fi

  # Read the step heading for the re-entry note.
  local step_heading
  step_heading=$(grep -m1 "^### Step $step" "$sub_file" 2>/dev/null) || step_heading=""

  # Read remaining commit line from the step body.
  local remaining_line
  remaining_line=$(python3 -c "
import re, sys
text = open(sys.argv[1], encoding='utf-8').read()
body = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.S)
step_re = re.compile(r'^### Step \Q$step\E\b.*$', re.M)
m = step_re.search(body)
if m:
    # Find the first bullet after the heading
    after = body[m.end():]
    for line in after.splitlines():
        stripped = line.strip()
        if stripped.startswith('- '):
            print(stripped)
            break
" "$sub_file" 2>/dev/null) || remaining_line=""

  # Build the re-entry note.
  local timestamp
  timestamp=$(date '+%Y-%m-%d %H:%M:%S')
  local note="### Re-entry $timestamp — step $step killed at its bound"
  if [[ -n "$step_heading" ]]; then
    note="$note
- Step in flight: $step_heading"
  fi
  if [[ -n "$remaining_line" ]]; then
    note="$note
- $remaining_line"
  fi

  # Append to Findings section. If ## Findings exists and is the last section,
  # append after it; otherwise append at end of file.
  if grep -q "^## Findings" "$sub_file" 2>/dev/null; then
    # Insert the note after the ## Findings line.
    python3 -c "
import sys
path = sys.argv[1]
note = sys.argv[2]
text = open(path, encoding='utf-8').read()
# Find ## Findings and append after it.
idx = text.find('## Findings')
if idx >= 0:
    # Find end of the line
    eol = text.find('\n', idx)
    if eol >= 0:
        text = text[:eol+1] + '\n' + note + '\n' + text[eol+1:]
open(path, 'w', encoding='utf-8').write(text)
" "$sub_file" "$note" 2>/dev/null
  else
    # No Findings section — append at end of file.
    echo "" >> "$sub_file"
    echo "## Findings" >> "$sub_file"
    echo "" >> "$sub_file"
    echo "$note" >> "$sub_file"
  fi

  echo "[runner] re-entry note stamped in $(basename "$sub_file") for step $step" >&2
  return 0
}

#
# Globals read: REPOS, PROJECT_PATH
# Globals modified: none
# Returns: 0 always (never fatal)
preserve_dirty_tree_on_timeout() {
  local wip_count=0
  local repo

  # Resolve the active sub-plan slug and step for re-entry stamping
  # (retro-2026-09-20-a-step-that-outgrew-its-window P3).
  local _reentry_slug="" _reentry_step=""
  local _targets
  _targets=$(get_active_subplan_targets 2>/dev/null) || true
  if [[ -n "$_targets" ]]; then
    _reentry_slug="${_targets%% *}"
    _reentry_step="${_targets#* }"
  fi

  for repo in "${REPOS[@]}"; do
    # Must be a git repo
    git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue

    # Check for tracked changes OR untracked files
    local has_dirty=0
    if ! git -C "$repo" diff --quiet 2>/dev/null || \
       ! git -C "$repo" diff --cached --quiet 2>/dev/null; then
      has_dirty=1
    fi
    local has_untracked=0
    if [[ -n "$(git -C "$repo" ls-files --others --exclude-standard 2>/dev/null)" ]]; then
      has_untracked=1
    fi
    if [[ "$has_dirty" -eq 0 && "$has_untracked" -eq 0 ]]; then
      continue
    fi

    # Stage everything (tracked + untracked) and commit.
    # Wrapped in set +e so a failure (detached HEAD, hook rejection, unwritable
    # index) logs and continues to the next repo / terminal classification
    # rather than aborting the run (AC-7).
    (
      set +e
      git -C "$repo" add -A 2>/dev/null
      local file_count
      file_count=$(git -C "$repo" diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
      local diff_stat
      diff_stat=$(git -C "$repo" diff --cached --stat 2>/dev/null | tail -1)

      # Build trailer: [wip:timeout] files=N ... step=<slug>#<N>
      local _wip_trailer="[wip:timeout] files=$file_count $diff_stat"
      if [[ -n "$_reentry_slug" && -n "$_reentry_step" ]]; then
        _wip_trailer="$_wip_trailer step=$_reentry_slug#$_reentry_step"
      fi

      git -C "$repo" commit -m "WIP: preserve timed-out iteration changes

Preserved by ilk-runner on timeout.  This commit is NOT a gate pass —
the next iteration will re-run verification.

$_wip_trailer" >/dev/null 2>&1
      # stdout MUST be redirected, not just stderr: this function ends with
      # `echo "$wip_count"`, so its stdout is the return value that :2182
      # captures into _WIP_PRESERVED.  A successful `git commit` prints
      # "[main abc1234] WIP: ...", which made int() raise in the JSONL
      # builder and lost the whole iteration record (run 20260829-163114).
      local rc=$?
      if [[ "$rc" -eq 0 ]]; then
        echo "[runner] WIP commit: preserved $file_count files in $repo" >&2
      else
        echo "[runner] WIP commit failed in $repo (rc=$rc) — work may be lost" >&2
      fi
    )
    # Count even if the commit failed — the attempt is what matters for telemetry
    wip_count=$((wip_count + 1))
  done

  # Stamp re-entry state into the active sub-plan's Findings section
  # (retro-2026-09-20-a-step-that-outgrew-its-window P3).
  if [[ -n "$_reentry_slug" && -n "$_reentry_step" ]]; then
    _stamp_reentry_note "$_reentry_slug" "$_reentry_step" || true
  fi

  echo "$wip_count"
}

# count_iteration_metrics
#
# Parse the per-iteration stream JSON log to extract tool-call and test-
# invocation counts.  These are emitted into the JSONL summary record so
# that `timeout` is diagnosable rather than opaque (AC-12).
#
# The stream JSON contains assistant messages with tool_use content blocks.
# A test invocation is a Bash tool call whose command contains a test-runner
# pattern (pytest, jest, npm test, bun test, vitest, mocha, cargo test,
# go test, or run_tests).
#
# Globals read: none
# Globals modified: none
# Args: $1 = path to the per-iteration .jsonl stream log
# stdout: "<tool_calls> <test_invocations>"
count_iteration_metrics() {
  local jsonl_log="$1"
  if [[ ! -s "$jsonl_log" ]]; then
    echo "0 0"
    return
  fi
  python3 -c "
import json, sys, re

tool_calls = 0
test_invocations = 0
test_pattern = re.compile(r'pytest|jest|npm\s+test|bun\s+test|vitest|mocha|cargo\s+test|go\s+test|run_tests', re.I)

for line in open(sys.argv[1], encoding='utf-8', errors='replace'):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        continue
    if d.get('type') != 'assistant':
        continue
    msg = d.get('message', {})
    for block in msg.get('content', []):
        if block.get('type') != 'tool_use':
            continue
        tool_calls += 1
        name = block.get('name', '')
        inp = block.get('input', {})
        if name == 'Bash':
            cmd = inp.get('command', '')
            if test_pattern.search(cmd):
                test_invocations += 1

print(f'{tool_calls} {test_invocations}')
" "$jsonl_log" 2>/dev/null || echo "0 0"
}

# write_suite_result_artifact
#
# After an iteration completes, scan its JSONL log for a broad test command.
# If found, write a machine-readable ``suite-result.json`` into the run
# directory so the next iteration can read it instead of re-running the suite.
#
# Fields: command, outcome, summary_line, exit_code, head_sha, timestamp.
# Absent data is written as explicit null (never as a plausible-looking zero).
#
# AC-4 from a-suite-result-outlives-its-iteration.
#
# Globals read: RUN_LOG_DIR
# Globals modified: none
# Args: $1 = path to the per-iteration .jsonl stream log
# Returns: 0 always (never fatal — a missing artifact is not a run-stopper)
write_suite_result_artifact() {
  local jsonl_log="$1"
  local artifact="${RUN_LOG_DIR}/suite-result.json"
  if [[ ! -s "$jsonl_log" ]]; then
    return 0
  fi
  python3 -c "
import json, sys
from pathlib import Path

# Import from the co-located iteration_timing module.
sys.path.insert(0, str(Path(sys.argv[0]).resolve().parent.parent / 'scripts'))
from iteration_timing import extract_suite_result_from_jsonl

jsonl_path = Path(sys.argv[1])
artifact_path = Path(sys.argv[2])

records = []
with open(jsonl_path, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            records.append(json.loads(line))

result = extract_suite_result_from_jsonl(records)
if result is None:
    sys.exit(0)

# Write with explicit nulls for absent data (AC-4 contract).
artifact_path.write_text(
    json.dumps(result, indent=2, ensure_ascii=False) + '\n',
    encoding='utf-8',
)
print(f'[suite-result] wrote artifact: {result[\"outcome\"]} — {result.get(\"summary_line\", \"no summary\")}', file=sys.stderr)
" "$jsonl_log" "$artifact" 2>/dev/null || true
}

# read_prior_suite_result_for_prompt
#
# Read the suite-result.json artifact from the previous iteration and format
# it for prompt injection.  Returns empty string when no artifact exists.
#
# AC-5 from a-suite-result-outlives-its-iteration.
#
# Globals read: RUN_LOG_DIR
# Globals modified: none
# stdout: formatted text for prompt injection (empty if no artifact)
read_prior_suite_result_for_prompt() {
  local artifact="${RUN_LOG_DIR}/suite-result.json"
  if [[ ! -f "$artifact" ]]; then
    return 0
  fi
  python3 -c "
import json, sys
from pathlib import Path

artifact = Path(sys.argv[1])
try:
    d = json.loads(artifact.read_text(encoding='utf-8-sig'))
except (json.JSONDecodeError, OSError):
    sys.exit(0)

cmd = d.get('command', '')
outcome = d.get('outcome', 'unknown')
summary = d.get('summary_line')
sha = d.get('head_sha')
ts = d.get('timestamp', '')

parts = [f'Previous iteration ran: {cmd}']
parts.append(f'Outcome: {outcome}')
if summary:
    parts.append(f'Result: {summary}')
if sha:
    parts.append(f'At commit: {sha[:12]}')
if ts:
    parts.append(f'When: {ts}')
print('\n'.join(parts))
" "$artifact" 2>/dev/null || true
}

# reap_iteration_orphans
#
# Kill background processes spawned by the iteration so they don't outlive
# it and compete for the next iteration's budget (AC-13).
#
# After invoke_claude_iteration returns synchronously, any child of this
# runner shell that is still running is an orphan from the iteration.
# Scoped to direct children of this shell — NOT by name pattern, which
# would kill the operator's own unrelated runs (AC-13 constraint).
#
# Wrapped in set +e so a failure (process already dead, permission denied)
# cannot abort the run (AC-14).
#
# Globals read: none
# Globals modified: none
# Returns: 0 always (never fatal)
reap_iteration_orphans() {
  (
    set +e
    local runner_pid=$$
    local child_pids
    child_pids=$(pgrep -P "$runner_pid" 2>/dev/null) || true
    local reaped=0
    for pid in $child_pids; do
      # Skip if pid is empty or is the runner itself
      [[ -z "$pid" || "$pid" == "$runner_pid" ]] && continue
      kill -TERM "$pid" 2>/dev/null && reaped=$((reaped + 1)) || true
    done
    if [[ "$reaped" -gt 0 ]]; then
      echo "[runner] reaped $reaped orphaned background processes" >&2
    fi
  )
}

# ----- Main ------------------------------------------------------------------

main() {
  parse_args "$@"

  # --- Single-instance lock (per-project) ----------------------------------
  # Acquire an exclusive flock via ilk_run_lock.py and re-exec ourselves under
  # it.  The lock lives on the open file description, survives exec (FD_CLOEXEC
  # cleared), and is released by the kernel when the process dies — even
  # SIGKILL.  Placement here is the contract: BEFORE preflight (which creates
  # the run directory), the sentinel write, or running.pid write, so a refused
  # second runner leaves no trace (AC-5).
  if [[ -z "${ILK_RUN_LOCK_HELD:-}" ]]; then
    local runtime_dir_for_lock
    runtime_dir_for_lock=$(get_ilk_runtime_dir) || runtime_dir_for_lock=""
    if [[ -n "$runtime_dir_for_lock" ]]; then
      local lock_file="${runtime_dir_for_lock}/run.lock"
      mkdir -p "$(dirname "$lock_file")"
      export ILK_RUN_LOCK_HELD=1
      # The helper acquires the lock and exec's us.  On success the current
      # process is replaced (the lines below never run).  On failure the
      # helper exits 3 (lock held) or 1 (other error) and we see the code.
      local lock_rc=0
      python3 "${_SKILL_ROOT}/ilk-loop/scripts/ilk_run_lock.py" \
        --lock "$lock_file" -- bash "$0" "$@" && { exit 0; } || lock_rc=$?
      # If we get here, the lock was NOT acquired.
      if [[ $lock_rc -eq 3 ]]; then
        echo "[runner] another runner holds this project's lock. Exiting." >&2
        exit 3
      fi
      echo "[runner] lock helper failed (exit $lock_rc)" >&2
      exit 1
    fi
  fi

  preflight
  discover_git_repos
  parse_master_branch_block
  # Resolve the actual worker model for display + JSONL telemetry.
  # Uses resolve_worker_model.py: flag > env > settings.json env block > unknown.
  local _cfg_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  local _resolver="${_SKILL_ROOT}/ilk-loop/scripts/resolve_worker_model.py"
  if [[ -f "$_resolver" ]]; then
    local _resolved
    _resolved=$(python3 "$_resolver" --model "${MODEL:-}" --env-model "${ANTHROPIC_MODEL:-}" --config-dir "$_cfg_dir" 2>/dev/null) || _resolved="|unknown"
    RESOLVED_MODEL="${_resolved%%|*}"
    RESOLVED_MODEL_SOURCE="${_resolved##*|}"
  else
    RESOLVED_MODEL="${MODEL:-}"
    RESOLVED_MODEL_SOURCE="unknown"
  fi
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
    # Claim running.pid — only the lock winner reaches here (ILK_RUN_LOCK_HELD=1).
    # launch.sh no longer writes the PID; the runner owns the file.
    if [[ "${ILK_RUN_LOCK_HELD:-}" == "1" ]]; then
      echo "$$" > "${runtime_dir}/running.pid"
    fi
    python3 -c "import json; print(json.dumps({
      'state': 'running',
      'pid': $$,
      'run_id': '$RUN_ID',
      'started_at': '$loop_started_at',
      'project_path': '$PROJECT_PATH',
      'cli': 'claude'
    }))" > "${runtime_dir}/last-exit.json.tmp" && mv -f "${runtime_dir}/last-exit.json.tmp" "${runtime_dir}/last-exit.json"
    echo "Sentinel: ${runtime_dir}/last-exit.json (state=running)"

    # Ensure the sentinel is never left as "running" on abnormal exit.
    # finalize_sentinel is idempotent — no-op when a clean path already set
    # a terminal state (all-shipped, error, max-iterations, etc.).
    trap 'record_err_context "$LINENO" "$BASH_COMMAND"' ERR
    trap finalize_sentinel EXIT INT TERM
  else
    echo "Sentinel: skipped (no runtime dir resolved)"
  fi

  # Initial check: classify the loop status into runnable / all-shipped / blocked.
  classify_loop_status
  if [[ "$CLASSIFIED_STATUS" == "all-shipped" ]]; then
    echo "All sub-plans already shipped. Nothing to do."
    # Batch-end gate: run the suite once before the master is done (SP1)
    if [[ -n "$runtime_dir" ]]; then
      invoke_batch_gate "$PROJECT_PATH" "$runtime_dir"
    fi
    echo "[ilk] ALL SHIPPED — nothing to run. Do NOT relaunch."
    local ts
    ts=$(date +%Y-%m-%dT%H:%M:%S%z)
    write_jsonl_record "{\"run_id\":\"$RUN_ID\",\"cli\":\"claude\",\"iteration\":0,\"timestamp\":\"$ts\",\"project\":\"$PROJECT_PATH\",\"stop_reason\":\"already-shipped\"}"
    return 0
  elif [[ "$CLASSIFIED_STATUS" == "shipped-unproven" ]]; then
    # Every sub-plan reports shipped and at least one has no proof. That is not
    # a clean finish and must not read as one.
    echo "Sub-plans report shipped, but proof is missing. Not a clean finish."
    echo "[ilk] SHIPPED WITHOUT PROOF — ${UNPROVEN_SUBPLANS:-unknown}. No gate ran for these; the ship claim is unverified. Do NOT relaunch; this needs a human." >&2
    local ts
    ts=$(date +%Y-%m-%dT%H:%M:%S%z)
    write_jsonl_record "{\"run_id\":\"$RUN_ID\",\"cli\":\"claude\",\"iteration\":0,\"timestamp\":\"$ts\",\"project\":\"$PROJECT_PATH\",\"stop_reason\":\"shipped-unproven\"}"
    return 0
  elif [[ "$CLASSIFIED_STATUS" == "blocked-no-runnable" ]]; then
    local blocked_count
    blocked_count=$(echo "$BLOCKED_SUBPLANS" | wc -w | tr -d ' ')
    echo "Blocked — ${blocked_count} sub-plan(s) parked for a human, 0 runnable: ${BLOCKED_SUBPLANS}. Nothing to do."
    echo "[ilk] BLOCKED — ${blocked_count} sub-plan(s) parked for a human, 0 runnable: ${BLOCKED_SUBPLANS}. Do NOT relaunch."
    local ts
    ts=$(date +%Y-%m-%dT%H:%M:%S%z)
    write_jsonl_record "{\"run_id\":\"$RUN_ID\",\"cli\":\"claude\",\"iteration\":0,\"timestamp\":\"$ts\",\"project\":\"$PROJECT_PATH\",\"stop_reason\":\"blocked-no-runnable\"}"
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

    # -- Steer hook: pause gate (OUTSIDE timed iteration region) ---------
    # If pause.flag is present, idle here — does NOT count toward
    # ITERATION_TIMEOUT_MIN and is NOT classified as no-progress/stuck.
    if [[ -n "$PROJECT_KEY" ]]; then
      invoke_steer_hook "$PROJECT_KEY"
      while [[ "$STEER_PAUSED" -eq 1 ]]; do
        echo "[steer] pause.flag detected — idling (remove pause.flag to resume)"
        sleep 5
        invoke_steer_hook "$PROJECT_KEY"
      done
    else
      STEER_INTERJECTION_TEXT=""
      STEER_PAUSED=0
    fi

    local iter_start
    iter_start=$(date +%s)

    local heads_before_file heads_after_file
    heads_before_file="${RUN_LOG_DIR}/heads-before-${i}.tmp"
    heads_after_file="${RUN_LOG_DIR}/heads-after-${i}.tmp"

    get_repo_heads "$heads_before_file"

    # Capture the sub-plan this iteration is about to work, BEFORE the agent
    # runs. It is the gate's fallback target when the commit carries no
    # [plan:...#step-N] trailer (the shared-remote case), and it has to be read
    # now: the agent marks the sub-plan `shipped` during the iteration, so by the
    # time the gate block runs there is no longer an unshipped sub-plan to find.
    # Measured end-to-end — reading it afterwards resolved nothing and the gate
    # still did not run.
    PRE_ITER_TARGET="$(get_active_subplan_targets 2>/dev/null || true)"
    # The ledger writer's wider baseline: every non-shipped sub-plan and the
    # step it starts this iteration on.  Read here for the same reason
    # PRE_ITER_TARGET is -- afterwards the agent has already marked sub-plans
    # `shipped` and moved their steps, so the pre-iteration value is gone.
    # stderr is NOT silenced: a baseline that failed to resolve is a missing
    # ledger row, not an empty project.
    PRE_ITER_ALL_STEPS="$(get_all_subplan_steps || true)"

    local iter_log
    iter_log="${RUN_LOG_DIR}/iter-$(printf '%02d' $i).log"

    local timeout_sec
    timeout_sec=$(resolve_iteration_timeout_sec)

    # -- Prior suite result carry-forward (AC-5) --------------------------
    # If a previous iteration ran a broad suite, inject its result so this
    # iteration starts informed instead of re-running from scratch.
    local prior_suite_text
    prior_suite_text=$(read_prior_suite_result_for_prompt)
    local iter_prompt="$PROMPT"
    if [[ -n "$prior_suite_text" ]]; then
      iter_prompt="SUITE RESULT FROM PREVIOUS ITERATION (do not re-run unless you have reason to believe the result is stale):
${prior_suite_text}

${iter_prompt}"
      echo "[suite-result] prior result injected into prompt"
    fi

    # -- Steer hook: interjection text -----------------------------------
    if [[ -n "$STEER_INTERJECTION_TEXT" ]]; then
      iter_prompt="OPERATOR INTERJECTIONS (honor before continuing the plan):
${STEER_INTERJECTION_TEXT}

${PROMPT}"
      echo "[steer] interjection injected (${#STEER_INTERJECTION_TEXT} chars)"
    fi

    # ── Pre-iteration record ──────────────────────────────────────────
    # Written BEFORE the agent runs, so a killed runner still leaves a
    # classifiable trace.
    #
    # `gtimeout` kills the AGENT and the runner survives to write the
    # completion record -- verified 2026-08-29 across four real runs, and not
    # the defect it looked like (the 0-byte .ilk-loop.log on rezmac was
    # f5674c6's int() crash, which needs a dirty tree).  The open gap is a
    # SIGKILL to the RUNNER: stop.sh, a `launchctl bootout`, or the machine
    # dying mid-iteration leaves ZERO records, a sentinel stuck at `running`,
    # and an orphaned `gtimeout ... claude -p`.
    #
    # That matters because this file is collect.py's only input, and
    # scheduler.sh:510 builds its blacklist from postmortems derived from it.
    # No records => no postmortem => dispatchable forever, which is how three
    # relaunches ran unbounded on 2026-08-29.
    #
    # Spaced separators, matching every other line in .ilk-loop.log; readers
    # json.loads per line.  (The compact-separator contract F2 pinned governs
    # the gate-results file -- Contract 2b -- not this one.)
    #
    # `|| true`: set -e is active here, and a bookkeeping write must never
    # abort the batch.
    local _start_ts
    _start_ts=$(date +%Y-%m-%dT%H:%M:%S%z)
    RUN_ID="$RUN_ID" \
    _ITER="$i" \
    _TS="$_start_ts" \
    PROJECT_PATH="$PROJECT_PATH" \
    MODEL="$MODEL" \
    _RESOLVED_MODEL="${RESOLVED_MODEL:-}" \
    python3 -c "
import json, os
print(json.dumps({
  'run_id': os.environ['RUN_ID'],
  'cli': 'claude',
  'iteration': int(os.environ['_ITER']),
  'timestamp': os.environ['_TS'],
  'project': os.environ['PROJECT_PATH'],
  'model': os.environ.get('_RESOLVED_MODEL', '') or os.environ.get('MODEL', '') or os.environ.get('ANTHROPIC_MODEL', ''),
  'status': 'started',
}))
" >> "$JSONL_LOG" || true

    # -- Selfmod isolation (DP-2): detect and create worktree ----------
    # If this project is the toolkit clone, run the batch in an isolated
    # worktree so live consumer loops keep executing the stable clone.
    SELFMOD_ISOLATED=0
    if selfmod_isolation_required; then
      SELFMOD_ISOLATED=1
      SELFMOD_ORIGINAL_PROJECT_PATH="$PROJECT_PATH"
      local runtime_dir
      runtime_dir="$(get_ilk_runtime_dir)" || {
        echo "[selfmod] ERROR: cannot resolve runtime dir — aborting." >&2
        exit 1
      }
      SELFMOD_WORKTREE_PATH="${runtime_dir}/worktrees/selfmod-batch"
      SELFMOD_MERGE_LOCK_PATH="${runtime_dir}/selfmod-merge.lock"
      create_selfmod_worktree
    fi

    # -- Gate-first fast path (opt-in: gate_first: true on the step) --------
    #
    # Run the current step's declared gate BEFORE dispatching the agent when
    # the step opts in.  Green advances with an empty marker and zero model
    # turns; red falls through to the agent exactly as today.  A step without
    # the marker never reaches this branch.
    local GATE_FIRST_GREEN=0
    local gate_first_results=""
    if [[ "$RUN_LOCAL_CHECKS" == true ]]; then
      gate_first_results=$(mktemp)
      if attempt_gate_first_fast_path "$gate_first_results"; then
        GATE_FIRST_GREEN=1
        echo "[gate-first] green -- advancing without invoking the agent"
        # invoke_claude_iteration normally sets these; without an agent turn
        # they stay at their pre-loop defaults and _decide_iter_stop_reason
        # reads the iteration as a boundary kill.
        ITER_COMPLETED=1
        ITER_EXIT_CODE=0
        ITER_BUDGET_EXHAUSTED=0
        ITER_QUOTA_EXHAUSTED=0
      else
        rm -f "$gate_first_results"
        gate_first_results=""
      fi
    fi

    if [[ "$GATE_FIRST_GREEN" -eq 0 ]]; then
      invoke_claude_iteration "$PROJECT_PATH" "$iter_log" "$iter_prompt" "$timeout_sec" "$MAX_BUDGET_USD" "$MODEL"
    fi

    local iter_end iter_dur_sec
    iter_end=$(date +%s)
    iter_dur_sec=$((iter_end - iter_start))

    # Count tool calls and test invocations from the stream JSON log (AC-12).
    local iter_tool_calls=0 iter_test_invocations=0
    local _metrics
    _metrics=$(count_iteration_metrics "${iter_log}.jsonl" 2>/dev/null) || _metrics="0 0"
    iter_tool_calls=${_metrics%% *}
    iter_test_invocations=${_metrics##* }

    # Reap background processes spawned by this iteration (AC-13, AC-14).
    # Find the gtimeout PID from the process tree — it's the direct child of
    # this shell that ran the claude invocation.  Scoped to what the iteration
    # started, NOT by name pattern.
    # Wrapped in set +e is already active below, so a failure here is safe.
    reap_iteration_orphans

    # Write suite-result artifact if a broad test ran this iteration (AC-4).
    # Must happen before set +e so a Python crash is visible.
    write_suite_result_artifact "${iter_log}.jsonl"

    # Non-essential bookkeeping: wrap in set +e so a stray non-zero
    # (grep no match, python3 parse failure, etc.) cannot abort the batch
    # before an explicit terminal classification is written.
    # Intended terminal signals (iter_stop_reason, test_all_shipped) are
    # checked below via explicit conditionals, not set -e.
    set +e

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

    # Ship-gap: committed-vs-changed path accounting
    local _SHIP_GAP_JSON=""
    local _SHIP_GAP_UNEXPLAINED=0
    for r in "${REPOS[@]}"; do
      local h_before h_after gap_json
      h_before=$(head_before_sha "$r" "$heads_before_file")
      h_after=$(head_after_sha "$r" "$heads_after_file")
      if [[ -n "$h_before" && -n "$h_after" ]]; then
        gap_json=$(python3 "${_SKILL_ROOT}/ilk-loop/scripts/ship_gap.py" \
          --repo "$r" --head-before "$h_before" --head-after "$h_after" --json 2>/dev/null) || gap_json=""
        if [[ -n "$gap_json" ]]; then
          _SHIP_GAP_JSON="$gap_json"
          local unexplained
          unexplained=$(echo "$gap_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(1 if d.get('unexplained') else 0)" 2>/dev/null) || unexplained=0
          if [[ "$unexplained" -eq 1 ]]; then
            _SHIP_GAP_UNEXPLAINED=1
          fi
        fi
      fi
    done

    echo ""
    echo "  duration: ${iter_dur_sec}s  exit: $ITER_EXIT_CODE  new commits: $total_new"
    if [[ -s "$new_commits_file" ]]; then
      while read -r repo_line count_line; do
        echo "    $repo_line : +$count_line"
      done < "$new_commits_file"
    fi
    if [[ "$_SHIP_GAP_UNEXPLAINED" -eq 1 ]]; then
      local gap_paths gap_committed gap_gap
      gap_paths=$(echo "$_SHIP_GAP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['tree_paths'])" 2>/dev/null) || gap_paths=0
      gap_committed=$(echo "$_SHIP_GAP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['committed_paths'])" 2>/dev/null) || gap_committed=0
      gap_gap=$(echo "$_SHIP_GAP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['gap'])" 2>/dev/null) || gap_gap=0
      echo "  ! [ship-gap] verified $gap_paths changed paths, committed $gap_committed — $gap_gap uncommitted at iteration end"
    fi

    # Ship-proof ledger: record which commits belong to which step range.
    # Only writes when there are new commits (total_new > 0) — an
    # unproductive iteration claims no steps (AC-2).
    if [[ "$total_new" -gt 0 ]]; then
      write_ship_proof_records "$heads_before_file" "$heads_after_file" "$i"
    fi

    # Stall detection — extracted to _decide_iter_stop_reason for testability.
    local iter_stop_reason=""
    iter_stop_reason=$(_decide_iter_stop_reason \
      "$ITER_COMPLETED" "$total_new" "$ITER_BUDGET_EXHAUSTED" "$no_progress_streak" "$ITER_QUOTA_EXHAUSTED")

    # Preserve any dirty tree as a WIP commit on boundary kills so the
    # next iteration can resume from a recoverable state (AC-1, AC-2, AC-4).
    local wip_preserved=0
    if [[ "$ITER_COMPLETED" -eq 0 ]]; then
      wip_preserved=$(preserve_dirty_tree_on_timeout 2>/dev/null) || wip_preserved=0
    fi

    # Update no-progress streak
    if [[ "$total_new" -eq 0 ]]; then
      no_progress_streak=$((no_progress_streak + 1))
      if [[ "$iter_stop_reason" == "" && "$ITER_EXIT_CODE" -ne 0 ]]; then
        echo "  ! agent exited $ITER_EXIT_CODE (likely transient upstream API error). Streak: $no_progress_streak/3. Continuing." >&2
      fi
    else
      no_progress_streak=0
    fi

    # Optional local_checks
    #
    # NOT gated on $total_new. The commit count is an input to TRAILER
    # SCANNING below, not a precondition for gating: the fallback (ledger ->
    # PRE_ITER_TARGET -> active sub-plan) exists for the case where trailers
    # are absent, and a shared remote strips them by policy. Guarding the whole
    # block on the commit count made that fallback unreachable in the one case
    # that most needs it -- measured 2026-09-08 on a consumer host, an
    # iteration with 0 commits was never gated and the run reported
    # all-shipped over two unproven sub-plans.
    local local_checks_results=""
    if [[ "$RUN_LOCAL_CHECKS" == true ]]; then
      if [[ "$GATE_FIRST_GREEN" -eq 1 && -n "$gate_first_results" ]]; then
        # The gate already ran on the fast path and passed.  Re-running it
        # here would double the exact cost the fast path just saved (the
        # batch-verification gate is a whole suite).  Its Contract 2b record
        # is the record this iteration reports and enforces.
        local_checks_results="$gate_first_results"
        gate_first_results=""
      else
      local all_targets_file
      all_targets_file=$(mktemp)
      for r in "${REPOS[@]}"; do
        local before after
        before=$(grep -F "$r=" "$heads_before_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
        after=$(grep -F "$r=" "$heads_after_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
        get_local_check_targets "$(selfmod_effective_repo "$r")" "$before" "$after" >> "$all_targets_file"
      done

      # Check trailer slugs against plans dir — catch typos at write time.
      # Only fires when trailers were found (not on shared remotes where
      # trailers are absent by policy).  An unknown slug is a loud finding
      # naming both the trailer and the nearest real slug.
      if [[ -s "$all_targets_file" ]]; then
        local plans_dir_for_check
        plans_dir_for_check=$(get_plans_dir) || plans_dir_for_check=""
        if [[ -n "$plans_dir_for_check" ]]; then
          for r in "${REPOS[@]}"; do
            local before after
            before=$(grep -F "$r=" "$heads_before_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
            after=$(grep -F "$r=" "$heads_after_file" 2>/dev/null | sed 's/^[^=]*=//' | head -n1)
            check_trailer_slugs_against_plans "$(selfmod_effective_repo "$r")" "$before" "$after" "$plans_dir_for_check" || true
          done
        fi
      fi

      # Trailer scanning found nothing. Either the iteration committed nothing,
      # or it committed without trailers. On a shared remote the latter is the
      # EXPECTED state, not an anomaly:
      # the trailer policy strips the [plan:…#step-N] tags this discovery reads.
      # Without this fallback the declared gate silently never runs and the
      # sub-plan ships as loop-verified on the strength of nothing.
      if [[ ! -s "$all_targets_file" ]]; then
        # Prefer the ledger: it records the step the iteration REACHED, not
        # the step it started on.  On a shared remote the ledger is the only
        # source that can resolve the last step's gate (AC-7).
        get_ledger_check_targets "$RUN_ID" "$i" >> "$all_targets_file"
        # Fall back to the pre-iteration capture when the ledger is empty
        # (e.g. the iteration produced no commits, or the ledger write failed).
        if [[ ! -s "$all_targets_file" ]]; then
          if [[ -n "${PRE_ITER_TARGET:-}" ]]; then
            printf '%s\n' "$PRE_ITER_TARGET" >> "$all_targets_file"
          else
            get_active_subplan_targets >> "$all_targets_file"
          fi
        fi
        if [[ -s "$all_targets_file" ]]; then
          echo "  [local_checks] no commit trailers found; gating the active sub-plan instead ($(tr '\n' ' ' < "$all_targets_file"))"
        else
          # Neither source produced a target. Say so loudly: a silent skip here
          # is what let unverified work ship as verified.
          echo "  ! [local_checks] NO gate target could be resolved (no commit trailers, no ledger row, no unshipped sub-plan) — gate did NOT run. This iteration made ${total_new} commit(s)." >&2
        fi
      fi

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

      # B2 enforcement: a gate that ERRORED or FAILED must BLOCK. Do NOT break
      # here — set iter_stop_reason and let the post-record check below (after
      # the JSONL write) break. Otherwise a gate-stopped run is never written
      # to .ilk-loop.log and collect.py / ilk-feedback go blind (falling back
      # to a stale run -> the misclassification cascade).
      #
      # B2 confirm-before-block (2026-06-17): a transient `error` (flaky exit,
      # missing shell builtin) must be CONFIRMED by re-running the blocking
      # checks once before committing to local_checks_failed.
      # One JSON reader for every question asked of this file. It used to be
      # four `grep -qE '"outcome":"(error|fail)"'` calls, which the writer's
      # `json.dumps` (a space after every colon) never matched -- so the whole
      # of B2 was dead code. See blocking_checks.py's module docstring and
      # references/detached-component-contracts.md.
      local blocking_checks_script="${_SKILL_ROOT}/ilk-loop/scripts/blocking_checks.py"
      if [[ -s "$local_checks_results" ]]; then
        if python3 "$blocking_checks_script" "$local_checks_results" --any; then
          # Extract blocking slug/step pairs and re-run them
          local blocking_targets
          blocking_targets=$(mktemp)
          python3 "$blocking_checks_script" "$local_checks_results" --targets \
            > "$blocking_targets" 2>/dev/null

          local rerun_results=""
          if [[ -s "$blocking_targets" ]]; then
            rerun_results=$(mktemp)
            invoke_local_checks "$PROJECT_PATH" "$blocking_targets" "$LOCAL_CHECKS_SCRIPT" "$LOCAL_CHECKS_TIMEOUT_SEC" "$rerun_results"
          fi

          # Call confirm_b2_block via run_local_checks.py --confirm-b2
          # Build first-pass and rerun JSON arrays (command-less, match by slug+step)
          local confirm_out=""
          if [[ -n "$rerun_results" && -s "$rerun_results" ]]; then
            confirm_out=$(python3 -c "
import json, sys

first_file, rerun_file = sys.argv[1], sys.argv[2]
with open(first_file, encoding='utf-8-sig') as f:
    first = [json.loads(l) for l in f if l.strip()]
with open(rerun_file, encoding='utf-8-sig') as f:
    rerun = [json.loads(l) for l in f if l.strip()]

blocking = [r for r in first if r.get('outcome') in ('fail', 'error')]
rerun_map = {(r['slug'], r.get('step', 0)): r['outcome'] for r in rerun}
confirmed = []
transient = []
for b in blocking:
    key = (b['slug'], b.get('step', 0))
    ro = rerun_map.get(key)
    if ro in ('fail', 'error'):
        confirmed.append(b)
    else:
        transient.append(key)

result = {'blocked': len(confirmed) > 0, 'transient_cleared': [str(k) for k in transient]}
print(json.dumps(result))
" "$local_checks_results" "$rerun_results" 2>/dev/null)
          fi

          rm -f "$blocking_targets"
          [[ -n "$rerun_results" ]] && rm -f "$rerun_results"

          # Parse confirm result
          local confirmed_blocked="true"
          if [[ -n "$confirm_out" ]]; then
            confirmed_blocked=$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
print('false' if not d.get('blocked', True) else 'true')
" "$confirm_out" 2>/dev/null || echo "true")
          fi

          if [[ "$confirmed_blocked" == "false" ]]; then
            echo "B2 transient cleared on re-run" >&2
          else
            # Confirmed blocking — try auto-quarantine before stopping.
            local quarantine_script="${_SKILL_ROOT}/ilk-loop/scripts/quarantine_subplan.py"
            local quarantined="false"
            if [[ -f "$quarantine_script" ]]; then
              local q_plans_dir
              # Resolve via the driver's own helper. `ilk_paths.py --plans-dir`
              # is NOT a flag ilk_paths has (it accepts --start, --where,
              # --sentinel-path): argparse rejected it, exited 2, and wrote its
              # usage to stderr -- which `2>/dev/null` discarded. So this
              # resolved to the empty string, the guard below was false at
              # EVERY failure count, and quarantine_subplan.py was never
              # invoked at all. The `auto_block_fails` counter therefore never
              # reached even 1, which is why the threshold looked like the
              # culprit and was not. Established 2026-09-08, sub-plan
              # quarantine-is-reachable step 0.
              q_plans_dir=$(get_plans_dir 2>/dev/null) || q_plans_dir=""
              if [[ -n "$q_plans_dir" && -d "$q_plans_dir" ]]; then
                # Extract slugs from blocking results
                local q_slugs
                q_slugs=$(python3 "$blocking_checks_script" "$local_checks_results" --slugs 2>/dev/null)
                local failing_desc
                failing_desc=$(python3 "$blocking_checks_script" "$local_checks_results" --describe 2>/dev/null)
                while IFS= read -r q_slug; do
                  [[ -z "$q_slug" ]] && continue
                  local q_out
                  q_out=$(python3 "$quarantine_script" --plans-dir "$q_plans_dir" --slug "$q_slug" --failing-check "$failing_desc" 2>/dev/null)
                  if [[ -n "$q_out" ]]; then
                    local q_blocked
                    q_blocked=$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
print('true' if d.get('blocked') else 'false')
" "$q_out" 2>/dev/null || echo "false")
                    if [[ "$q_blocked" == "true" ]]; then
                      quarantined="true"
                      local q_fails q_thresh
                      q_fails=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('fails','?'))" "$q_out" 2>/dev/null || echo "?")
                      q_thresh=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('threshold','?'))" "$q_out" 2>/dev/null || echo "?")
                      echo "  [quarantine] sub-plan $q_slug auto-quarantined after $q_fails failures (threshold: $q_thresh)" >&2
                    fi
                  fi
                done <<< "$q_slugs"
              fi
            fi

            if [[ "$quarantined" == "true" ]]; then
              echo "B2 quarantine: continuing to next runnable sub-plan" >&2
            else
              iter_stop_reason="local_checks_failed"
              echo "Loop stopped: local_checks not passing (B2 confirmed)" >&2
            fi
          fi
        else
          # --any is false, so nothing attributable blocked. A record with no
          # identity is not a verdict about any sub-plan (Contract 2b
          # invariant 6): it means the invoker lost its own target — a harness
          # defect — so report loudly and continue instead of blocking the
          # batch on the harness.
          local unattr_count
          unattr_count=$(python3 "$blocking_checks_script" "$local_checks_results" --unattributable-count 2>/dev/null || true)
          if [[ "${unattr_count:-0}" -gt 0 ]]; then
            echo "  ! [local_checks] ${unattr_count} gate result(s) carried no sub-plan identity — harness defect, those gates verified nothing. See Contract 2b invariant 6." >&2
          fi
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

    # Build local_checks JSON.  The results file is NOT deleted here: it is
    # still needed by test_ship_integrity below, which looks this iteration's
    # gate verdict up in it by slug.  Deleting it here made that lookup hit
    # OSError, leave gate_passed at 'skip', and fall through the :1259 scoping
    # guard -- so a red gate shipped as verified (20260828-211346).  Cleanup
    # now happens after enforcement; see `rm -f "$local_checks_results"` below.
    local local_checks_json="[]"
    if [[ -n "$local_checks_results" && -s "$local_checks_results" ]]; then
      local_checks_json=$(python3 -c "
import json, sys
print(json.dumps([json.loads(l) for l in sys.stdin]))
" < "$local_checks_results")
    fi

    # Write JSONL record via Python to avoid bash JSON escaping issues
    local ts
    ts=$(date +%Y-%m-%dT%H:%M:%S%z)

    PROJECT_PATH="$PROJECT_PATH" \
    MODEL="$MODEL" \
    _RESOLVED_MODEL="${RESOLVED_MODEL:-}" \
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
    _SHIP_GAP_JSON="$_SHIP_GAP_JSON" \
    _WIP_PRESERVED="$wip_preserved" \
    _TOOL_CALLS="$iter_tool_calls" \
    _TEST_INVOCATIONS="$iter_test_invocations" \
    python3 -c "
import json, os
d = {
  'run_id': os.environ['RUN_ID'],
  'cli': 'claude',
  'iteration': int(os.environ['_ITER']),
  'timestamp': os.environ['_TS'],
  'project': os.environ['PROJECT_PATH'],
  'model': os.environ.get('_RESOLVED_MODEL', '') or os.environ.get('MODEL', '') or os.environ.get('ANTHROPIC_MODEL', ''),
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
wp = os.environ.get('_WIP_PRESERVED', '0')
# Defensive: a non-integer here means some command leaked into a captured
# function's stdout.  Degrade to 0 and record the raw value rather than raising
# -- an unparseable field must not cost the whole iteration record, which is the
# classifier's only input (run 20260829-163114).
try:
  wp_n = int(str(wp).strip())
except (TypeError, ValueError):
  wp_n = 0
  d['wip_preserved_raw'] = str(wp)[:200]
if wp_n > 0:
  d['wip_preserved'] = wp_n
tc = int(os.environ.get('_TOOL_CALLS', '0'))
ti = int(os.environ.get('_TEST_INVOCATIONS', '0'))
if tc > 0:
  d['tool_calls'] = tc
if ti > 0:
  d['test_invocations'] = ti
lc = os.environ.get('_LOCAL_CHECKS_JSON', '')
if lc and lc != '[]':
  d['local_checks'] = json.loads(lc)
sg = os.environ.get('_SHIP_GAP_JSON', '')
if sg:
  try:
    sg_d = json.loads(sg)
    if sg_d.get('unexplained'):
      d['ship_gap'] = sg_d
  except (json.JSONDecodeError, ValueError):
    pass
print(json.dumps(d))
" >> "$JSONL_LOG"

    # Restore set -e: terminal decision logic below must be fatal.
    set -e

    # Quality gates
    # TODO: step 6+ (invoke_quality_gates_if_needed)

    # Ship-integrity enforcement runs BEFORE the early break on
    # iter_stop_reason, not after it.  A red gate sets
    # iter_stop_reason="local_checks_failed" a few lines up, so the one case
    # where enforcement is most needed -- a sub-plan marked `shipped` while
    # its gate is red -- was the one case that broke past it untouched
    # (kira-cloudflare 20260828-211346).  The status revert has to happen
    # regardless of WHY the iteration stopped.
    #
    # Precedence: ship_integrity_violation wins over any iter_stop_reason
    # already set.  A sub-plan wrongly marked `shipped` is the condition that
    # needs a human; the red gate is merely how we noticed.  This is judgment
    # call 2 in MASTER-2026-08-29 -- read it before flipping the precedence,
    # and flip it HERE if you disagree.
    #
    # An empty $local_checks_results is legitimate -- the file is only
    # mktemp'd when this iteration had gate targets (:2076) -- but it is NOT
    # the same thing as a file that went missing, and the two used to arrive
    # here looking identical. Say which one this is, so a future "enforcement
    # silently skipped everything" is diagnosable from the log alone.
    if [[ -z "$local_checks_results" ]]; then
      echo "  [ship-integrity] no gate ran this iteration; enforcing without gate data"
    elif [[ ! -s "$local_checks_results" ]]; then
      echo "  ! [ship-integrity] gate results at $local_checks_results are missing or empty — enforcing without gate data" >&2
    fi
    # Converge a half-written ship BEFORE integrity enforcement reads the
    # front-matter: a transition killed between its two writes must not be
    # mistaken for a sub-plan that never shipped. Integrity still gets the last
    # word -- it runs after, and its revert is not re-applied (see the
    # --only-interrupted note in converge_ship_transition).
    converge_ship_transition "$(get_plans_dir)"

    # -- Selfmod merge-back: land or report --------------------------------
    # After the ship transition converges, merge the worktree back into the
    # clone.  On success, PROJECT_PATH is restored and the worktree removed.
    # On failure, the worktree is left intact and the reason is on stderr.
    if [[ "${SELFMOD_ISOLATED:-0}" -eq 1 ]]; then
      merge_rc=0
      merge_selfmod_worktree || merge_rc=$?
      if [[ $merge_rc -ne 0 ]]; then
        echo "[selfmod] merge exited $merge_rc — batch did not land." >&2
        iter_stop_reason="selfmod_merge_failed"
        stop_reason="selfmod_merge_failed"
      fi
      # Reset for next iteration (merge only runs once per batch).
      SELFMOD_ISOLATED=0
    fi

    if ! test_ship_integrity "$(get_plans_dir)" "$local_checks_results"; then
      stop_reason="ship_integrity_violation"
      iter_stop_reason="ship_integrity_violation"
      # Park the master instead of leaving it queued.  Without this call
      # the scheduler re-dispatches the batch immediately — the 13-re-
      # dispatch cycle measured on kira pv3 2026-09-18.
      local _violating_slugs=""
      if [[ -n "$local_checks_results" && -s "$local_checks_results" ]]; then
        _violating_slugs=$(python3 -c "
import json, sys
slugs = [json.loads(l).get('slug','') for l in sys.stdin if l.strip()]
print(','.join(s for s in slugs if s))
" < "$local_checks_results" 2>/dev/null) || true
      fi
      local _park_reason="ship_integrity_violation: run ${RUN_ID} slugs=[${_violating_slugs}]"
      python3 "${_SKILL_ROOT}/ilk-loop/scripts/park_master.py" \
        --plans-dir "$(get_plans_dir)" \
        --reason "$_park_reason" 2>/dev/null || true
    fi
    # After a ship-integrity revert, reconcile the master so it no longer
    # claims "shipped" when a sub-plan was un-shipped.  Without this call,
    # reconcile_master_status (now symmetric) is never reached and the
    # master stays stranded — the scheduler sees "shipped" and stops
    # dispatching the batch.  Idempotent: a clean pass is a no-op.
    python3 -c "
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from plan_status import reconcile_master_status
plans_dir = Path(sys.argv[2])
masters = sorted(plans_dir.glob('MASTER-*.md'))
for mp in masters:
    reconcile_master_status(mp, plans_dir)
" "${_SKILL_ROOT}/ilk-loop/scripts" "$(get_plans_dir)" 2>/dev/null || true
    # Every reader in this iteration is done with it.
    rm -f "$local_checks_results"

    if [[ -n "$iter_stop_reason" ]]; then
      stop_reason="$iter_stop_reason"
      break
    fi

    # There is deliberately no `test_all_shipped` shortcut here any more.
    # That helper keys off loop_status's EXIT CODE, which reports "nothing
    # actionable" and knows nothing about proof -- so reaching it first let a
    # run terminate `all-shipped` without the proof consultation below ever
    # happening. classify_loop_status subsumes it, and still falls back to it
    # internally when the JSON oracle is unavailable.
    classify_loop_status
    if [[ "$CLASSIFIED_STATUS" == "all-shipped" ]]; then
      stop_reason="all-shipped"
      break
    elif [[ "$CLASSIFIED_STATUS" == "shipped-unproven" ]]; then
      stop_reason="shipped-unproven"
      break
    elif [[ "$CLASSIFIED_STATUS" == "blocked-no-runnable" ]]; then
      stop_reason="blocked-no-runnable"
      break
    fi
  done

  if [[ -z "$stop_reason" ]]; then
    stop_reason="max-iterations"
  fi

  # Final report
  echo ""
  echo "=== Loop ended: $stop_reason ==="

  # Run-level terminal record: the per-iteration record at :3189-3247 is
  # written BEFORE enforcement can set stop_reason (D1, retro-2026-09-18).
  # Emit a second record carrying the terminal cause so readers
  # (collect.py, watchdog) see the real reason — not an empty field.
  # record_type="run_exit" marks it distinct; iter 999999 keeps it out of
  # the per-iteration de-dup key (run_id, iteration).
  local ts_terminal
  ts_terminal=$(date '+%Y-%m-%dT%H:%M:%S%z')
  python3 -c "
import json, sys
d = {
  'run_id': sys.argv[1],
  'cli': 'claude',
  'iteration': 999999,
  'timestamp': sys.argv[2],
  'project': sys.argv[3],
  'stop_reason': sys.argv[4],
  'record_type': 'run_exit',
  'iters': int(sys.argv[5]) if sys.argv[5].isdigit() else 0,
}
print(json.dumps(d))
" "$RUN_ID" "$ts_terminal" "$PROJECT_PATH" "$stop_reason" "$iter_counter" >> "$JSONL_LOG"

  echo "Run logs: $RUN_LOG_DIR"
  echo "JSONL:    $JSONL_LOG"
  echo ""
  echo "Final loop_status:"
  # Anchor the report to the RECORDED project root, not the mutable
  # PROJECT_PATH: after a failed selfmod merge PROJECT_PATH still points
  # at the isolated worktree, whose own project key has no plans dir, and
  # the report printed "no plans dir found" (run 20260920-111204, log
  # lines 304-305) while the same run's startup had resolved fine. On a
  # successful merge PROJECT_PATH is already restored to this same root;
  # non-selfmod runs take the :- fallback unchanged.
  local _status_root="${SELFMOD_ORIGINAL_PROJECT_PATH:-$PROJECT_PATH}"
  (cd "$_status_root" && python3 "$LOOP_STATUS_SCRIPT" 2>&1) || true

  if [[ "$stop_reason" == "all-shipped" ]]; then
    # Batch-end gate: run the suite once before the master is done (SP1)
    if [[ -n "$runtime_dir" ]]; then
      invoke_batch_gate "$PROJECT_PATH" "$runtime_dir"
    fi
    echo "[ilk] ALL SHIPPED — nothing to run. Do NOT relaunch."
  elif [[ "$stop_reason" == "shipped-unproven" ]]; then
    echo "[ilk] SHIPPED WITHOUT PROOF — ${UNPROVEN_SUBPLANS:-unknown}. No gate ran for these; the ship claim is unverified. Do NOT relaunch; this needs a human." >&2
  elif [[ "$stop_reason" == "blocked-no-runnable" ]]; then
    local blocked_count
    blocked_count=$(echo "$BLOCKED_SUBPLANS" | wc -w | tr -d ' ')
    echo "[ilk] BLOCKED — ${blocked_count} sub-plan(s) parked for a human, 0 runnable: ${BLOCKED_SUBPLANS}. Do NOT relaunch."
  fi

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
    rm -f "${runtime_dir}/running.pid"
  fi
}

# Dot-source guard: when ILK_DOTSOURCE_ONLY=1, functions are defined but
# main() does not execute. Lets tests source this script to call internal
# functions without starting the iteration loop.
if [[ "${ILK_DOTSOURCE_ONLY:-}" != "1" ]]; then
  main "$@"
fi
