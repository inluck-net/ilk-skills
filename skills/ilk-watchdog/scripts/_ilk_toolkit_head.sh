#!/usr/bin/env bash
# _ilk_toolkit_head.sh — resolve toolkit HEAD and tree state, release-aware.
#
# Sourced by scheduler.sh and bounce_daemons.sh.  Provides:
#
#   ilk_toolkit_head <start_dir>
#     Prints the toolkit HEAD sha (bare, no prefix).  Walks up from
#     <start_dir> looking for .git (file or dir) or .ilk-release.json.
#     - git work tree: git rev-parse HEAD
#     - release dir:   sha from .ilk-release.json
#     - neither:       prints nothing (empty string)
#
#   ilk_toolkit_tree_state <start_dir>
#     Prints the tree state: "clean", "dirty", or "unknown".
#     - git work tree: clean/dirty from git status --porcelain
#     - release dir:   "clean" when the release root is not writable,
#                      else "dirty" (chmod 0o444 stands in for integrity)
#     - neither:       "unknown"
#
# The walk-up checks each ancestor for BOTH .git and .ilk-release.json
# (the same directory may contain both — release dirs extracted from a
# git archive won't, but a future layout might).  Whichever marker is
# found at the SHALLOWEST level wins.
#
# NEVER falls back to $PWD or any cwd-dependent resolution.
# Portable: bash 3.2+ (macOS default).

ilk_toolkit_head() {
  local start="${1:-.}"
  start="$(cd -P "$start" && pwd -P 2>/dev/null)" || { echo ""; return 0; }

  local found=""  # "git" or "release"
  local cur="$start"
  while true; do
    if [[ -e "$cur/.ilk-release.json" ]]; then
      found="release"
      break
    fi
    if [[ -e "$cur/.git" ]]; then
      found="git"
      break
    fi
    local parent
    parent="$(dirname "$cur")"
    if [[ "$parent" == "$cur" ]]; then break; fi  # reached /
    cur="$parent"
  done

  case "$found" in
    release)
      # Extract sha from the manifest.  Python is always available;
      # fall back to grep+sed for minimal environments.
      if command -v python3 >/dev/null 2>&1; then
        python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['sha'])" \
          "$cur/.ilk-release.json" 2>/dev/null || echo ""
      else
        # grep for the sha key — portable enough for a JSON manifest
        # written by ilk_release.py (always pretty-printed).
        sed -n 's/.*"sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
          "$cur/.ilk-release.json" 2>/dev/null | head -1
      fi
      ;;
    git)
      git -C "$cur" rev-parse HEAD 2>/dev/null || echo ""
      ;;
    *)
      echo ""
      ;;
  esac
}

ilk_toolkit_tree_state() {
  local start="${1:-.}"
  start="$(cd -P "$start" && pwd -P 2>/dev/null)" || { echo "unknown"; return 0; }

  local found=""  # "git" or "release"
  local cur="$start"
  while true; do
    if [[ -e "$cur/.ilk-release.json" ]]; then
      found="release"
      break
    fi
    if [[ -e "$cur/.git" ]]; then
      found="git"
      break
    fi
    local parent
    parent="$(dirname "$cur")"
    if [[ "$parent" == "$cur" ]]; then break; fi
    cur="$parent"
  done

  case "$found" in
    release)
      # A release dir extracted by ilk_release.py is chmod read-only.
      # Read-only + manifest stands in for "the files are the tag".
      if [[ ! -w "$cur" ]]; then
        echo "clean"
      else
        echo "dirty"
      fi
      ;;
    git)
      local porcelain
      porcelain="$(git -C "$cur" status --porcelain 2>/dev/null)" || { echo "unknown"; return 0; }
      if [[ -z "$porcelain" ]]; then
        echo "clean"
      else
        echo "dirty"
      fi
      ;;
    *)
      echo "unknown"
      ;;
  esac
}