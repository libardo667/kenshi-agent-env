$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$python = if (Test-Path ".venv\Scripts\python.exe") {
    ".\.venv\Scripts\python.exe"
} else {
    "python"
}

& $python -m pytest
& $python -m ruff check src tests scripts\external_planner_example.py
& $python -m mypy src
