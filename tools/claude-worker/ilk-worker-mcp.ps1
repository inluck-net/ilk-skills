<#
.SYNOPSIS
  Add / list / verify an MCP server in the loop WORKER home (Windows).

.DESCRIPTION
  Thin wrapper over worker_mcp_edit.py. The loop worker runs with
  CLAUDE_CONFIG_DIR=<worker home> (default ~/.claude-worker) and reads its OWN
  .claude.json mcpServers — NOT ~/.claude.json. Use this to give the loop an
  MCP it can actually reach.

  add <name> [-FromUser]   Add a known server (figma, chrome-devtools). With
                           -FromUser, also copy that server's OAuth token from
                           ~/.claude/.credentials.json (figma) — never the
                           planner's Claude identity.
  list                     Print the worker's MCP servers (JSON).
  verify                   Run `claude mcp list` under the worker config dir.

.EXAMPLE
  .\ilk-worker-mcp.ps1 add figma -FromUser
  .\ilk-worker-mcp.ps1 verify
#>
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $ScriptDir 'worker_mcp_edit.py'
if (-not (Test-Path $Py)) { throw "worker_mcp_edit.py not found: $Py" }

# Translate -FromUser (PowerShell-style) to --from-user for argparse; pass the
# rest through untouched.
$fwd = @()
foreach ($a in $args) {
  if ($a -ieq '-FromUser') { $fwd += '--from-user' }
  else { $fwd += $a }
}

& python $Py @fwd
exit $LASTEXITCODE
