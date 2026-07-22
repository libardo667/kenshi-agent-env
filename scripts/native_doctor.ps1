[CmdletBinding()]
param(
    [string]$KenshiLibDir = $env:KENSHILIB_DIR
)

$checks = @()
function Add-Check([string]$Name, [bool]$Passed, [string]$Detail) {
    $script:checks += [PSCustomObject]@{ Name = $Name; Passed = $Passed; Detail = $Detail }
}

Add-Check "Windows" ($IsWindows -or $env:OS -eq "Windows_NT") $env:OS
Add-Check "KENSHILIB_DIR" (-not [string]::IsNullOrWhiteSpace($KenshiLibDir)) $KenshiLibDir

if (-not [string]::IsNullOrWhiteSpace($KenshiLibDir)) {
    Add-Check "KenshiLib Include" (Test-Path (Join-Path $KenshiLibDir "Include\Debug.h")) (Join-Path $KenshiLibDir "Include\Debug.h")
    Add-Check "KenshiLib Library" (Test-Path (Join-Path $KenshiLibDir "Libraries\kenshilib.lib")) (Join-Path $KenshiLibDir "Libraries\kenshilib.lib")
}

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
Add-Check "vswhere" (Test-Path $vswhere) $vswhere
if (Test-Path $vswhere) {
    $msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find MSBuild\**\Bin\MSBuild.exe | Select-Object -First 1
    Add-Check "MSBuild" (-not [string]::IsNullOrWhiteSpace($msbuild)) $msbuild
    $v100 = & $vswhere -all -products * -find MSBuild\Microsoft.Cpp\v4.0\Platforms\x64\PlatformToolsets\v100\Toolset.targets | Select-Object -First 1
    Add-Check "v100 x64 toolset" (-not [string]::IsNullOrWhiteSpace($v100)) $v100
}

$checks | ForEach-Object {
    $state = if ($_.Passed) { "PASS" } else { "FAIL" }
    Write-Host ("{0,-4}  {1,-22}  {2}" -f $state, $_.Name, $_.Detail)
}

if ($checks.Where({ -not $_.Passed }).Count -gt 0) { exit 1 }
