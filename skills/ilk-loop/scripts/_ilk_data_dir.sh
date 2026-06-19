# Shared helper — source from any ilk-* bash script.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/_ilk_data_dir.sh"
#   data_dir="$(ilk_data_dir)"
#
# Precedence (identical across Python / PowerShell / bash):
#   $ILK_DATA_HOME  →  $ILK_DATA_DIR (alias)  →  ~/.ilk-data

ilk_data_dir() {
  if [ -n "$ILK_DATA_HOME" ]; then
    printf '%s' "$ILK_DATA_HOME"
  elif [ -n "$ILK_DATA_DIR" ]; then
    printf '%s' "$ILK_DATA_DIR"
  else
    printf '%s' "$HOME/.ilk-data"
  fi
}
