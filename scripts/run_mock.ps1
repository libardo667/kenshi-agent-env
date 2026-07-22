[CmdletBinding()]
param(
    [int]$Steps = 40,
    [ValidateSet("heuristic", "scripted", "subprocess", "openai")]
    [string]$Planner = "heuristic"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$python = if (Test-Path ".venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
& $python -m kenshi_agent run --config config\default.yaml --mode mock --planner $Planner --steps $Steps
