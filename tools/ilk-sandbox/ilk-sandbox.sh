#!/usr/bin/env bash
# =============================================================================
# ilk-sandbox.sh — launch a command in an isolated RSI sandbox environment
# =============================================================================
# Pins HOME, ILK_DATA_HOME, CLAUDE_CONFIG_DIR, ILK_SKILL_HOME, and
# ILK_SANDBOX together so the sandboxed process cannot write into the
# stable data home.  Foreground only — no --detach, --daemon or launchctl.
#
# Usage:
#   ilk-sandbox.sh [--root DIR] --worktree PATH [--] CMD...
#
# Sub-plan: a-sandbox-cannot-write-the-stable-home (MASTER-2026-10-02d)
# =============================================================================
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────

ROOT=""
WORKTREE=""
CMD=()

# ── Argument parsing ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="$2"
      shift 2
      ;;
    --worktree)
      WORKTREE="$2"
      shift 2
      ;;
    --detach|--daemon)
      echo "ilk-sandbox: --detach/--daemon is not allowed in a sandbox (foreground only)" >&2
      exit 2
      ;;
    --)
      shift
      CMD=("$@")
      break
      ;;
    -*)
      # Check for launchctl anywhere in args (should never reach here,
      # but defend in depth).
      if [[ "$1" == *launchctl* ]]; then
        echo "ilk-sandbox: launchctl is not allowed in a sandbox" >&2
        exit 2
      fi
      echo "ilk-sandbox: unknown option: $1" >&2
      exit 2
      ;;
    *)
      CMD=("$1")
      shift
      CMD+=("$@")
      break
      ;;
  esac
done

# ── Validate: worktree required ─────────────────────────────────────────────

if [[ -z "$WORKTREE" ]]; then
  echo "ilk-sandbox: --worktree is required" >&2
  exit 2
fi

# ── Validate: worktree is a git repo with skills/ ───────────────────────────

if [[ ! -d "$WORKTREE" ]]; then
  echo "ilk-sandbox: worktree does not exist: $WORKTREE" >&2
  exit 2
fi

_real_wt="$(cd -P "$WORKTREE" && pwd -P)" || {
  echo "ilk-sandbox: cannot resolve worktree: $WORKTREE" >&2
  exit 2
}

# Check it's a git work tree (has .git — file for worktrees, dir for main).
if [[ ! -e "$_real_wt/.git" ]]; then
  echo "ilk-sandbox: worktree is not a git repo: $_real_wt" >&2
  exit 2
fi

if [[ ! -d "$_real_wt/skills" ]]; then
  echo "ilk-sandbox: worktree has no skills/ directory: $_real_wt" >&2
  exit 2
fi

# ── Validate: worktree not under releases ────────────────────────────────────

_releases="${ILK_RELEASES_ROOT:-$HOME/.ilk/releases}"
_real_releases="$(cd -P "$_releases" && pwd -P 2>/dev/null)" || _real_releases=""

if [[ -n "$_real_releases" ]]; then
  if [[ "$_real_wt" == "$_real_releases" || "$_real_wt" == "$_real_releases"/* ]]; then
    echo "ilk-sandbox: worktree is under ILK_RELEASES_ROOT ($_releases): $_real_wt" >&2
    exit 2
  fi
fi

# ── Compute stable data home (before pinning) ───────────────────────────────
# Precedence: ILK_DATA_HOME → ILK_DATA_DIR → $HOME/.ilk-data
# (same as _ilk_data_dir.sh)

if [[ -n "${ILK_DATA_HOME:-}" ]]; then
  _stable_raw="$ILK_DATA_HOME"
elif [[ -n "${ILK_DATA_DIR:-}" ]]; then
  _stable_raw="$ILK_DATA_DIR"
else
  _stable_raw="$HOME/.ilk-data"
fi
STABLE_DATA_HOME="$(cd -P "$_stable_raw" && pwd -P 2>/dev/null)" || STABLE_DATA_HOME="$_stable_raw"

# ── Default root ─────────────────────────────────────────────────────────────

if [[ -z "$ROOT" ]]; then
  ROOT="$HOME/.ilk-sandbox"
fi

_real_root="$(cd -P "$ROOT" 2>/dev/null && pwd -P 2>/dev/null)" || _real_root="$ROOT"

# ── Validate: root is not HOME or inside stable data home ────────────────────

_real_home="$(cd -P "$HOME" && pwd -P 2>/dev/null)" || _real_home="$HOME"

if [[ "$_real_root" == "$_real_home" ]]; then
  echo "ilk-sandbox: root must not be HOME ($_real_home)" >&2
  exit 2
fi

if [[ -n "$STABLE_DATA_HOME" ]]; then
  if [[ "$_real_root" == "$STABLE_DATA_HOME" || "$_real_root" == "$STABLE_DATA_HOME"/* ]]; then
    echo "ilk-sandbox: root is inside the stable data home ($STABLE_DATA_HOME): $_real_root" >&2
    exit 2
  fi
fi

# ── Validate: command present ────────────────────────────────────────────────

if [[ ${#CMD[@]} -eq 0 ]]; then
  echo "ilk-sandbox: no command specified" >&2
  exit 2
fi

# ── Create sandbox directories ───────────────────────────────────────────────

mkdir -p "$ROOT/home" "$ROOT/data" "$ROOT/claude-config"

# ── Pin environment and exec ─────────────────────────────────────────────────

export HOME="$ROOT/home"
export ILK_DATA_HOME="$ROOT/data"
unset ILK_DATA_DIR 2>/dev/null || true
export CLAUDE_CONFIG_DIR="$ROOT/claude-config"
export ILK_SKILL_HOME="$_real_wt/skills"
export ILK_SANDBOX=1
export ILK_STABLE_DATA_HOME="$STABLE_DATA_HOME"

cd "$_real_wt"

exec "${CMD[@]}"