# Shared helper — dot-source from any ilk-* PowerShell script.
#
# Usage:
#   . (Join-Path $PSScriptRoot "_ilk_data_dir.ps1")
#   $dataDir = Get-IlkDataDir
#
# Precedence (identical across Python / PowerShell / bash):
#   $env:ILK_DATA_HOME  →  $env:ILK_DATA_DIR (alias)  →  ~/.ilk-data

function Get-IlkDataDir {
  if ($env:ILK_DATA_HOME) { return $env:ILK_DATA_HOME }
  if ($env:ILK_DATA_DIR)  { return $env:ILK_DATA_DIR  }
  return (Join-Path $HOME '.ilk-data')
}
