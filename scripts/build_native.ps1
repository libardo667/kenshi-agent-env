[CmdletBinding()]
param(
    [ValidateSet("Release")]
    [string]$Configuration = "Release",
    [string]$BuildRoot
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

foreach ($name in @("KENSHILIB_DIR", "KENSHILIB_DEPS_DIR", "BOOST_INCLUDE_PATH", "BOOST_ROOT")) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, "Process"))) {
        $userValue = [Environment]::GetEnvironmentVariable($name, "User")
        if (-not [string]::IsNullOrWhiteSpace($userValue)) {
            [Environment]::SetEnvironmentVariable($name, $userValue, "Process")
        }
    }
}

& (Join-Path $PSScriptRoot "native_doctor.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Native prerequisites failed. Resolve every native_doctor.ps1 failure before building."
}

if ([string]::IsNullOrWhiteSpace($BuildRoot)) {
    $BuildRoot = Join-Path $env:LOCALAPPDATA "KenshiAgent\build\native"
}
$BuildRoot = [IO.Path]::GetFullPath($BuildRoot)
$intermediate = Join-Path $BuildRoot "obj"
$output = Join-Path $BuildRoot "bin"
New-Item -ItemType Directory -Force -Path $intermediate, $output | Out-Null

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$msbuild = & $vswhere `
    -latest `
    -products * `
    -requires Microsoft.Component.MSBuild `
    -find MSBuild\**\Bin\MSBuild.exe | Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($msbuild)) {
    throw "Could not locate MSBuild with vswhere."
}

$solution = Join-Path $repo "native\KenshiAgentTelemetry\KenshiAgentTelemetry.sln"
& $msbuild `
    $solution `
    /m `
    /nologo `
    /v:minimal `
    "/flp:logfile=$BuildRoot\msbuild.log;verbosity=normal" `
    "/p:Configuration=$Configuration" `
    /p:Platform=x64 `
    "/p:IntDir=$intermediate\" `
    "/p:OutDir=$output\"
if ($LASTEXITCODE -ne 0) {
    throw "Native build failed with exit code $LASTEXITCODE."
}

$dll = Join-Path $output "KenshiAgentTelemetry.dll"
if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) {
    throw "MSBuild completed without producing $dll"
}

Write-Host "Built native plugin at $dll"
