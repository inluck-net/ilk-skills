# steer_hook.sh — crash-safe consume of operator interjections + pause gate.
#
# bash port of steer_hook.ps1. Same state root, same file protocol, same
# consume/pause/crash-recovery semantics.
#
# Usage (sourced from run_ilk_loop_claude.sh):
#   source "$(dirname "${BASH_SOURCE[0]}")/steer_hook.sh"
#   invoke_steer_hook "$PROJECT_KEY"
#   # $STEER_INTERJECTION_TEXT — text to prepend (empty string if none)
#   # $STEER_PAUSED            — 1 if pause.flag present, else 0
#
# Contract: see ilk-pocket handoff 2026-07-01-ilk-loop-steer-hook.md
# State root: ~/.ilk-data/projects/<key>/runtime/steer/
#   inbox.md             — append-only entries (uuid + timestamp)
#   pause.flag           — presence = pause
#   inbox.consumed.jsonl — hook appends {uuid, consumed_at} per consumed entry
#
# All reads/writes are utf-8 (bash printf never emits a BOM). The rename
# retries on a transient failure (producer may still hold the file open).

source "$(dirname "${BASH_SOURCE[0]}")/_ilk_data_dir.sh"

# Results are published as globals (bash functions cannot return a struct).
STEER_INTERJECTION_TEXT=""
STEER_PAUSED=0

invoke_steer_hook() {
  local project_key="$1"
  local max_retries="${2:-10}"
  local retry_delay_ms="${3:-100}"

  STEER_INTERJECTION_TEXT=""
  STEER_PAUSED=0

  local data_dir steer_dir
  data_dir="$(ilk_data_dir)"
  steer_dir="${data_dir}/projects/${project_key}/runtime/steer"

  # Ensure steer dir exists
  mkdir -p "$steer_dir"

  local inbox_path="${steer_dir}/inbox.md"
  local processing_path="${steer_dir}/inbox.processing.md"
  local consumed_path="${steer_dir}/inbox.consumed.jsonl"
  local pause_path="${steer_dir}/pause.flag"

  # ── Pause gate ──────────────────────────────────────────────────────
  if [[ -f "$pause_path" ]]; then
    STEER_PAUSED=1
    return 0
  fi

  # ── Crash recovery: reconcile leftover inbox.processing.md ─────────
  # A previous run crashed between rename and delete. Re-parse and inject
  # only uuids not yet in consumed.jsonl.
  if [[ -f "$processing_path" ]]; then
    _steer_consume "$processing_path" "$consumed_path"
    rm -f "$processing_path"
    return 0
  fi

  # ── Normal path: atomic rename inbox.md → inbox.processing.md ──────
  if [[ ! -f "$inbox_path" ]]; then
    return 0  # nothing to consume
  fi

  # Retry rename on transient failure (producer may have file open)
  local renamed=0 attempt
  for (( attempt = 0; attempt < max_retries; attempt++ )); do
    if mv "$inbox_path" "$processing_path" 2>/dev/null; then
      renamed=1
      break
    fi
    _steer_sleep_ms "$retry_delay_ms"
  done

  if [[ "$renamed" -ne 1 ]]; then
    # Could not rename after retries — skip this cycle (don't lose data)
    return 0
  fi

  _steer_consume "$processing_path" "$consumed_path"
  rm -f "$processing_path"
  return 0
}

# ── Internal helpers ──────────────────────────────────────────────────

# Parse a processing file, inject only fresh (not-yet-consumed) entries,
# and append a consumed record for each. Sets STEER_INTERJECTION_TEXT.
_steer_consume() {
  local processing_path="$1"
  local consumed_path="$2"
  [[ -f "$processing_path" ]] || return 0

  local fresh_file
  fresh_file="$(mktemp)"

  # awk does the parse + dedupe + join; it prints the joined interjection
  # text to stdout and the freshly-consumed uuids (one per line) to FRESH.
  local interjection
  interjection="$(awk -v CONSUMED="$consumed_path" -v FRESH="$fresh_file" '
    function trim(s) {
      gsub(/^[[:space:]]+/, "", s)
      gsub(/[[:space:]]+$/, "", s)
      return s
    }
    function flush(   i, uuid, text, has_marker, tmp) {
      if (nblk == 0) return
      uuid = ""
      has_marker = 0
      # branch 1: comment marker <!-- uuid: X -->
      for (i = 1; i <= nblk; i++) {
        if (blk[i] ~ /<!--[[:space:]]*uuid:[[:space:]]*[^[:space:]]+[[:space:]]*-->/) {
          has_marker = 1
          tmp = blk[i]
          sub(/.*<!--[[:space:]]*uuid:[[:space:]]*/, "", tmp)
          sub(/[[:space:]]*-->.*/, "", tmp)
          uuid = tmp
          break
        }
      }
      text = ""
      if (has_marker) {
        for (i = 1; i <= nblk; i++) {
          if (blk[i] ~ /<!--[[:space:]]*uuid:[[:space:]]*[^[:space:]]+[[:space:]]*-->/) continue
          text = text (text == "" ? "" : "\n") blk[i]
        }
      } else {
        # branch 2 (fallback): bare uuid: X — keep the whole block as text
        for (i = 1; i <= nblk; i++) {
          if (blk[i] ~ /uuid:[[:space:]]*[^[:space:]]+/) {
            tmp = blk[i]
            sub(/.*uuid:[[:space:]]*/, "", tmp)
            sub(/[[:space:]].*/, "", tmp)
            uuid = tmp
            break
          }
        }
        for (i = 1; i <= nblk; i++) {
          text = text (text == "" ? "" : "\n") blk[i]
        }
      }
      text = trim(text)
      nblk = 0

      if (uuid == "" || text !~ /[^[:space:]]/) return
      if (uuid in seen) return           # already consumed (or dup in-file)
      seen[uuid] = 1
      if (emitted > 0) printf "\n"       # join entries with a newline
      printf "%s", text
      emitted++
      print uuid >> FRESH
    }
    BEGIN {
      if (CONSUMED != "") {
        while ((getline line < CONSUMED) > 0) {
          if (match(line, /"uuid"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
            u = substr(line, RSTART, RLENGTH)
            sub(/.*"uuid"[[:space:]]*:[[:space:]]*"/, "", u)
            sub(/".*/, "", u)
            seen[u] = 1
          }
        }
        close(CONSUMED)
      }
    }
    { blk[++nblk] = $0 }
    /^---[[:space:]]*$/ { nblk--; delete blk[nblk + 1]; flush() }
    END { flush() }
  ' "$processing_path")"

  # Append a consumed record per fresh uuid (in emission order).
  if [[ -s "$fresh_file" ]]; then
    local u ts
    while IFS= read -r u; do
      [[ -n "$u" ]] || continue
      ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      printf '{"uuid":"%s","consumed_at":"%s"}\n' "$u" "$ts" >> "$consumed_path"
    done < "$fresh_file"
  fi
  rm -f "$fresh_file"

  if [[ -n "$interjection" ]]; then
    STEER_INTERJECTION_TEXT="$interjection"
  fi
}

# Sleep for a whole number of milliseconds (fractional-second sleep).
_steer_sleep_ms() {
  local ms="$1"
  sleep "$(awk -v ms="$ms" 'BEGIN { printf "%.3f", ms / 1000 }')"
}
