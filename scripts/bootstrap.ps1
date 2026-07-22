[CmdletBinding()]
param(
    [string]$Python = "py",
    [switch]$WithOpenAI
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

& $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required. Pass -Python with a suitable executable."
}

if (-not (Test-Path ".venv")) {
    & $Python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
$target = if ($WithOpenAI) { ".[dev,openai]" } else { ".[dev]" }
& ".\.venv\Scripts\python.exe" -m pip install -e $target
& ".\.venv\Scripts\python.exe" -m kenshi_agent export-schemas --output schemas
& ".\.venv\Scripts\python.exe" -m kenshi_agent write-sample-telemetry --output examples\telemetry.latest.json
& ".\.venv\Scripts\python.exe" -m pytest
