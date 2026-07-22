[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Config,
    [int]$Steps = 20,
    [ValidateSet("heuristic", "scripted", "subprocess", "openai", "openrouter")]
    [string]$Planner = "heuristic",
    [string]$Python
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
if (-not $Python) {
    $repoVenv = Join-Path $repo ".venv\Scripts\python.exe"
    $liveVenv = Join-Path $env:LOCALAPPDATA "KenshiAgent\venvs\kenshi-agent-env\Scripts\python.exe"
    $Python = if (Test-Path $repoVenv) {
        $repoVenv
    } elseif (Test-Path $liveVenv) {
        $liveVenv
    } else {
        "python"
    }
}

& $Python -c "import kenshi_agent, PIL"
if ($LASTEXITCODE -ne 0) {
    throw "The selected Python cannot import kenshi_agent and Pillow. Run scripts\bootstrap_live_windows.ps1 or pass -Python with a prepared Windows environment."
}

& $Python -m kenshi_agent doctor --config $Config --mode live --planner $Planner
if ($LASTEXITCODE -ne 0) {
    throw "Live doctor checks failed. No agent run was started."
}

# Deliberately omits --execute-live-actions.
& $Python -m kenshi_agent run --config $Config --mode live --planner $Planner --steps $Steps
