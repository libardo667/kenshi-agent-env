[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Config,
    [int]$Steps = 20,
    [ValidateSet("heuristic", "scripted", "subprocess", "openai")]
    [string]$Planner = "heuristic"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$python = if (Test-Path ".venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

& $python -m kenshi_agent doctor --config $Config --mode live --planner $Planner
if ($LASTEXITCODE -ne 0) {
    throw "Live doctor checks failed. No agent run was started."
}

# Deliberately omits --execute-live-actions.
& $python -m kenshi_agent run --config $Config --mode live --planner $Planner --steps $Steps
