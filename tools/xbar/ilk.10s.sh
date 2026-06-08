#!/usr/bin/env bash
#  <xbar.title>ilk status</xbar.title>
#  <xbar.version>v1.0</xbar.version>
#  <xbar.author>ilk contributors</xbar.author>
#  <xbar.desc>Show ilk loop status in the macOS menu bar.</xbar.desc>
#  <xbar.dependencies>python3</xbar.dependencies>
#  <xbar.abouturl>https://github.com/inluck-net/ilk-skills</xbar.abouturl>
#
# xbar/SwiftBar plugin: refreshes every 10 seconds (filename convention).
# Renders status_all --json through render_xbar.py into menu-bar format.
#
# Install:
#   ln -s /path/to/ilk-skills/tools/xbar/ilk.10s.sh ~/Library/Application\ Support/xbar/plugins/
#   — or use SwiftBar's "Add Plugin…" and point at this file.

set -euo pipefail

# Resolve the directory this script lives in, following symlinks portably
# (macOS BSD readlink has no -f, so we use a loop).
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" != /* ]] && src="$dir/$src"
done
SCRIPT_DIR="$(cd -P "$(dirname "$src")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Locate python3.
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
    PYTHON="python"
fi

# Locate status_all.py and render_xbar.py.
STATUS_ALL="$REPO_ROOT/skills/ilk-loop/scripts/status_all.py"
RENDER="$SCRIPT_DIR/render_xbar.py"

if [[ ! -f "$STATUS_ALL" ]]; then
    echo "ilk ✗"
    echo "---"
    echo "status_all.py not found"
    exit 0
fi

if [[ ! -f "$RENDER" ]]; then
    echo "ilk ✗"
    echo "---"
    echo "render_xbar.py not found"
    exit 0
fi

# Pipe status_all --json through render_xbar.
"$PYTHON" "$STATUS_ALL" --json | "$PYTHON" "$RENDER"
