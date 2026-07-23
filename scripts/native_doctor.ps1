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
}

# VS2010 predates vswhere and uses Microsoft.Cpp.x64.v100.* rather than the
# Toolset.targets filename used by newer platform toolsets. Detect the legacy
# compiler and MSBuild integration at their registered/default locations.
$vc100ProductDir = Get-ItemPropertyValue `
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\10.0\Setup\VC" `
    -Name ProductDir `
    -ErrorAction SilentlyContinue
if ([string]::IsNullOrWhiteSpace($vc100ProductDir)) {
    $vc100ProductDir = "${env:ProgramFiles(x86)}\Microsoft Visual Studio 10.0\VC\"
}

$v100CompilerCandidates = @(
    (Join-Path $vc100ProductDir "bin\amd64\cl.exe"),
    (Join-Path $vc100ProductDir "bin\x86_amd64\cl.exe")
)
$v100Compiler = $v100CompilerCandidates | Where-Object { Test-Path $_ -PathType Leaf } | Select-Object -First 1
Add-Check "v100 x64 compiler" (-not [string]::IsNullOrWhiteSpace($v100Compiler)) $v100Compiler

$v100Sp1Minimum = [version]"16.0.40219.1"
$v100Sp1Installed = $false
$v100VersionDetail = "compiler unavailable"
if (-not [string]::IsNullOrWhiteSpace($v100Compiler)) {
    $v100VersionText = ((Get-Item -LiteralPath $v100Compiler).VersionInfo.FileVersion -split " ")[0]
    $v100VersionDetail = "$v100VersionText (minimum SP1 $v100Sp1Minimum)"
    try {
        $v100Sp1Installed = [version]$v100VersionText -ge $v100Sp1Minimum
    }
    catch {
        $v100VersionDetail = "$v100VersionText (could not parse; minimum SP1 $v100Sp1Minimum)"
    }
}
Add-Check "v100 compiler SP1" $v100Sp1Installed $v100VersionDetail

$v100TargetsDir = "${env:ProgramFiles(x86)}\MSBuild\Microsoft.Cpp\v4.0\Platforms\x64\PlatformToolsets\v100"
$v100Props = Join-Path $v100TargetsDir "Microsoft.Cpp.x64.v100.props"
$v100Targets = Join-Path $v100TargetsDir "Microsoft.Cpp.x64.v100.targets"
$v100MsBuildInstalled = (Test-Path $v100Props -PathType Leaf) -and (Test-Path $v100Targets -PathType Leaf)
Add-Check "v100 x64 MSBuild" $v100MsBuildInstalled $v100TargetsDir

$checks | ForEach-Object {
    $state = if ($_.Passed) { "PASS" } else { "FAIL" }
    Write-Host ("{0,-4}  {1,-22}  {2}" -f $state, $_.Name, $_.Detail)
}

if ($checks.Where({ -not $_.Passed }).Count -gt 0) { exit 1 }
exit 0
