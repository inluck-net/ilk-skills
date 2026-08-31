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
#   --install-path       also install claude-worker + claude-worker-switch onto PATH
#   --only-path          install ONLY the PATH entries (skip skills)
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
#   --install-path       also install claude-worker + claude-worker-switch onto PATH (in addition
#                        to the normal skill/command install)
#   --only-path          install ONLY the PATH entries; skip
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

# Insert-or-replace a delimited block in a file (idempotent).
#   upsert_block <file> <start-marker> <end-marker> <block-content>
# If the markers already exist, replaces the block between them.
# If the markers don't exist, appends the block to the file.
# Creates the file if absent.
upsert_block() {
  local file="$1" start="$2" end="$3" content="$4"
  mkdir -p "$(dirname "$file")"
  if [[ -f "$file" ]] && grep -qF "$start" "$file"; then
    # Replace block between markers (inclusive) in-place.
    # Use a temp file to avoid BSD sed / GNU sed portability issues
    # with multiline in-place editing.  Pass the (multi-line) replacement
    # block via a file read with getline rather than `awk -v`: BSD awk
    # (macOS) rejects newlines in a -v assignment ("newline in string"),
    # whereas GNU awk tolerates them.
    local tmp blkfile
    tmp="$(mktemp)"
    blkfile="$(mktemp)"
    printf '%s\n' "$content" > "$blkfile"
    awk -v s="$start" -v e="$end" -v blkfile="$blkfile" '
      $0 == s { while ((getline line < blkfile) > 0) print line; close(blkfile); skipping=1; next }
      $0 == e { skipping=0; next }
      skipping { next }
      { print }
    ' "$file" > "$tmp"
    mv -- "$tmp" "$file"
    rm -f -- "$blkfile"
  else
    # Append block (with a leading blank line if file is non-empty).
    if [[ -s "$file" ]]; then
      echo >> "$file"
    fi
    echo "$content" >> "$file"
  fi
}

# Strip a delimited block from a file (inclusive of markers).
# Leaves surrounding content byte-for-byte intact.
strip_block() {
  local file="$1" start="$2" end="$3"
  if [[ ! -f "$file" ]] || ! grep -qF "$start" "$file"; then
    return 0
  fi
  local tmp
  tmp="$(mktemp)"
  awk -v s="$start" -v e="$end" '
    $0 == s { skipping=1; next }
    $0 == e { skipping=0; next }
    skipping { next }
    { print }
  ' "$file" > "$tmp"
  mv -- "$tmp" "$file"
}

# Reconcile the auto-plan managed block into each host agent's
# user-global instructions.  Respects dry-run, only-cursor/claude/codex,
# and the committed preference.
reconcile_auto_plan() {
  local pref
  pref="$(read_auto_plan_pref)"
  local mode="DRY-RUN"
  [[ $apply -eq 1 ]] && mode="APPLY"

  echo "=== auto-plan reconcile ($mode) ==="
  echo "preference: auto_use_ilk_plan=${pref}"

  local block=""
  if [[ "$pref" == "true" ]]; then
    block="$(render_auto_plan_block)" || return 1
  fi

  local start_marker="<!-- ilk:auto-plan:start -->"
  local end_marker="<!-- ilk:auto-plan:end -->"

  # Determine which home dirs to reconcile into.
  local -a homes=()
  local -a home_names=()
  if [[ $any_only -eq 0 || $only_cursor -eq 1 ]]; then
    homes+=("$HOME")
    home_names+=("Cursor")
  fi
  if [[ $any_only -eq 0 || $only_claude -eq 1 ]]; then
    if [[ -n "$claude_home" ]]; then
      homes+=("$claude_home")
      home_names+=("Claude Code [$claude_home]")
    else
      homes+=("$HOME")
      home_names+=("Claude Code")
    fi
  fi
  if [[ $any_only -eq 0 || $only_codex -eq 1 ]]; then
    homes+=("$HOME")
    home_names+=("Codex")
  fi

  # Reconcile into shared files (CLAUDE.md, AGENTS.md).
  local -a shared_files=()
  local -a shared_labels=()
  for i in "${!homes[@]}"; do
    local name="${home_names[$i]}"
    case "$name" in
      Cursor*)   ;; # Cursor uses .mdc, not a shared file
      Claude*)   shared_files+=("${homes[$i]}/.claude/CLAUDE.md"); shared_labels+=("$name") ;;
      Codex*)    shared_files+=("${homes[$i]}/.codex/AGENTS.md"); shared_labels+=("$name") ;;
    esac
  done

  for i in "${!shared_files[@]}"; do
    local f="${shared_files[$i]}"
    local label="${shared_labels[$i]}"
    if [[ "$pref" == "true" ]]; then
      if [[ $apply -eq 1 ]]; then
        upsert_block "$f" "$start_marker" "$end_marker" "$block"
        echo "[ok] reconciled block -> $f ($label)"
      else
        echo "(dry-run: would reconcile block -> $f ($label))"
      fi
    else
      if [[ -f "$f" ]] && grep -qF "$start_marker" "$f"; then
        if [[ $apply -eq 1 ]]; then
          strip_block "$f" "$start_marker" "$end_marker"
          echo "[ok] removed block from $f ($label)"
        else
          echo "(dry-run: would remove block from $f ($label))"
        fi
      fi
    fi
  done

  # Reconcile dedicated .mdc file for Cursor.
  if [[ $any_only -eq 0 || $only_cursor -eq 1 ]]; then
    local mdc="$HOME/.cursor/rules/ilk-auto-plan.mdc"
    if [[ "$pref" == "true" ]]; then
      if [[ $apply -eq 1 ]]; then
        mkdir -p "$(dirname "$mdc")"
        cp -- "$REPO_ROOT/conventions/auto-plan-routing.md" "$mdc"
        echo "[ok] wrote $mdc (Cursor)"
      else
        echo "(dry-run: would write $mdc (Cursor))"
      fi
    else
      if [[ -f "$mdc" ]]; then
        if [[ $apply -eq 1 ]]; then
          rm -f -- "$mdc"
          echo "[ok] deleted $mdc (Cursor)"
        else
          echo "(dry-run: would delete $mdc (Cursor))"
        fi
      fi
    fi
  fi
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
declare -a TARGET_HOOKS=()

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
    TARGET_HOOKS+=("$claude_home/hooks")
  else
    TARGET_NAMES+=("Claude Code")
    TARGET_SKILLS+=("$HOME/.claude/skills")
    TARGET_COMMANDS+=("$HOME/.claude/commands")
    TARGET_HOOKS+=("$HOME/.claude/hooks")
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

