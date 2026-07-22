[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$MediaDirectory,
    [string]$ManifestPath,
    [string[]]$MediaId
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestPath = Join-Path $PSScriptRoot "..\docs\native-media.lock.json"
}

$manifestFile = (Resolve-Path -LiteralPath $ManifestPath).Path
$mediaRoot = (Resolve-Path -LiteralPath $MediaDirectory).Path
$manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
$entries = @($manifest.media)

if ($MediaId.Count -gt 0) {
    $unknownIds = @($MediaId | Where-Object { $_ -notin $entries.id })
    if ($unknownIds.Count -gt 0) {
        throw "Unknown media id(s): $($unknownIds -join ', ')"
    }
    $entries = @($entries | Where-Object { $_.id -in $MediaId })
}

$failed = $false
foreach ($entry in $entries) {
    $path = Join-Path $mediaRoot $entry.filename
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Host ("MISSING  {0,-30}  {1}" -f $entry.id, $path)
        $failed = $true
        continue
    }

    $size = (Get-Item -LiteralPath $path).Length
    if ($size -ne [long]$entry.size_bytes) {
        Write-Host ("FAIL     {0,-30}  size {1}; expected {2}" -f $entry.id, $size, $entry.size_bytes)
        $failed = $true
        continue
    }

    Write-Host ("HASHING  {0,-30}  {1}" -f $entry.id, $entry.filename)
    $sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sha256 -ne $entry.sha256.ToLowerInvariant()) {
        Write-Host ("FAIL     {0,-30}  SHA-256 {1}" -f $entry.id, $sha256)
        $failed = $true
        continue
    }

    Write-Host ("PASS     {0,-30}  {1}" -f $entry.id, $sha256)
}

if ($failed) {
    exit 1
}
