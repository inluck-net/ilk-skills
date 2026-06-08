# ilk xbar / SwiftBar plugin

Shows ilk loop status in the macOS menu bar.  Refreshes every 10 seconds.

![xbar format: `ilk 2▣` with per-project submenu]

## Install

### xbar (v2)

```bash
# Symlink into the xbar plugins directory:
ln -s "$(pwd)/tools/xbar/ilk.10s.sh" ~/Library/Application\ Support/xbar/plugins/
```

### SwiftBar

1. Open SwiftBar → **Plugins → Add Plugin…**
2. Navigate to `tools/xbar/ilk.10s.sh` and select it.

## What it shows

- **Menu-bar title**: `ilk 2▣` (2 alive loops) or `ilk ✓` (all idle).
- **Submenu**: one row per project with its key, state, and current step.
- **Actions**: open `/ilk-status`, refresh.

## Requirements

- Python 3.10+
- `status_all.py` from the ilk-loop skill (ships with ilk-skills).

## How it works

The shell entrypoint (`ilk.10s.sh`) runs `status_all.py --json` and pipes
the output to `render_xbar.py`, which formats it as xbar text.  The
filename encodes the refresh interval (`10s` = every 10 seconds).

## Uninstall

Remove the symlink or plugin file from your xbar/SwiftBar plugins directory.

## Windows counterpart

On Windows, use the native system-tray monitor instead:
[`tools/tray/`](../tray/) — same architecture (status_all → renderer → host),
native NotifyIcon with logon auto-start.
