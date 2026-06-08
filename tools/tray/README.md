# Windows System Tray Monitor

Part of the [Windows tray monitor](../../skills/ilk-loop/SKILL.md) batch.

## Architecture

```
skills/ilk-loop/scripts/status_all.py --json   ← data source (REUSE AS-IS)
        │  array of {project_key, path, active_master, next_subplan,
        │            step, sentinel:{pid,state,alive}, last_class}
        ├─ tools/xbar/render_xbar.py → ilk.10s.sh   → macOS menu bar  (exists)
        └─ tools/tray/render_tray.py → ilk-tray.ps1 → Windows tray    (THIS)
```

`render_tray.py` is a **pure renderer** — no network, no side effects. It reads
`status_all --json` from stdin or `--json-from <file>` and emits a JSON
**view-spec** dict that the PowerShell host (`ilk-tray.ps1`) paints into a
NotifyIcon tray icon and context menu.

This mirrors the macOS architecture: `render_xbar.py` is the pure renderer,
`ilk.10s.sh` is the host.

## View-spec schema

`render_tray(entries: list[dict]) -> dict` returns:

```json
{
  "icon_state": "running" | "idle" | "attention",
  "tooltip": "ilk: 1 running, 2 idle",
  "rows": [
    {
      "label": "my-app  2/5  auth-module",
      "icon_state": "running" | "idle" | "attention",
      "project_key": "my-app",
      "action": {"kind": "status", "project_key": "my-app"}
    }
  ]
}
```

### `icon_state` logic

| Condition | `icon_state` |
|---|---|
| Any project has `sentinel.alive == true` | `running` |
| Any project has stale sentinel (`state=="running"` but `alive==false`) or `state` is `"error"`/`"errored"` | `attention` |
| All projects idle or dead | `idle` |

### `tooltip`

Summary string ≤127 chars (Windows NotifyIcon limit). Truncated with `...`
if the count summary exceeds the limit.

### `rows`

Each row's `label` mirrors the xbar text convention: `project_key  step  next_subplan  (state)`.
The `action` field is a structured hint for the host — no shell strings baked in.

## CLI usage

```bash
# From file
python tools/tray/render_tray.py --json-from status.json

# From stdin
echo '[]' | python tools/tray/render_tray.py
```

## Tests

```bash
python -m pytest tools/tray/tests/test_render_tray.py -q
```

Covers: empty input, all-idle, ≥1 running, stale-running→attention,
error→attention, tooltip length cap, CLI --json-from, CLI stdin, ASCII safety.

## Launch

```powershell
powershell -NoProfile -File tools/tray/ilk-tray.ps1
```

A tray icon appears in the system notification area. The icon recolors
by state: green = running, grey = idle, red/amber = attention. The
dropdown lists every registered project with its step, next sub-plan,
and state. Click a project to open its status/log directory.

Use `-IntervalSec 30` to change the refresh interval (default: 10s).

## Auto-start (logon)

```powershell
# Install — creates a per-user Startup shortcut (no admin needed)
powershell -NoProfile -File tools/tray/install-tray-autostart.ps1

# Uninstall — removes the shortcut
powershell -NoProfile -File tools/tray/install-tray-autostart.ps1 -Uninstall
```

The installer is idempotent: re-running refreshes the shortcut without
duplicating it. Uses `WScript.Shell` to create a `.lnk` in the user's
Startup folder. No admin, no service, no scheduled task.

## Limitation

The tray icon requires an **interactive desktop session** — it will not
work in headless / RDP-disconnected / service contexts. This is a
Windows `NotifyIcon` constraint, not something we can work around.

## Tests (renderer only)

```bash
python -m pytest tools/tray/tests/test_render_tray.py -q
```

The renderer is loop-verified (pytest). The host (`ilk-tray.ps1`) is
device-manual — a human must eyeball the actual tray.
