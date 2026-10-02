# Shared helper — iteration-shipped marker management.
#
# Source from run_ilk_loop_claude.sh and from tests that exercise the
# runner's marker logic.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/_ilk_marker.sh"
#   clear_iteration_marker "$runtime_dir"
#   export_iteration_marker "$runtime_dir"

# ── clear_iteration_marker ────────────────────────────────────────────────────
# Remove a stale iteration-shipped marker file.
#
# $1  runtime dir (e.g. ~/.ilk-data/projects/<key>/runtime/launcher)
#     MUST NOT be empty — refuses to resolve from cwd.
clear_iteration_marker() {
  local runtime_dir="${1:-}"
  if [[ -z "$runtime_dir" ]]; then
    echo "clear_iteration_marker: runtime_dir must not be empty" >&2
    return 1
  fi
  local marker="${runtime_dir}/iteration-shipped.marker"
  if [[ -f "$marker" ]]; then
    rm -f "$marker"
  fi
}

# ── export_iteration_marker ───────────────────────────────────────────────────
# Export ILK_SHIPPED_MARKER pointing at the runtime dir's marker file.
#
# $1  runtime dir (e.g. ~/.ilk-data/projects/<key>/runtime/launcher)
#     MUST NOT be empty — refuses to resolve from cwd.
export_iteration_marker() {
  local runtime_dir="${1:-}"
  if [[ -z "$runtime_dir" ]]; then
    echo "export_iteration_marker: runtime_dir must not be empty" >&2
    return 1
  fi
  export ILK_SHIPPED_MARKER="${runtime_dir}/iteration-shipped.marker"
}