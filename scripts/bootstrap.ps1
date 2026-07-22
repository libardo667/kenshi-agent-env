[CmdletBinding()]
param(
    [string]$Python = "py -3.11",
    [switch]$WithOpenAI
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (-not (Test-Path ".venv")) {
    & ([scriptblock]::Create("$Python -m venv .venv"))
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
$target = if ($WithOpenAI) { ".[dev,openai]" } else { ".[dev]" }
& ".\.venv\Scripts\python.exe" -m pip install -e $target
& ".\.venv\Scripts\python.exe" -m kenshi_agent export-schemas --output schemas
& ".\.venv\Scripts\python.exe" -m kenshi_agent write-sample-telemetry --output examples\telemetry.latest.json
& ".\.venv\Scripts\python.exe" -m pytest
