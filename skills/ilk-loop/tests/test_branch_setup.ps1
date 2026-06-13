<#
.SYNOPSIS
  Dot-source test for run_ilk_loop_claude.ps1 Setup-Branch — proves the branch
  policy lands the feat branch in EVERY hostable repo (not just the first), and
  skips repos that can't host it without failing the run.

  Uses the runner's dot-source guard (ILK_DOTSOURCE_ONLY=1): the script defines
  its functions and returns before the main loop, so we can call Setup-Branch
  directly against throwaway git repos. Hermetic — scratch dir under the repo.
#>
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir '..\..\..')
$Runner    = Join-Path $ScriptDir '..\scripts\run_ilk_loop_claude.ps1'
$Scratch   = Join-Path $RepoRoot 'scratch\branch-setup-test'

function Cleanup { if (Test-Path $Scratch) { Remove-Item $Scratch -Recurse -Force -ErrorAction SilentlyContinue } }

function New-GitRepo {
  param([string]$Path, [switch]$NoCommit)
  New-Item -ItemType Directory -Path $Path -Force | Out-Null
  & git -c init.defaultBranch=master init $Path 2>&1 | Out-Null
  if (-not $NoCommit) {
    Set-Content -Path (Join-Path $Path 'README.md') -Value 'x' -Encoding utf8
    & git -C $Path add -A 2>&1 | Out-Null
    & git -C $Path -c user.email='t@t' -c user.name='t' commit -m init 2>&1 | Out-Null
  }
}

function Get-Branch { param([string]$Path) (& git -C $Path branch --show-current 2>$null).Trim() }

Cleanup
$env:ILK_DOTSOURCE_ONLY = '1'
try {
  . $Runner    # dot-source: defines functions, returns before the main loop
  if (-not (Get-Command Setup-Branch -ErrorAction SilentlyContinue)) {
    throw "Setup-Branch not defined after dot-source"
  }

  # --- Test 1: two repos, both with master -> both end on feat/x ---
  $a = Join-Path $Scratch 'repo-a'; $b = Join-Path $Scratch 'repo-b'
  New-GitRepo -Path $a; New-GitRepo -Path $b
  $script:BranchName = 'feat/x'
  $script:BranchCreateFrom = 'master'
  $ok = Setup-Branch -Repos @($a, $b)
  if (-not $ok) { throw "Test1: Setup-Branch returned false for two clean repos" }
  if ((Get-Branch $a) -ne 'feat/x') { throw "Test1: repo-a on '$(Get-Branch $a)', expected feat/x" }
  if ((Get-Branch $b) -ne 'feat/x') { throw "Test1: repo-b (NOT first) on '$(Get-Branch $b)', expected feat/x" }
  Write-Host 'PASS: branch created in BOTH repos (target not skipped)'

  # --- Test 2: a third repo with NO master is skipped, others still branched ---
  Cleanup
  $a = Join-Path $Scratch 'repo-a'; $b = Join-Path $Scratch 'repo-b'; $c = Join-Path $Scratch 'repo-c'
  New-GitRepo -Path $a; New-GitRepo -Path $b; New-GitRepo -Path $c -NoCommit  # c has no master
  $script:BranchName = 'feat/x'; $script:BranchCreateFrom = 'master'
  $ok = Setup-Branch -Repos @($c, $a, $b)   # c first, to prove first-repo-skip is non-fatal
  if (-not $ok) { throw "Test2: Setup-Branch returned false despite branchable repos" }
  if ((Get-Branch $a) -ne 'feat/x') { throw "Test2: repo-a not branched" }
  if ((Get-Branch $b) -ne 'feat/x') { throw "Test2: repo-b not branched" }
  Write-Host 'PASS: repo without base ref skipped (non-fatal); others branched'

  # --- Test 3: no branchable repo -> false (preserves single-repo guard) ---
  Cleanup
  $c = Join-Path $Scratch 'repo-c'; New-GitRepo -Path $c -NoCommit
  $script:BranchName = 'feat/x'; $script:BranchCreateFrom = 'master'
  $ok = Setup-Branch -Repos @($c)
  if ($ok) { throw "Test3: Setup-Branch returned true when no repo could host the branch" }
  Write-Host 'PASS: zero branchable repos -> false'

  Write-Host 'ALL PASS'
} finally {
  Remove-Item Env:\ILK_DOTSOURCE_ONLY -ErrorAction SilentlyContinue
  Cleanup
}
