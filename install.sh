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
#
# Default mode is dry-run: prints what would happen but touches nothing.
# Pass --apply to execute.
#
# Flags:
#   --apply         actually create / refresh links
#   --only-cursor   skip ~/.claude/
#   --only-claude   skip ~/.cursor/
#   --force         back up real (non-symlink) targets to
#                   <link>.pre-ilk-<timestamp> before linking
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
force=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)        apply=1 ;;
    --only-cursor)  only_cursor=1 ;;
    --only-claude)  only_claude=1 ;;
    --force)        force=1 ;;
    -h|--help)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown flag: $1" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ ! -d "$SKILLS_SRC" ]]; then
  echo "error: cannot find skills/ under repo root: $REPO_ROOT" >&2
  exit 2
fi

# --- target environments ----------------------------------------------------

declare -a TARGET_NAMES=()
declare -a TARGET_SKILLS=()
declare -a TARGET_COMMANDS=()

if [[ $only_claude -eq 0 ]]; then
  TARGET_NAMES+=("Cursor")
  TARGET_SKILLS+=("$HOME/.cursor/skills")
  TARGET_COMMANDS+=("$HOME/.cursor/commands")
fi
if [[ $only_cursor -eq 0 ]]; then
  TARGET_NAMES+=("Claude Code")
  TARGET_SKILLS+=("$HOME/.claude/skills")
  TARGET_COMMANDS+=("$HOME/.claude/commands")
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

echo "Done."
