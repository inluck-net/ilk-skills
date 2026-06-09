#!/usr/bin/env bash
# Install (or update) the ilk-skills suite on macOS / Linux by creating
# symlinks from the user's Cursor / Claude Code directories into this
# repository.
#
# Single source of truth lives in this repo. The install script makes
# Cursor and Claude Code see the latest version by linking:
#
#   ~/.cursor/skills/<name>   ->  <repo>/skills/<name>
#   ~/.cursor/commands/<file> ->  <repo>/commands/<file>
#   ~/.claude/skills/<name>   ->  <repo>/skills/<name>
#   ~/.claude/commands/<file> ->  <repo>/commands/<file>
#   ~/.codex/skills/<name>    ->  <repo>/skills/<name>
#   ~/.codex/commands/<file>  ->  <repo>/commands/<file>
#
# Default mode is dry-run: prints what would happen but touches nothing.
# Pass --apply to execute.
#
# PATH entry (opt-in):
#   --install-path       also install claude-worker onto PATH
#   --only-path          install ONLY the claude-worker PATH entry (skip skills)
#   --path-bin-dir <dir> target bin directory (default: ~/.local/bin)
#
# macOS entry points installed via directory symlinks (all scripts
# inside each skill directory are automatically reachable):
#   ~/.cursor/skills/ilk-launcher/scripts/launch.sh
#   ~/.cursor/skills/ilk-launcher/scripts/stop.sh
#   ~/.cursor/skills/ilk-loop/scripts/run_ilk_loop_claude.sh
#   ~/.cursor/skills/ilk-watchdog/scripts/watchdog.sh
#   ~/.cursor/skills/ilk-watchdog/scripts/stop_watchdog.sh
#   ~/.cursor/skills/ilk-loop/scripts/_stream_json_render.py
#
# Flags:
#   --apply              actually create / refresh links
#   --dry-run            preview only (the default; accepted for symmetry)
#   --only-cursor        install only to ~/.cursor/
#   --only-claude        install only to the Claude Code home
#   --only-codex         install only to ~/.codex/
#   --claude-home <dir>  use <dir> as the Claude Code home instead of
#                        ~/.claude (e.g. a worker home ~/.claude-worker);
#                        targets <dir>/skills, <dir>/commands, <dir>/tools
#   --force              back up real (non-symlink) targets to
#                        <link>.pre-ilk-<timestamp> before linking
#   --install-path       also install claude-worker onto PATH (in addition
#                        to the normal skill/command install)
#   --only-path          install ONLY the claude-worker PATH entry; skip
#                        all skill/command linking
#   --path-bin-dir <dir> target bin directory for the PATH entry
#                        (default: ~/.local/bin)
#   --auto-use-ilk-plan  set auto_use_ilk_plan: true in conventions/config.yml
#                        (the git-propagated opt-in for auto-plan routing)
#   --only-auto-plan     reconcile ONLY the auto-plan managed block into host
#                        agent files (no skill/command linking); used by
#                        /ilk-upgrade after git pull
#
# Idempotent: re-running --apply just re-points stale symlinks (e.g.
# if you moved the repo) and is otherwise a no-op.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$REPO_ROOT/skills"
COMMANDS_SRC="$REPO_ROOT/commands"

apply=0
only_cursor=0
only_claude=0
only_codex=0
force=0
claude_home=""
install_path=0
only_path=0
path_bin_dir=""
auto_use_ilk_plan=0
only_auto_plan=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)        apply=1 ;;
    --dry-run)      apply=0 ;;
    --only-cursor)  only_cursor=1 ;;
    --only-claude)  only_claude=1 ;;
    --only-codex)   only_codex=1 ;;
    --force)        force=1 ;;
    --install-path) install_path=1 ;;
    --only-path)    only_path=1 ;;
    --auto-use-ilk-plan) auto_use_ilk_plan=1 ;;
    --only-auto-plan)    only_auto_plan=1 ;;
    --claude-home)
      shift
      if [[ $# -eq 0 ]]; then
        echo "error: --claude-home requires a directory argument" >&2
        exit 2
      fi
      claude_home="$1"
      ;;
    --claude-home=*)
      claude_home="${1#--claude-home=}"
      ;;
    --path-bin-dir)
      shift
      if [[ $# -eq 0 ]]; then
        echo "error: --path-bin-dir requires a directory argument" >&2
        exit 2
      fi
      path_bin_dir="$1"
      ;;
    --path-bin-dir=*)
      path_bin_dir="${1#--path-bin-dir=}"
      ;;
    -h|--help)
      sed -n '2,50p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown flag: $1" >&2
      exit 2
      ;;
  esac
  shift
