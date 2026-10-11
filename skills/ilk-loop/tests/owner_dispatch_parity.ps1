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
