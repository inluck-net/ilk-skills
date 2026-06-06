# Tests for slot-home clone bootstrap (-CloneSlot).
# AC-1: clone creates settings.json with matching env, skills link, .claude.json;
#        re-run is idempotent (no error, no duplicate).
# Run: powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/claude-worker/tests/test_slot_homes.ps1

$ErrorActionPreference = "Stop"

$TestDir = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $TestDir "..\..\..")).Path
$Bootstrap = Join-Path $TestDir "..\bootstrap.ps1"
if (-not (Test-Path -LiteralPath $Bootstrap)) {
  Write-Error "bootstrap.ps1 not found at $Bootstrap"
  exit 1
}

$passed = 0
$failed = 0

function Assert-Eq($label, $expected, $actual) {
  if ($expected -eq $actual) {
    Write-Host "  PASS: $label" -ForegroundColor Green
    $script:passed++
  } else {
    Write-Host "  FAIL: $label — expected '$expected', got '$actual'" -ForegroundColor Red
    $script:failed++
  }
}

function Assert-FileExists($label, $path) {
  if (Test-Path -LiteralPath $path) {
    Write-Host "  PASS: $label" -ForegroundColor Green
    $script:passed++
  } else {
    Write-Host "  FAIL: $label — file does not exist: $path" -ForegroundColor Red
    $script:failed++
  }
}

function Assert-ExitOk($label, [scriptblock]$block) {
  try {
    & $block
    Write-Host "  PASS: $label" -ForegroundColor Green
    $script:passed++
  } catch {
    Write-Host "  FAIL: $label — exception: $($_.Exception.Message)" -ForegroundColor Red
    $script:failed++
  }
}

# --- Setup: fake base home under repo-local scratch ---
$FakeBase = Join-Path $RepoRoot "scratch\slot-test\base"
$ScratchDir = Join-Path $RepoRoot "scratch\slot-test"
if (Test-Path -LiteralPath $ScratchDir) {
  Remove-Item -Recurse -Force $ScratchDir
}
New-Item -ItemType Directory -Path (Join-Path $FakeBase "skills\ilk-runner") -Force | Out-Null

# Write a dummy settings.json with provider env.
$settingsContent = @{
  env = @{
    ANTHROPIC_BASE_URL   = "https://test-provider.example.com/anthropic"
    ANTHROPIC_AUTH_TOKEN = "test-token-12345"
    ANTHROPIC_MODEL      = "test-model-v1"
  }
} | ConvertTo-Json -Depth 5
Set-Content -LiteralPath (Join-Path $FakeBase "settings.json") -Value $settingsContent -Encoding utf8

# Write a minimal .claude.json.
'{
  "mcpServers": {}
}' | Set-Content -LiteralPath (Join-Path $FakeBase ".claude.json") -Encoding utf8

# Write a dummy file in skills/ so we can verify the link.
"skill-content" | Set-Content -LiteralPath (Join-Path $FakeBase "skills\ilk-runner\SKILL.md") -Encoding utf8

$SlotHome = "${FakeBase}-2"

try {
  # === Test 1: Clone slot 2 from fake base ===
  Write-Host "=== Test 1: Clone slot 2 ==="

  Assert-ExitOk "clone slot 2 succeeds" {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Bootstrap -CloneSlot 2 -From $FakeBase
  }

  Assert-FileExists "slot home exists" $SlotHome
  Assert-FileExists "settings.json exists" (Join-Path $SlotHome "settings.json")
  Assert-FileExists ".claude.json exists" (Join-Path $SlotHome ".claude.json")

  # Verify settings.json env matches the base.
  $slotSettings = Get-Content -LiteralPath (Join-Path $SlotHome "settings.json") -Raw | ConvertFrom-Json
  Assert-Eq "ANTHROPIC_BASE_URL matches" "https://test-provider.example.com/anthropic" $slotSettings.env.ANTHROPIC_BASE_URL
  Assert-Eq "ANTHROPIC_AUTH_TOKEN matches" "test-token-12345" $slotSettings.env.ANTHROPIC_AUTH_TOKEN
  Assert-Eq "ANTHROPIC_MODEL matches" "test-model-v1" $slotSettings.env.ANTHROPIC_MODEL

  # Verify skills link/dir exists and contains the expected file.
  $skillFile = Join-Path $SlotHome "skills\ilk-runner\SKILL.md"
  if (Test-Path -LiteralPath $skillFile) {
    Write-Host "  PASS: skills link accessible" -ForegroundColor Green
    $script:passed++
  } else {
    Write-Host "  FAIL: skills link not accessible" -ForegroundColor Red
    $script:failed++
  }

  # === Test 2: Idempotent re-run ===
  Write-Host ""
  Write-Host "=== Test 2: Idempotent re-run ==="

  Assert-ExitOk "re-clone slot 2 succeeds (idempotent)" {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Bootstrap -CloneSlot 2 -From $FakeBase
  }

  # Verify still correct after re-run.
  $slotSettings2 = Get-Content -LiteralPath (Join-Path $SlotHome "settings.json") -Raw | ConvertFrom-Json
  Assert-Eq "env still correct after re-run" "https://test-provider.example.com/anthropic" $slotSettings2.env.ANTHROPIC_BASE_URL

  # Verify skills still accessible.
  if (Test-Path -LiteralPath $skillFile) {
    Write-Host "  PASS: skills still accessible after re-run" -ForegroundColor Green
    $script:passed++
  } else {
    Write-Host "  FAIL: skills not accessible after re-run" -ForegroundColor Red
    $script:failed++
  }

  # === Test 3: Missing base home ===
  Write-Host ""
  Write-Host "=== Test 3: Missing base home ==="

  $nonexistent = Join-Path $RepoRoot "scratch\slot-test\nonexistent"
  $proc = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Bootstrap, "-CloneSlot", "3", "-From", $nonexistent) -NoNewWindow -Wait -PassThru -RedirectStandardOutput "$ScratchDir\test3_stdout.txt" -RedirectStandardError "$ScratchDir\test3_stderr.txt"
  if ($proc.ExitCode -ne 0) {
    Write-Host "  PASS: missing base home fails (exit $($proc.ExitCode))" -ForegroundColor Green
    $script:passed++
  } else {
    Write-Host "  FAIL: missing base home should fail (exit non-zero)" -ForegroundColor Red
    $script:failed++
  }

} finally {
  # Cleanup scratch directory.
  if (Test-Path -LiteralPath $ScratchDir) {
    Remove-Item -Recurse -Force $ScratchDir -ErrorAction SilentlyContinue
  }
}

Write-Host ""
Write-Host "=== Results: $passed passed, $failed failed ==="
if ($failed -gt 0) {
  throw "Test failures detected."
}
exit 0