HOOKS_SRC="$REPO_ROOT/hooks"
HOOK_FILES=()
if [[ -d "$HOOKS_SRC" ]]; then
  while IFS= read -r line; do
    HOOK_FILES+=("$line")
  done < <(find "$HOOKS_SRC" -maxdepth 1 -mindepth 1 -type f -name '*.sh' -exec basename {} \; | sort)
fi

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
CLAUDE_WORKER_SWITCH_SRC="$REPO_ROOT/tools/claude-worker/switch.sh"

# Every command this installer puts on PATH, as "name=source" pairs. Adding a
# command here is all it takes for a fresh host to get it via --only-path.
PATH_ENTRIES=(
  "claude-worker=$CLAUDE_WORKER_SRC"
  "claude-worker-switch=$CLAUDE_WORKER_SWITCH_SRC"
)

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

# Install a single PATH entry: <bin_dir>/<name> -> <source>. Idempotent.
install_one_path_entry() {
  local bin_dir="$1" name="$2" src="$3"
  local link="$bin_dir/$name"

  if [[ ! -f "$src" ]]; then
    echo "error: $name source not found: $src" >&2
    return 1
  fi

  local entry_mode="DRY-RUN"
  [[ $apply -eq 1 ]] && entry_mode="APPLY"
  echo "=== $name PATH entry ($entry_mode) ==="
  echo "source:    $src"
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
    if [[ "$current_abs" == "$src" ]]; then
      echo "noop: $link already points to the correct source"
    else
      echo "action:  replace-stale-link"
      rm -f -- "$link"
      create_path_entry "$link" "$src"
      echo "updated: $link"
    fi
  elif [[ -f "$link" ]]; then
    # Regular file — could be a previous copy (Windows fallback).  Compare
    # contents to decide whether we need to refresh.
    if cmp -s "$src" "$link"; then
      echo "noop: $link already has the correct content"
    else
      if [[ $force -eq 1 ]]; then
        local stamp backup
        stamp="$(date +%Y%m%d-%H%M%S)"
        backup="${link}.pre-ilk-${stamp}"
        mv -- "$link" "$backup"
        create_path_entry "$link" "$src"
        echo "backed up + updated: $link"
      else
        echo "BLOCKED: $link exists and is not a symlink (re-run with --force to back up)" >&2
        return 1
      fi
    fi
  else
    echo "action:  create"
    mkdir -p "$bin_dir"
    create_path_entry "$link" "$src"
    echo "created: $link"
  fi
}

