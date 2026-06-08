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

## Install / Uninstall

> **Coming in sub-plan 2** (`tray-host-and-autostart`): the PowerShell
> NotifyIcon host (`ilk-tray.ps1`) and logon auto-start installer
> (`install-tray-autostart.ps1`).
