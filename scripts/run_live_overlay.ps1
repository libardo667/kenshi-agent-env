[CmdletBinding()]
param(
    [string]$Config = "config\live.longform.yaml",
    [int]$Steps = 80,
    [ValidateSet("openai", "openrouter")]
    [string]$Planner = "openrouter",

    # Planner model overrides. These set the environment variables the live
    # configs interpolate, so a model swap never needs a config edit.
    [string]$Model,
    [ValidateSet("none", "low", "medium", "high")]
    [string]$ReasoningEffort,

    # A refused launch still creates runs\<id>\, so a retry after any failure
    # needs a fresh id. Left unset, the timestamp guarantees one.
    [string]$RunId,

    [switch]$ExecuteLiveActions,
    [switch]$AcknowledgeNativeAssistedControl,
    [switch]$AcknowledgeContinuousLive,

    # controls.pointer_mode=relative requires an exclusive input session, and
    # both live configs use it, so this is on by default. Opt out only for a
    # config that sets pointer_mode=absolute.
    [switch]$NoExclusiveInputSession,

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

# The overlay is a GUI; launching it with python.exe opens a stray console
# window behind it.
$Pythonw = $Python -replace "python\.exe$", "pythonw.exe"
if (-not (Test-Path $Pythonw)) {
    $Pythonw = $Python
}

if ($Model) {
    if ($Planner -eq "openrouter") {
        $env:KENSHI_AGENT_OPENROUTER_MODEL = $Model
    } else {
        $env:KENSHI_AGENT_MODEL = $Model
    }
}
if ($ReasoningEffort) {
    $env:KENSHI_AGENT_REASONING_EFFORT = $ReasoningEffort
}

if (-not $RunId) {
    $RunId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmss.ffffffZ")
}
$runDir = Join-Path $repo "runs\$RunId"
if (Test-Path $runDir) {
    throw "runs\$RunId already exists. Every run needs an unused id -- even a refused launch creates the directory."
}

& $Python -m kenshi_agent doctor --config $Config --mode live --planner $Planner
if ($LASTEXITCODE -ne 0) {
    throw "Live doctor checks failed. No agent run was started."
}

$runArgs = @(
    "-m", "kenshi_agent", "run",
    "--config", $Config,
    "--mode", "live",
    "--planner", $Planner,
    "--steps", $Steps,
    "--run-id", $RunId
)
if ($ExecuteLiveActions) {
    $runArgs += "--execute-live-actions"
    if (-not $NoExclusiveInputSession) {
        $runArgs += "--exclusive-input-session"
    }
}
if ($AcknowledgeNativeAssistedControl) {
    if (-not $ExecuteLiveActions) {
        throw "-AcknowledgeNativeAssistedControl requires -ExecuteLiveActions."
    }
    $runArgs += "--acknowledge-native-assisted-control"
}
if ($AcknowledgeContinuousLive) {
    if (-not $ExecuteLiveActions) {
        throw "-AcknowledgeContinuousLive requires -ExecuteLiveActions."
    }
    $runArgs += "--acknowledge-continuous-live"
}

$eventLog = Join-Path $runDir "events.jsonl"
$overlayArgs = @(
    "-m", "kenshi_agent", "overlay",
    "--log", $eventLog,
    "--opacity", $Opacity,
    "--auto-close-seconds", $AutoCloseSeconds
)
$overlay = Start-Process -FilePath $Pythonw -ArgumentList $overlayArgs -PassThru

# The overlay is click-through so it cannot steal input meant for Kenshi, which
# also means nothing in it can be selected or copied. It is the stream view. The
# transcript is the reading view: the same decisions as plain text, scrollable in
# any editor, still there after the run ends.
$transcript = Join-Path $runDir "transcript.log"
Write-Host "Transcript: $transcript"

try {
    & $Python @runArgs 2>&1 | Tee-Object -FilePath $transcript
    if ($LASTEXITCODE -ne 0) {
        throw "The live agent run exited with code $LASTEXITCODE."
    }
} finally {
    # Give the overlay its auto-close grace so the final state stays readable,
    # then make sure it never outlives the run.
    if (-not $overlay.HasExited) {
        $null = $overlay.WaitForExit($AutoCloseSeconds * 1000)
    }
    if (-not $overlay.HasExited) {
        Stop-Process -Id $overlay.Id -Force -ErrorAction SilentlyContinue
    }
}
