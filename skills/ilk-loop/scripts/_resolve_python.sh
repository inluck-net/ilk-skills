#!/usr/bin/env bash
# Shared helper — source from any ilk-* bash script.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/_resolve_python.sh"
#   ilk_python  # prints executable name
#   ilk_invoke_python script.py --arg val

ilk_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo python
    return 0
  fi
  if command -v py >/dev/null 2>&1; then
    echo py
    return 0
  fi
  echo "ilk_python: Python 3 not found on PATH" >&2
  return 1
}

ilk_invoke_python() {
  local py
  py="$(ilk_python)" || return 1
  if [[ "$py" == "py" ]]; then
    py -3 "$@"
  else
    "$py" "$@"
  fi
}
