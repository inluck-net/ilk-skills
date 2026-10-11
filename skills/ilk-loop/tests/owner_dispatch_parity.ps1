param([string]$Repo,[string]$Fixture)
$ErrorActionPreference='Stop'
$runner=Join-Path $Repo 'skills/ilk-loop/scripts/run_ilk_loop_claude.ps1'
$tokens=$null;$errors=$null
[void][System.Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$env:ILK_DOTSOURCE_ONLY='1'
Set-Alias python python3
. $runner -LogDir (Join-Path $Fixture 'logs') -JsonlLogPath (Join-Path $Fixture 'summary.jsonl')
$SkillRoot=Join-Path $Repo 'skills'
$target=[PSCustomObject]@{slug='work';step=0;fname='2026-10-11-work.md';master='MASTER-selected.md'}
$context=Get-WorkerDispatchContext -Project $Fixture -Target $target
if ($context.Identity.step -ne 0 -or $context.Identity.batch_base_sha -ne 'base123') { throw 'identity mismatch' }
if (-not $context.Brief.Contains($context.Identity.subplan_path)) { throw 'brief misses exact path' }
$missing=Get-WorkerCostObservation -LogFile (Join-Path $Fixture 'absent.log')
if ($null -ne $missing) { throw 'missing observation invented' }
New-Item -ItemType Directory -Path (Join-Path $Fixture 'logs') -Force | Out-Null
$iteration=New-IterationContext -Project $Fixture -Repos @($Fixture) -Dispatch $context -Run 'fixture' -Iteration 1 -Directory (Join-Path $Fixture 'logs')
if ($iteration.dispatch.subplan_path -ne $context.Identity.subplan_path) { throw 'context identity mismatch' }
$canonicalFixture = (& python -c 'import pathlib,sys;print(pathlib.Path(sys.argv[1]).resolve())' $Fixture | Out-String).Trim()
if ($iteration.exec_tree -ne $canonicalFixture) { throw 'context execution mismatch' }
'PowerShell parser, exact dispatch, brief, unknown observation and context: PASS'

$observationLog=Join-Path $Fixture 'observation.log'
@{type='system';subtype='init';model='fixture';session_id='fixture'} | ConvertTo-Json -Compress | Set-Content "$observationLog.jsonl"
$observed=Get-WorkerCostObservation -LogFile $observationLog -ExecTree $Fixture
if ($observed.bootstrap_execution_root -ne $canonicalFixture) { throw 'observation execution root mismatch' }
$profiles=@{ILK_WORKER_MODE='code-only';ILK_WORKER_TOOLS='native';ILK_WORKER_THINKING='disabled';ILK_ENABLE_UPGRADE_DRAIN='1'}
foreach ($name in $profiles.Keys) {
  $previous=[Environment]::GetEnvironmentVariable($name)
  try {
    [Environment]::SetEnvironmentVariable($name,$profiles[$name])
    $savedDot=$env:ILK_DOTSOURCE_ONLY;Remove-Item Env:ILK_DOTSOURCE_ONLY
    $refused=Join-Path $Fixture "must-not-exist-$name"
    $savedEap=$ErrorActionPreference;$ErrorActionPreference='Continue'
    try {
      $output=& (Join-Path $PSHOME 'pwsh') -NoProfile -File $runner -ProjectPath $Fixture -LogDir $refused -JsonlLogPath (Join-Path $refused 'summary.jsonl') 2>&1 | Out-String
      $code=$LASTEXITCODE
    } finally { $ErrorActionPreference=$savedEap;$env:ILK_DOTSOURCE_ONLY=$savedDot }
    if ($code -eq 0 -or $output -notmatch 'legacy default only' -or (Test-Path $refused)) { throw "profile refusal failed: $name : $output" }
  } finally { [Environment]::SetEnvironmentVariable($name,$previous) }
}
'PowerShell execution-root observation and unsupported profile refusals: PASS'
