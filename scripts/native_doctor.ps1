[CmdletBinding()]
param(
    [string]$KenshiLibDir = $env:KENSHILIB_DIR,
    [string]$BoostRoot = $env:BOOST_INCLUDE_PATH
)

$checks = @()
function Add-Check([string]$Name, [bool]$Passed, [string]$Detail) {
    $script:checks += [PSCustomObject]@{ Name = $Name; Passed = $Passed; Detail = $Detail }
}

Add-Check "Windows" ($IsWindows -or $env:OS -eq "Windows_NT") $env:OS
Add-Check "KENSHILIB_DIR" (-not [string]::IsNullOrWhiteSpace($KenshiLibDir)) $KenshiLibDir

if (-not [string]::IsNullOrWhiteSpace($KenshiLibDir)) {
    $include = Join-Path $KenshiLibDir "Include\Debug.h"
    $library = Join-Path $KenshiLibDir "Libraries\kenshilib.lib"
    $libraryExists = Test-Path $library -PathType Leaf
    $libraryIsBinary = $libraryExists -and (Get-Item $library).Length -gt 1024
    Add-Check "KenshiLib Include" (Test-Path $include -PathType Leaf) $include
    Add-Check "KenshiLib Library" $libraryIsBinary "$library (must be a real binary, not a Git LFS pointer)"
}

Add-Check "BOOST_INCLUDE_PATH" (-not [string]::IsNullOrWhiteSpace($BoostRoot)) $BoostRoot
if (-not [string]::IsNullOrWhiteSpace($BoostRoot)) {
    $boostHeader = Join-Path $BoostRoot "boost\unordered_map.hpp"
    $boostLibrary = Join-Path $BoostRoot "stage\lib\libboost_thread-vc100-mt-1_60.lib"
    Add-Check "Boost headers" (Test-Path $boostHeader -PathType Leaf) $boostHeader
    Add-Check "Boost v100 library" (Test-Path $boostLibrary -PathType Leaf) $boostLibrary
}

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
Add-Check "vswhere" (Test-Path $vswhere) $vswhere
if (Test-Path $vswhere) {
    $msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find MSBuild\**\Bin\MSBuild.exe | Select-Object -First 1
    Add-Check "MSBuild" (-not [string]::IsNullOrWhiteSpace($msbuild)) $msbuild
    $modernCompiler = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -find VC\Tools\MSVC\**\bin\Hostx64\x64\cl.exe | Select-Object -First 1
    Add-Check "Visual C++ x64 tools" (-not [string]::IsNullOrWhiteSpace($modernCompiler)) $modernCompiler
    $v100 = & $vswhere -all -products * -find MSBuild\Microsoft.Cpp\v4.0\Platforms\x64\PlatformToolsets\v100\Toolset.targets | Select-Object -First 1
    Add-Check "v100 x64 toolset" (-not [string]::IsNullOrWhiteSpace($v100)) $v100
}

$checks | ForEach-Object {
    $state = if ($_.Passed) { "PASS" } else { "FAIL" }
    Write-Host ("{0,-4}  {1,-22}  {2}" -f $state, $_.Name, $_.Detail)
}

if ($checks.Where({ -not $_.Passed }).Count -gt 0) { exit 1 }
