# Native Python↔PowerShell IO convention

Canonical reference for the two-sided rule that prevents the recurring
`NativeCommandError` bug class in the ilk-loop toolkit.

## The problem

PowerShell 5.1 (Windows built-in) wraps **any** native command's stderr
output as a `NativeCommandError` `RuntimeException` when
`$ErrorActionPreference` is `"Stop"` (the default in many runner
scripts). Python's `--json` mode was designed to emit a clean JSON
document to stdout, but informational notices ("no active master",
">1 active") were printed to stderr — which PS 5.1 converted into a
terminating exception that crashed the runner.

This bug has appeared **5+ times** across the ilk-loop toolkit (runner,
launcher, watchdog, status probe) because the pattern is subtle:
Python's stderr output looks harmless on Linux/macOS but is fatal under
PS 5.1's `$EAP='Stop'`.

## The two-sided rule

### Side 1: Python scripts — `--json` = payload-only on stdout

When a Python script is invoked with `--json` (or `--quiet`, or any
machine-readable mode), it MUST:

- **stdout**: emit ONLY the machine payload (JSON document). No
  banners, no progress lines, no human-readable prefix/suffix.
- **stderr**: emit ONLY fatal error messages that warrant a non-zero
  exit code. Informational notices, warnings, and diagnostics MUST be
  folded into the payload itself (e.g. as a `notices[]` array field).

**Why:** The PS caller captures stdout for parsing. Any non-JSON on
stdout breaks the parser. Any stderr output — even benign warnings —
triggers `NativeCommandError` under `$EAP='Stop'`.

#### Canonical idiom: `notices[]` payload field

```python
def resolve_status(cwd: Path, json_mode: bool = False) -> dict:
    notices: list[str] = []
    # ...
    if something_unusual:
        msg = "[ilk] WARNING: something unusual happened"
        if json_mode:
            notices.append(msg)  # ← fold into payload
        else:
            print(msg, file=sys.stderr)  # ← human mode: stderr OK
    # ...
    result = {
        # ... machine fields ...
        "notices": notices,  # ← always present, empty when no notices
    }
    return result

def main() -> int:
    # ...
    if args.json:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return data["queue_exit"]
```

The key contract: `notices[]` is **always present** in the JSON output
(even if empty `[]`), so callers can reliably read it. The runner uses
this field to log warnings without losing them to stderr.

### Side 2: PowerShell callers — neutralize `NativeCommandError`

Every `& python …` invocation in a PS script MUST run under
`$ErrorActionPreference = 'Continue'` so that Python's stderr output
(which may include deprecation warnings, encoding notices, or other
non-fatal noise) does not trigger a `NativeCommandError`.

There are two idioms, depending on scope:

#### Idiom A: Function-local (auto-restores)

Inside a `function` block, `$ErrorActionPreference` is scoped to the
function — it auto-restores on exit. This is the preferred idiom when
the `& python` call is inside a function:

```powershell
function Get-PlansDir {
  # PS 5.1 wraps native stderr as NativeCommandError under $EAP='Stop'.
  # Function-local Continue auto-restores on exit.
  $ErrorActionPreference = 'Continue'
  $resolver = Join-Path (Split-Path $PSCommandPath -Parent) "ilk_paths.py"
  if (-not (Test-Path $resolver)) { return $null }
  $raw = & python $resolver --start $ProjectPath --where 2>$null
  # ...
}
```

#### Idiom B: Script-level save/restore (not in a function)

At the top level of a script (not inside a function), there is no
auto-restore. Save and restore manually:

```powershell
# PS 5.1 wraps native stderr as NativeCommandError under $EAP='Stop'.
# Script-level: save/restore (not in a function, so no auto-restore).
$savedEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
try {
  $json = & python $LoopStatusScript 2>$null
  # ... process $json ...
} finally {
  $ErrorActionPreference = $savedEAP
}
```

#### Why `2>$null` is not enough alone

`2>$null` discards stderr but does NOT prevent `NativeCommandError` —
PS 5.1 raises the exception before the redirect applies when
`$EAP='Stop'`. The `$ErrorActionPreference = 'Continue'` is mandatory;
`2>$null` is optional (use it when you don't need stderr content).

## When this convention applies

This convention applies to **every** `& python` call site in the
ilk-loop toolkit scripts:

- `run_ilk_loop_claude.ps1` — the main runner
- `run_ilk_loop.ps1` — the lightweight runner
- Launcher scripts
- Watchdog scripts
- Any helper `.ps1` that invokes Python

It also applies to **every** Python script in the toolkit that supports
a `--json` or machine-readable mode:

- `loop_status.py` — the status probe
- `ilk_paths.py` — the path resolver
- Any future script with a `--json` flag

## Enforcement

The `--source-hygiene` mode of `plan_lint.py` enforces this convention
mechanically:

1. **stderr-in-json-path**: Flags Python files that write to
   `sys.stderr` inside a `--json` code path (heuristic: within an
   `if args.json:` block or a `resolve_status` called with `json_mode`).

2. **unguarded-native-python**: Flags `.ps1` files with a `& python`
   line that has no `$ErrorActionPreference = 'Continue'` guard in
   scope.

Run with:
```bash
python skills/ilk-loop/scripts/plan_lint.py --source-hygiene <scripts...>
```

## History

| Date | Instance | File | Root cause |
|------|----------|------|------------|
| 2026-07-04 | #5 | `run_ilk_loop_claude.ps1:1394` (loop-status probe) | `loop_status.py` printed "no active master" warning to stderr in `--json` mode; PS 5.1 wrapped it as `NativeCommandError` |

The pattern has been independently fixed 4 times before without a
convention doc or lint — each fix was local to the specific call site.
This convention and its enforcing lint exist to kill the class, not just
the instance.
