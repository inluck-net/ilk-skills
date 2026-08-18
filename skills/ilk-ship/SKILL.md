---
name: ilk-ship
description: >-
  Loads and validates the ship: block from .ilk-launch.json — the
  project-level declaration of how it ships (suite command, baseline-red
  exclusions, hosts). Used by ilk-ship phases 0-3 to scope the release
  gate to the consumer set rather than the diff size.
---

# ilk-ship — project ship config loader

## When to use

- The `ilk-ship` skill's phases read the `ship:` block via
  `ship_config.load_ship_config(project_path)`.
- `doctor.py` uses the same loader to validate the config during preflight.

## Schema

The `ship:` block lives inside `.ilk-launch.json` (resolved by the same
3-location precedence as `read_project_config` in `launch.sh:240`):

```json
{
  "ship": {
    "suite": {
      "command": "python3 -m pytest",
      "flags": ["--timeout-method=signal"],
      "timeout": 300
    },
    "baseline_red": [
      {
        "node_id": "skills/ilk-lark-tickets/tests/test_init_project.py",
        "reason": "20 failures pre-existing at v0.9.62, unrelated to this batch",
        "as_of": "2026-08-14"
      }
    ],
    "hosts": ["chad-mbp", "rezmac"],
    "path_prelude": "export PATH=\"/opt/homebrew/bin:$PATH\""
  }
}
```

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `suite.command` | string | yes | The test runner command |
| `suite.flags` | list[str] | no | Additional flags for the runner |
| `suite.timeout` | int | no | Gate timeout in seconds |
| `baseline_red` | list[obj] | no | Exclusions with reasons |
| `baseline_red[].node_id` | string | yes | Test node or file path |
| `baseline_red[].reason` | string | yes | Why excluded (non-empty) |
| `baseline_red[].as_of` | string | yes | Date measured (YYYY-MM-DD) |
| `hosts` | list[str] | no | Target hosts (declarative, no probing) |
| `path_prelude` | string | no | Shell prelude for PATH setup |

## Resolution

`load_ship_config(project_path)` returns one of three result types:

- `ShipConfig` — valid config with `resolved_path` and `location` (1/2/3)
- `NotConfigured` — no `ship:` key found (degrade-to-default)
- `MalformedConfig` — invalid schema (hard error, names the key and file)

The 3-location precedence matches `launch.sh:240-269` and `doctor.py:670-680`.

## CLI

```bash
python3 skills/ilk-ship/scripts/ship_config.py --validate --project .
```

Reports the resolved file, location, and any staleness warnings.
