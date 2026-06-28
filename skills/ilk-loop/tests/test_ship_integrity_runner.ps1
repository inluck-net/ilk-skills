# Runtime regression gate for Test-ShipIntegrity (run_ilk_loop_claude.ps1).
#
# This is the test that was MISSING: the ship-integrity feature shipped with 6
# PowerShell-side bugs that the Python pytest could not catch because none of
# them live in ship_integrity.py — they were all in the runner's PS wiring:
#   1. $violations += [PSCustomObject] then .ToArray()  (Object[] has no ToArray)
#   2. cross-run scoping: a PRIOR-run shipped sub-plan (no current-run gate
#      result) was flagged -> would falsely revert already-shipped work
#   3. `return ,$x.ToArray()` double-wraps an EMPTY result -> phantom Count=1
#   4. `$lines -notmatch X` on an ARRAY always-truthy -> skipped EVERY file
#   5. JSON --gate-json mangled PS->python.exe; non-zero-exit conflated bad-input
#      (exit 2) with a real violation (exit 1)
#   6. native python stderr under $ErrorActionPreference='Stop' -> terminating
#      NativeCommandError -> runner crashed exactly when a violation was found
#
# The test extracts Test-ShipIntegrity from the runner, stubs its deps, points
# it at a temp plans dir of fixture sub-plans, and asserts the scenarios. It
# runs under ErrorActionPreference='Stop' (matching the real runner) so the
# bug-6 fix is exercised.

$ErrorActionPreference = 'Stop'
$fail = 0
function Assert($name, $cond) {
  if ($cond) { Write-Host "  PASS: $name" } else { Write-Host "  FAIL: $name" -ForegroundColor Red; $script:fail++ }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$runner = Join-Path $repoRoot "skills\ilk-loop\scripts\run_ilk_loop_claude.ps1"
$SkillRoot = Join-Path $repoRoot "skills"

# --- Extract the Test-ShipIntegrity function body (robust to line moves) ---
$src = Get-Content $runner -Raw
$m = [regex]::Match($src, '(?ms)^function Test-ShipIntegrity \{.*?^\}')
if (-not $m.Success) { Write-Host "FAIL: could not extract Test-ShipIntegrity" -ForegroundColor Red; exit 1 }
Invoke-Expression $m.Value

# --- Stub Get-PlansDir to point at our temp fixtures ---
$script:TestPlansDir = $null
function Get-PlansDir { param($Project) return $script:TestPlansDir }

# --- Build a temp plans dir with fixture sub-plans ---
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ship-int-test-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$script:TestPlansDir = $tmp

# shipped + declared frontmatter local_checks
@"
---
plan: alpha
status: shipped
local_checks:
  - command: pytest -q
---
# alpha
"@ | Set-Content -Path (Join-Path $tmp "2026-01-01-alpha.md") -Encoding utf8

# shipped but NO frontmatter checks (must never be enforced)
@"
---
plan: beta
status: shipped
local_checks: []
---
# beta
"@ | Set-Content -Path (Join-Path $tmp "2026-01-01-beta.md") -Encoding utf8

# pending (must never be enforced)
@"
---
plan: gamma
status: pending
local_checks:
  - command: pytest -q
---
# gamma
"@ | Set-Content -Path (Join-Path $tmp "2026-01-01-gamma.md") -Encoding utf8

try {
  # Bug 2/3/4: empty gate-map (all are PRIOR-run ships) -> 0 violations, no phantom
  $v1 = @(Test-ShipIntegrity -ProjectPath 'x' -LocalChecksRun @())
  Assert "empty gate-map -> 0 violations (no phantom, no false-revert)" ($v1.Count -eq 0)

  # Bug 1/5/6: alpha shipped + red gate THIS run -> exactly 1 violation, no crash
  $v2 = @(Test-ShipIntegrity -ProjectPath 'x' -LocalChecksRun @(@{ slug='alpha'; outcome='fail'; raw=$null }))
  Assert "alpha red this run -> 1 violation" ($v2.Count -eq 1)
  Assert "violation names the slug" ($v2.Count -ge 1 -and $v2[0].Slug -eq 'alpha')

  # alpha shipped + green gate THIS run -> 0 violations
  $v3 = @(Test-ShipIntegrity -ProjectPath 'x' -LocalChecksRun @(@{ slug='alpha'; outcome='pass'; raw=$null }))
  Assert "alpha green this run -> 0 violations" ($v3.Count -eq 0)

  # beta shipped but no declared checks -> never enforced even with a red gate
  $v4 = @(Test-ShipIntegrity -ProjectPath 'x' -LocalChecksRun @(@{ slug='beta'; outcome='fail'; raw=$null }))
  Assert "beta (no declared checks) -> 0 violations" ($v4.Count -eq 0)

  # gamma is pending -> never enforced
  $v5 = @(Test-ShipIntegrity -ProjectPath 'x' -LocalChecksRun @(@{ slug='gamma'; outcome='fail'; raw=$null }))
  Assert "gamma (pending) -> 0 violations" ($v5.Count -eq 0)
}
finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

if ($fail -eq 0) { Write-Host "ALL PASS" -ForegroundColor Green; exit 0 }
else { Write-Host "$fail FAILED" -ForegroundColor Red; exit 1 }