done

# Normalize a custom Claude home: expand a leading ~ and make relative
# paths absolute. Conservative — does NOT require the directory to exist
# yet, so a dry-run can preview a not-yet-created worker home.
if [[ -n "$claude_home" ]]; then
  case "$claude_home" in
    "~")   claude_home="$HOME" ;;
    "~/"*) claude_home="$HOME/${claude_home#\~/}" ;;
  esac
  case "$claude_home" in
    /*) ;;
    *)  claude_home="$(pwd)/$claude_home" ;;
  esac
fi

# --- auto-plan routing helpers ------------------------------------------------

# Read the auto_use_ilk_plan boolean from conventions/config.yml.
# Prints "true" or "false"; defaults to "false" if the key is absent.
read_auto_plan_pref() {
  local cfg="$REPO_ROOT/conventions/config.yml"
  if [[ -f "$cfg" ]] && grep -q '^auto_use_ilk_plan:\s*true' "$cfg"; then
    echo "true"
  else
    echo "false"
  fi
}

# Set auto_use_ilk_plan in conventions/config.yml (idempotent).
# Requires the file to already exist (created in step 0).
set_auto_plan_pref() {
  local cfg="$REPO_ROOT/conventions/config.yml"
  local val="$1"  # true or false
  if [[ ! -f "$cfg" ]]; then
    echo "error: conventions/config.yml not found" >&2
    return 1
  fi
  # Portable sed: GNU and BSD both accept this form for a simple substitution.
  sed -i.bak "s/^auto_use_ilk_plan:.*/auto_use_ilk_plan: ${val}/" "$cfg"
  rm -f "${cfg}.bak"
}

# Render the managed block content: marker-wrapped contents of
# conventions/auto-plan-routing.md.  Prints to stdout.
render_auto_plan_block() {
  local snippet="$REPO_ROOT/conventions/auto-plan-routing.md"
  if [[ ! -f "$snippet" ]]; then
    echo "error: conventions/auto-plan-routing.md not found" >&2
    return 1
  fi
  echo "<!-- ilk:auto-plan:start -->"
  cat "$snippet"
  echo "<!-- ilk:auto-plan:end -->"
}

if [[ ! -d "$SKILLS_SRC" ]]; then
  echo "error: cannot find skills/ under repo root: $REPO_ROOT" >&2
  exit 2
fi

# --- macOS dependency check -------------------------------------------------
if [[ "$(uname -s)" == "Darwin" ]] && ! command -v gtimeout >/dev/null 2>&1; then
  echo "Warning: gtimeout not found. The ilk-loop bash runner uses it for" >&2
  echo "  iteration timeouts. Install with: brew install coreutils" >&2
fi

# --- target environments ----------------------------------------------------

declare -a TARGET_NAMES=()
declare -a TARGET_SKILLS=()
declare -a TARGET_COMMANDS=()

# An --only-X flag selects exactly one target; when none are set, all
# targets are included.
any_only=$(( only_cursor + only_claude + only_codex ))

if [[ $any_only -eq 0 || $only_cursor -eq 1 ]]; then
  TARGET_NAMES+=("Cursor")
  TARGET_SKILLS+=("$HOME/.cursor/skills")
  TARGET_COMMANDS+=("$HOME/.cursor/commands")
fi
if [[ $any_only -eq 0 || $only_claude -eq 1 ]]; then
  if [[ -n "$claude_home" ]]; then
    TARGET_NAMES+=("Claude Code [$claude_home]")
    TARGET_SKILLS+=("$claude_home/skills")
    TARGET_COMMANDS+=("$claude_home/commands")
  else
    TARGET_NAMES+=("Claude Code")
    TARGET_SKILLS+=("$HOME/.claude/skills")
    TARGET_COMMANDS+=("$HOME/.claude/commands")
  fi
fi
if [[ $any_only -eq 0 || $only_codex -eq 1 ]]; then
  TARGET_NAMES+=("Codex")
  TARGET_SKILLS+=("$HOME/.codex/skills")
  TARGET_COMMANDS+=("$HOME/.codex/commands")
fi

# --- discovery --------------------------------------------------------------

# macOS ships bash 3.2 (no `mapfile`), so build the arrays with a
# portable while-read loop. The IFS reset + `-r` keeps names with
# spaces / backslashes intact, though `ilk-*` directories don't have
# either today.
SKILL_NAMES=()
while IFS= read -r line; do
  SKILL_NAMES+=("$line")
