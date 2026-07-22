[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$WithOpenAI
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $env:LOCALAPPDATA "KenshiAgent\venvs\kenshi-agent-env"
$venvPython = Join-Path $venv "Scripts\python.exe"

& $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required. Pass -Python with a suitable executable."
}

if (-not (Test-Path $venvPython)) {
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the Windows live runtime at $venv."
    }
}

$target = if ($WithOpenAI) { "$repo[openai]" } else { $repo }
& $venvPython -m pip install --disable-pip-version-check -e $target
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the editable Kenshi agent package into $venv."
}

& $venvPython -c "import kenshi_agent, PIL; print(kenshi_agent.__file__); print('Windows live runtime ready.')"
