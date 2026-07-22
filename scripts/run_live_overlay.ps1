[CmdletBinding()]
param(
    [string]$Config = "config\live.burnin.yaml",
    [int]$Steps = 30,
    [ValidateSet("openai", "openrouter")]
    [string]$Planner = "openai",
    [switch]$ExecuteLiveActions,
    [ValidateRange(0.25, 1.0)]
    [double]$Opacity = 0.82,
    [ValidateRange(0, 3600)]
    [int]$AutoCloseSeconds = 30,
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

& $Python -m kenshi_agent doctor --config $Config --mode live --planner $Planner
if ($LASTEXITCODE -ne 0) {
    throw "Live doctor checks failed. No agent run was started."
}

$runId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmss.ffffffZ")
$eventLog = Join-Path $repo "runs\$runId\events.jsonl"
$overlayArgs = @(
    "-m", "kenshi_agent", "overlay",
    "--log", $eventLog,
    "--opacity", $Opacity,
    "--auto-close-seconds", $AutoCloseSeconds
)
$overlay = Start-Process -FilePath $Python -ArgumentList $overlayArgs -PassThru

$runArgs = @(
    "-m", "kenshi_agent", "run",
    "--config", $Config,
    "--mode", "live",
    "--planner", $Planner,
    "--steps", $Steps,
    "--run-id", $runId
)
if ($ExecuteLiveActions) {
    $runArgs += "--execute-live-actions"
}

try {
    & $Python @runArgs
    if ($LASTEXITCODE -ne 0) {
        throw "The live agent run exited with code $LASTEXITCODE."
    }
} catch {
    if (-not $overlay.HasExited) {
        Stop-Process -Id $overlay.Id
    }
    throw
}
