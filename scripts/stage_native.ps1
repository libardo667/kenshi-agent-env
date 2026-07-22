[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BuiltDll,
    [string]$OutputDirectory = "staging\KenshiAgentTelemetry"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$dll = Resolve-Path $BuiltDll
$destination = Join-Path $repo $OutputDirectory
New-Item -ItemType Directory -Force -Path $destination | Out-Null
Copy-Item $dll -Destination (Join-Path $destination "KenshiAgentTelemetry.dll") -Force
Copy-Item "native\KenshiAgentTelemetry\RE_Kenshi.json" -Destination $destination -Force
Copy-Item "native\KenshiAgentTelemetry\README.md" -Destination $destination -Force
Copy-Item "native\KenshiAgentTelemetry\THIRD_PARTY_NOTICES.md" -Destination $destination -Force
$modStubBase64 = Get-Content "native\KenshiAgentTelemetry\KenshiAgentTelemetry.mod.base64" -Raw
$modStub = [Convert]::FromBase64String($modStubBase64.Trim())
[IO.File]::WriteAllBytes(
    (Join-Path $destination "KenshiAgentTelemetry.mod"),
    $modStub
)

Write-Host "Staged native files at $destination"
Write-Host "This script does not install them into Kenshi."
Write-Host "After review, copy this folder to <Kenshi>\mods\KenshiAgentTelemetry."