done < <(find "$SKILLS_SRC" -maxdepth 1 -mindepth 1 -type d -name 'ilk-*' -exec basename {} \; | sort)

COMMAND_FILES=()
while IFS= read -r line; do
  COMMAND_FILES+=("$line")
done < <(find "$COMMANDS_SRC" -maxdepth 1 -mindepth 1 -type f -name 'ilk*' -exec basename {} \; | sort)

# --- planning ---------------------------------------------------------------

# Returns one of: skip-correct, replace-stale-link, replace-real,
# blocked-real, create
plan_link() {
  local link="$1" source="$2"
  if [[ ! -e "$link" && ! -L "$link" ]]; then
    echo create
    return
  fi
  if [[ -L "$link" ]]; then
    local current
    current="$(readlink "$link")"
    # readlink can be relative; normalise via cd
    local current_abs
    if [[ "$current" = /* ]]; then
      current_abs="$current"
    else
      current_abs="$(cd "$(dirname "$link")" && cd "$(dirname "$current")" 2>/dev/null && pwd)/$(basename "$current")"
    fi
    if [[ "$current_abs" == "$source" ]]; then
      echo skip-correct
    else
      echo replace-stale-link
    fi
    return
  fi
  if [[ $force -eq 1 ]]; then
    echo replace-real
  else
    echo blocked-real
  fi
}

apply_action() {
  local action="$1" link="$2" source="$3"
  case "$action" in
    skip-correct) echo noop; return ;;
    replace-stale-link)
      rm -rf -- "$link"
      ln -sfn "$source" "$link"
      echo symlink
      ;;
    replace-real)
      local stamp backup
      stamp="$(date +%Y%m%d-%H%M%S)"
      backup="${link}.pre-ilk-${stamp}"
      mv -- "$link" "$backup"
      ln -sfn "$source" "$link"
      echo "backed-up:$backup"
      ;;
    blocked-real) echo blocked; return ;;
    create)
      mkdir -p "$(dirname "$link")"
      ln -sfn "$source" "$link"
      echo symlink
      ;;
  esac
}

# --- PATH entry for claude-worker -------------------------------------------
# Creates a symlink (or re-points a stale one) at <bin-dir>/claude-worker
# pointing to tools/claude-worker/claude-worker.sh. Idempotent.

CLAUDE_WORKER_SRC="$REPO_ROOT/tools/claude-worker/claude-worker.sh"

# Create or replace the PATH entry (symlink preferred, copy as fallback for
# environments where ln -s silently copies, e.g. Windows Git Bash without
# Developer Mode).  Returns 0 on success, 1 on error.
create_path_entry() {
  local link="$1" source="$2"
  # Try symlink first; if the result is not an actual symlink (ln -s may
  # silently copy on some platforms), fall back to a plain copy.
  ln -sfn "$source" "$link"
  if [[ ! -L "$link" ]]; then
    cp -- "$source" "$link"
  fi
}

install_path_entry() {
  local bin_dir="$1"
  local link="$bin_dir/claude-worker"

  if [[ ! -f "$CLAUDE_WORKER_SRC" ]]; then
    echo "error: claude-worker source not found: $CLAUDE_WORKER_SRC" >&2
    return 1
  fi

  local entry_mode="DRY-RUN"
  [[ $apply -eq 1 ]] && entry_mode="APPLY"
  echo "=== claude-worker PATH entry ($entry_mode) ==="
  echo "source:    $CLAUDE_WORKER_SRC"
  echo "target:    $link"

  if [[ $apply -eq 0 ]]; then
    echo "(dry-run: not writing)"
    return 0
  fi

  # Check current state: already correct (symlink or identical copy)?
  if [[ -L "$link" ]]; then
    local current current_abs
    current="$(readlink "$link")"
    if [[ "$current" = /* ]]; then
      current_abs="$current"
    else
      current_abs="$(cd "$(dirname "$link")" && cd "$(dirname "$current")" 2>/dev/null && pwd)/$(basename "$current")"
    fi
    if [[ "$current_abs" == "$CLAUDE_WORKER_SRC" ]]; then
      echo "noop: $link already points to the correct source"
    else
      echo "action:  replace-stale-link"
      rm -f -- "$link"
      create_path_entry "$link" "$CLAUDE_WORKER_SRC"
      echo "updated: $link"
    fi
  elif [[ -f "$link" ]]; then
    # Regular file — could be a previous copy (Windows fallback).  Compare
    # contents to decide whether we need to refresh.
    if cmp -s "$CLAUDE_WORKER_SRC" "$link"; then
      echo "noop: $link already has the correct content"
    else
      if [[ $force -eq 1 ]]; then
        local stamp backup
        stamp="$(date +%Y%m%d-%H%M%S)"
        backup="${link}.pre-ilk-${stamp}"
        mv -- "$link" "$backup"
        create_path_entry "$link" "$CLAUDE_WORKER_SRC"
        echo "backed up + updated: $link"
      else
        echo "BLOCKED: $link exists and is not a symlink (re-run with --force to back up)" >&2
        return 1
      fi
    fi
  else
    echo "action:  create"
    mkdir -p "$bin_dir"
    create_path_entry "$link" "$CLAUDE_WORKER_SRC"
    echo "created: $link"
  fi

  # Warn if bin_dir is not on PATH
  local on_path=0
  local IFS=':'
  for dir in $PATH; do
    # Normalize trailing slash for comparison
    dir="${dir%/}"
    bin_dir_norm="${bin_dir%/}"
    if [[ "$dir" == "$bin_dir_norm" ]]; then
      on_path=1
      break
    fi
  done
  if [[ $on_path -eq 0 ]]; then
    echo
    echo "WARNING: $bin_dir is not on your PATH."
    echo "Add it by running:"
    echo
    echo "  export PATH=\"$bin_dir:\$PATH\""
    echo
    echo "To make it permanent, add that line to your shell rc file (~/.bashrc, ~/.zshrc, etc.)."
  fi
}

# Default bin dir for PATH entry
if [[ -z "$path_bin_dir" ]]; then
  path_bin_dir="$HOME/.local/bin"
fi

# Resolve path_bin_dir: expand ~ and make relative paths absolute
case "$path_bin_dir" in
  "~")   path_bin_dir="$HOME" ;;
  "~/"*) path_bin_dir="$HOME/${path_bin_dir#\~/}" ;;
esac
case "$path_bin_dir" in
  /*) ;;
  *)  path_bin_dir="$(pwd)/$path_bin_dir" ;;
esac

# --only-path: install ONLY the PATH entry, skip all skill/command linking
if [[ $only_path -eq 1 ]]; then
  install_path_entry "$path_bin_dir"
  exit $?
fi

# --auto-use-ilk-plan: set the committed preference to true (then continue
# to the normal plan/apply flow so the block reconcile happens in the same run).
if [[ $auto_use_ilk_plan -eq 1 ]]; then
  if [[ $apply -eq 1 ]]; then
    set_auto_plan_pref "true"
    echo "Set auto_use_ilk_plan: true in conventions/config.yml"
  else
    echo "(dry-run: would set auto_use_ilk_plan: true in conventions/config.yml)"
  fi
fi

# --only-auto-plan: reconcile ONLY the auto-plan managed block (skip all
# skill/command linking).  Used by /ilk-upgrade to refresh the block after
# a git pull without touching symlinks.
if [[ $only_auto_plan -eq 1 ]]; then
  reconcile_auto_plan
  exit $?
fi

# --- build plan -------------------------------------------------------------

declare -a PLAN_TARGET=()
declare -a PLAN_LINK=()
declare -a PLAN_SOURCE=()
declare -a PLAN_ACTION=()

for i in "${!TARGET_NAMES[@]}"; do
  for name in "${SKILL_NAMES[@]}"; do
    link="${TARGET_SKILLS[$i]}/$name"
    source="$SKILLS_SRC/$name"
    action="$(plan_link "$link" "$source")"
    PLAN_TARGET+=("${TARGET_NAMES[$i]}")
    PLAN_LINK+=("$link")
    PLAN_SOURCE+=("$source")
    PLAN_ACTION+=("$action")
  done
  for f in "${COMMAND_FILES[@]}"; do
    link="${TARGET_COMMANDS[$i]}/$f"
    source="$COMMANDS_SRC/$f"
    action="$(plan_link "$link" "$source")"
    PLAN_TARGET+=("${TARGET_NAMES[$i]}")
    PLAN_LINK+=("$link")
    PLAN_SOURCE+=("$source")
    PLAN_ACTION+=("$action")
  done
done

# --- print plan -------------------------------------------------------------

mode="DRY-RUN"
[[ $apply -eq 1 ]] && mode="APPLY"

echo "=== ilk-skills install ($mode) ==="
echo "repo:           $REPO_ROOT"
[[ -n "$claude_home" ]] && echo "claude home:    $claude_home (custom)"
echo "skills found:   ${#SKILL_NAMES[@]} (ilk-*)"
echo "commands found: ${#COMMAND_FILES[@]} (ilk*)"
printf 'targets:        '
printf '%s ' "${TARGET_NAMES[@]}"
echo
# bash 3.2 compatibility: count actions by dedup + grep -c instead of
# `declare -A`. The action set is small and bounded; the few extra
# greps are not measurable next to filesystem syscalls.
printf 'actions:        '
if [[ ${#PLAN_ACTION[@]} -gt 0 ]]; then
  while IFS= read -r k; do
    [[ -z "$k" ]] && continue
    cnt=$(printf '%s\n' "${PLAN_ACTION[@]}" | grep -c -x "$k" || true)
    printf '%s=%s ' "$k" "$cnt"
  done < <(printf '%s\n' "${PLAN_ACTION[@]}" | sort -u)
fi
echo
echo

printf '%-12s %-22s %-7s %s\n' "TARGET" "ACTION" "" "LINK"
for i in "${!PLAN_LINK[@]}"; do
  printf '%-12s %-22s %-7s %s\n' "${PLAN_TARGET[$i]}" "${PLAN_ACTION[$i]}" "" "${PLAN_LINK[$i]}"
done

# --- tools ------------------------------------------------------------------
# tools/migration is symlinked so operators can run migrate_project_runtime_dirs.py
# (and other migration tools) from any path without needing to know the repo location.
tools_migration_link() {
  local target_skills_dir="$1"
  local link="${target_skills_dir}/../tools/migration"
  local source="$REPO_ROOT/tools/migration"
  mkdir -p "$(dirname "$link")"
  ln -sfn "$source" "$link"
  printf '[ok] %-12s %s\n' "symlink" "$link"
}

for i in "${!TARGET_NAMES[@]}"; do
  tools_migration_link "${TARGET_SKILLS[$i]}"
done

# blocked summary
blocked_any=0
for action in "${PLAN_ACTION[@]}"; do
  if [[ "$action" == "blocked-real" ]]; then blocked_any=1; break; fi
done
if [[ $blocked_any -eq 1 ]]; then
  echo
  echo "BLOCKED on real content at the paths marked 'blocked-real' above (would clobber non-symlink dirs/files)."
  echo "Re-run with --force to back them up to <link>.pre-ilk-<timestamp> before linking."
fi

if [[ $apply -eq 0 ]]; then
  echo
  echo "Dry-run complete. Re-run with --apply to install."
  exit 0
fi

if [[ $blocked_any -eq 1 && $force -eq 0 ]]; then
  echo
  echo "Aborting: blocked entries above. Re-run with --force or remove the targets manually."
  exit 4
fi

# --- execute ----------------------------------------------------------------

# bash 3.2 compatibility: collect outcome strings then count with grep,
# instead of a `declare -A` associative array.
RESULT_KEYS=()
for i in "${!PLAN_LINK[@]}"; do
  action="${PLAN_ACTION[$i]}"
  link="${PLAN_LINK[$i]}"
  source="${PLAN_SOURCE[$i]}"
  if [[ "$action" == "skip-correct" ]]; then
    RESULT_KEYS+=("noop")
    continue
  fi
  if outcome="$(apply_action "$action" "$link" "$source")"; then
    key="$outcome"
    [[ "$outcome" == backed-up:* ]] && key="backed-up"
    RESULT_KEYS+=("$key")
    printf '[ok] %-12s %s\n' "$outcome" "$link"
  else
    RESULT_KEYS+=("error")
    printf '[ERR] %s\n' "$link" >&2
  fi
done

echo
printf 'Results:'
if [[ ${#RESULT_KEYS[@]} -gt 0 ]]; then
  while IFS= read -r k; do
    [[ -z "$k" ]] && continue
    cnt=$(printf '%s\n' "${RESULT_KEYS[@]}" | grep -c -x "$k" || true)
    printf ' %s=%s' "$k" "$cnt"
  done < <(printf '%s\n' "${RESULT_KEYS[@]}" | sort -u)
fi
echo

# Bootstrap projects.json from example. projects.json is gitignored
# (per-operator paths); seed on first install, never overwrite.
projects_json="$SKILLS_SRC/ilk-launcher/projects.json"
projects_example="$SKILLS_SRC/ilk-launcher/projects.example.json"
if [[ -f "$projects_example" && ! -f "$projects_json" ]]; then
  cp "$projects_example" "$projects_json"
  echo
  echo "Created: $projects_json (from projects.example.json)"
  echo "Edit it to point at your real projects before using launch.sh --all."
fi

# --install-path: also install the claude-worker PATH entry
if [[ $install_path -eq 1 ]]; then
  echo
  install_path_entry "$path_bin_dir"
fi

echo "Done."
