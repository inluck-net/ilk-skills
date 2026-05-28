# Shared helper — dot-source from any ilk-* PowerShell script.
#
# Usage:
#   . (Join-Path $PSScriptRoot "_resolve_python.ps1")
#   $json = Invoke-IlkPython -ArgumentList @($script, "--start", $path)

function Get-IlkPythonExe {
  if (Get-Command python -ErrorAction SilentlyContinue) {
    return @{ Exe = 'python'; Prefix = @() }
  }
  if (Get-Command py -ErrorAction SilentlyContinue) {
    return @{ Exe = 'py'; Prefix = @('-3') }
  }
  if (Get-Command python3 -ErrorAction SilentlyContinue) {
    return @{ Exe = 'python3'; Prefix = @() }
  }
  throw "Python 3 not found on PATH. Install Python 3 and ensure 'python' is available (Windows typically uses 'python', not 'python3')."
}

function Invoke-IlkPython {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]]$ArgumentList,

    [string]$WorkingDirectory = ""
  )

  $py = Get-IlkPythonExe
  $allArgs = @($py.Prefix + $ArgumentList)

  if ($WorkingDirectory) {
    Push-Location $WorkingDirectory
    try {
      & $py.Exe @allArgs
      return $LASTEXITCODE
    } finally {
      Pop-Location
    }
  }

  & $py.Exe @allArgs
  return $LASTEXITCODE
}

function Invoke-IlkPythonCapture {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]]$ArgumentList,

    [string]$WorkingDirectory = ""
  )

  $py = Get-IlkPythonExe
  $allArgs = @($py.Prefix + $ArgumentList)

  if ($WorkingDirectory) {
    Push-Location $WorkingDirectory
    try {
      $out = & $py.Exe @allArgs 2>&1
    } finally {
      Pop-Location
    }
  } else {
    $out = & $py.Exe @allArgs 2>&1
  }

  return @{
    Output   = ($out | ForEach-Object { "$_" }) -join "`n"
    ExitCode = $LASTEXITCODE
  }
}
