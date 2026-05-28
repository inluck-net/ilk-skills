#!/usr/bin/env bash
# Mark last-exit.json as interrupted when the stopped PID matches the
# recorded running PID. Preserves run metadata; adds stopped_by + stopped_at.
# Usage: mark_sentinel_interrupted.sh <runtime_dir> <stopped_pid>
set -euo pipefail

RUNTIME_DIR="${1:?usage: mark_sentinel_interrupted.sh <runtime_dir> <stopped_pid>}"
STOPPED_PID="${2:?usage: mark_sentinel_interrupted.sh <runtime_dir> <stopped_pid>}"
SENTINEL="${RUNTIME_DIR}/last-exit.json"

[[ -f "$SENTINEL" ]] || exit 0

python3 -c "
import json, sys, os
from datetime import datetime, timezone

sentinel = sys.argv[1]
stopped_pid = int(sys.argv[2])

try:
    with open(sentinel) as f:
        data = json.load(f)
except (json.JSONDecodeError, OSError):
    sys.exit(0)

if data.get('state') != 'running':
    sys.exit(0)
if int(data.get('pid', 0)) != stopped_pid:
    sys.exit(0)

data['state'] = 'interrupted'
data['stopped_by'] = 'ilk-stop'
data['stopped_at'] = datetime.now(timezone.utc).isoformat()

tmp = sentinel + '.tmp'
try:
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, sentinel)
    print(f'Sentinel marked interrupted (pid={stopped_pid})')
except OSError as e:
    print(f'  ! sentinel update failed: {e}', file=sys.stderr)
    try:
        os.unlink(tmp)
    except OSError:
        pass
" "$SENTINEL" "$STOPPED_PID"