# Install every PATH entry, then warn once if the bin dir is not on PATH.
install_path_entry() {
  local bin_dir="$1"
  local rc=0 entry name src

  for entry in "${PATH_ENTRIES[@]}"; do
    name="${entry%%=*}"
    src="${entry#*=}"
    install_one_path_entry "$bin_dir" "$name" "$src" || rc=1
    echo
  done

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

  return $rc
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

# Hooks are Claude Code only — one entry per discovered hook.
for hook_name in "${HOOK_FILES[@]}"; do
  for i in "${!TARGET_HOOKS[@]}"; do
    link="${TARGET_HOOKS[$i]}/$hook_name"
    source="$HOOKS_SRC/$hook_name"
    action="$(plan_link "$link" "$source")"
    PLAN_TARGET+=("hooks")
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
echo "hooks found:    ${#HOOK_FILES[@]} (*.sh)"
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

# --- reconcile settings.json hooks block ------------------------------------
# Ensures guardrail hooks are registered in settings.json without disturbing
# foreign entries.  Each hook is declared once in HOOK_TABLE below; adding a
# row is the only edit needed to register a new hook (AC-1).
reconcile_hooks_settings() {
  [[ ${#TARGET_HOOKS[@]} -gt 0 ]] || return 0

  local mode="DRY-RUN"
  [[ $apply -eq 1 ]] && mode="APPLY"

  echo
  echo "=== hooks settings.json reconcile ($mode) ==="

  # Declaration table — each row: "filename:matcher:hosts"
  # hosts = "all" (both interactive and worker) or "worker" (worker only).
  local HOOK_TABLE=(
    "no-full-suite.sh:Bash:all"
    "no-duplicate-read.sh:Read:worker"
  )

  # Serialise the table for the Python block.
  local hook_cmds=() matchers=() hosts=()
  for entry in "${HOOK_TABLE[@]}"; do
    IFS=: read -r cmd mtr hst <<< "$entry"
    hook_cmds+=("$cmd")
    matchers+=("$mtr")
    hosts+=("$hst")
  done
  local hook_cmds_json matchers_json hosts_json
  hook_cmds_json=$(printf '%s\n' "${hook_cmds[@]}" | python3 -c "import sys,json; print(json.dumps([l.rstrip() for l in sys.stdin]))")
  matchers_json=$(printf '%s\n' "${matchers[@]}" | python3 -c "import sys,json; print(json.dumps([l.rstrip() for l in sys.stdin]))")
  hosts_json=$(printf '%s\n' "${hosts[@]}" | python3 -c "import sys,json; print(json.dumps([l.rstrip() for l in sys.stdin]))")

  for hooks_dir in "${TARGET_HOOKS[@]}"; do
    local settings="${hooks_dir%/hooks}/settings.json"
    # Detect host type from settings path.
    local host_type="interactive"
    if [[ "$settings" == *".claude-worker/"* ]]; then
      host_type="worker"
    fi

    python3 - "$settings" "$hook_cmds_json" "$matchers_json" "$hosts_json" "$host_type" "$apply" <<'PYEOF'
import json, os, sys

settings_path = sys.argv[1]
hook_cmds = json.loads(sys.argv[2])
matchers = json.loads(sys.argv[3])
hosts = json.loads(sys.argv[4])
host_type = sys.argv[5]
dry_run = sys.argv[6] != "1"

if os.path.isfile(settings_path):
    with open(settings_path) as f:
        settings = json.load(f)
else:
    settings = {}

hooks = settings.get("hooks", {})
pre_tool = hooks.get("PreToolUse", [])
if not pre_tool:
    pre_tool = []
    hooks["PreToolUse"] = pre_tool

entries_by_matcher = {e.get("matcher"): e for e in pre_tool}
any_change = False

for hook_cmd, matcher, hook_hosts in zip(hook_cmds, matchers, hosts):
    # Skip hooks scoped to a different host type (AC-3).
    if hook_hosts == "worker" and host_type != "worker":
        continue

    hook_path = os.path.join(os.path.dirname(settings_path), "hooks", hook_cmd)
    entry = entries_by_matcher.get(matcher)
    if entry is None:
        entry = {"matcher": matcher, "hooks": []}
        pre_tool.append(entry)
        entries_by_matcher[matcher] = entry

    existing = entry.get("hooks", [])
    if any(h.get("command") == hook_path for h in existing):
        continue

    kept = [h for h in existing if h.get("command") != hook_path]
    kept.append({"type": "command", "command": hook_path})
    entry["hooks"] = kept
    any_change = True

hooks["PreToolUse"] = pre_tool
settings["hooks"] = hooks

if dry_run:
    if any_change:
        print("would update: {}".format(settings_path))
    else:
        print("skip: {} already up to date".format(settings_path))
else:
    if any_change:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        print("updated: {}".format(settings_path))
    else:
        print("skip: {} already up to date".format(settings_path))
PYEOF
  done
}

# Reconcile hooks into settings.json (Claude Code only).  Runs in both
# dry-run and apply modes so the user sees what would change.
reconcile_hooks_settings

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

# Auto-plan managed block reconcile (always runs in the normal --apply path;
# idempotent — no-op when the preference is off and no stale block exists).
echo
reconcile_auto_plan

echo
echo "Done."
