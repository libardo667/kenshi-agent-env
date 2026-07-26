# Guide: rebuilding the native plug-in on Windows

For contributors rebuilding `KenshiAgentTelemetry.dll`. Installing a published
release needs none of this. Exact pinned versions are in
[`GUIDE_UPSTREAM_LOCK.md`](GUIDE_UPSTREAM_LOCK.md).

This project redistributes no Microsoft media; acquire Visual Studio under an
applicable Microsoft license. ISO files are gitignored and must stay outside the
repository.

## Why two Visual Studio versions

VS2022 supplies the maintained IDE and MSBuild host. The native project selects
the Visual C++ 2010 `v100` x64 compiler because upstream KenshiLib and Boost
binaries use that ABI. Installing VS2010 under `Program Files (x86)` does not
make the output 32-bit — the selected compiler target is x64. A Visual C++
redistributable is not a substitute: it runs compiled programs and contains no
`cl.exe`, linker, headers, or toolset files.

## 1. Build host

Visual Studio 2019+ (2022 Build Tools is enough) with **Desktop development with
C++** enabled.

## 2. Verify legacy media before opening it

Prefer [My.VisualStudio.com](https://my.visualstudio.com/Downloads). Keep media
outside the repository, then from the repo root:

```powershell
.\scripts\verify_native_media.ps1 -MediaDirectory C:\path\to\media
# or one item: -MediaId vs2010-sp1-multilanguage
```

The verifier requires both exact byte size and SHA-256. Do not clear the
browser's Mark of the Web until it passes.

## 3. Visual C++ 2010 x64

Unblock the VS2010 Professional ISO in **Properties**, mount it, run only the
root `setup.exe`. Custom install, enable **Visual C++** and **Visual C++ > X64
Compilers and Tools**. Keep the default directory
`C:\Program Files (x86)\Microsoft Visual Studio 10.0\`.

## 4. Visual Studio 2010 SP1

Verify and unblock the SP1 ISO the same way; run only its root `Setup.exe`, never
individual `.msi`/`.msp` payloads. SP1 is KB983509 and updates the compiler to
`16.00.40219.01`. Microsoft documents a conflict with x64/IA64 compilers from the
*standalone* Windows SDK 7.1 — resolve that first if present. SDK components
installed with Visual Studio are a different case.

## 5. KenshiLib dependency bundle

Uses Git LFS. Clone it; GitHub's source ZIP does not hydrate the `.lib`, `.dll`,
and dependency archives.

```powershell
git lfs install
git clone --no-checkout https://github.com/BFrizzleFoShizzle/KenshiLib_Examples_deps.git
cd KenshiLib_Examples_deps
git checkout b566d74bf3d74629cc2fb632a97595b8202993f1
git lfs pull
.\Setup.bat
```

`Setup.bat` extracts Boost 1.60 and sets `KENSHILIB_DIR`, `KENSHILIB_DEPS_DIR`,
`BOOST_INCLUDE_PATH`, and `BOOST_ROOT`. Open a new PowerShell window afterwards
so the `setx` changes are visible.

## 6. Diagnose, then build

```powershell
.\scripts\native_doctor.ps1   # resolve every failure first
.\scripts\build_native.ps1
```

Output goes under `%LOCALAPPDATA%\KenshiAgent\build\native`. A normal local
Windows path is **required** when the checkout is reached through WSL: the VS2010
compiler cannot create its PDB on a WSL UNC path. Upstream documents the Debug
configuration as broken. Never retarget to a newer compiler to dodge a `v100`
failure — fix the toolchain or dependency mismatch.

## 7. Stage before installing

```powershell
.\scripts\stage_native.ps1 `
  -BuiltDll "$env:LOCALAPPDATA\KenshiAgent\build\native\bin\KenshiAgentTelemetry.dll"
```

Inspect the staged DLL, `.mod` marker, `RE_Kenshi.json`, notices, and README,
then follow [`GUIDE_LIVE_RUNS.md`](GUIDE_LIVE_RUNS.md) against a supported
Kenshi/RE_Kenshi combination and a disposable save.

## Smart App Control can block an unsigned local DLL

Windows 11 Smart App Control can refuse a freshly compiled unsigned plug-in even
when the build succeeds and RE_Kenshi finds it. This is not the downloaded-file
`Unblock` checkbox: `Unblock-File` only strips `Zone.Identifier`, while Smart App
Control also evaluates reputation and signature. Confirm in
`Microsoft-Windows-CodeIntegrity/Operational` — event 3077 names the refused DLL.
RE_Kenshi may report only `Could not load plugin` and `No error`, because Windows
rejects the image before its entry point runs.

Do not casually advise disabling it; Microsoft provides no per-file exception.
Supported paths: sign release DLLs with a CA certificate in the Microsoft Trusted
Root Program; use Microsoft Artifact Signing Public Trust; do inner-loop testing
on a machine or VM where it is not enforcing; or move to Microsoft's developer
evaluation configuration only after the machine owner accepts the system-wide
tradeoff. Record the prior state before changing it.

References: [Smart App Control
overview](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview),
[FAQ](https://support.microsoft.com/en-us/topic/smart-app-control-frequently-asked-questions-285ea03d-fa88-4d56-882e-6698afdb7003),
[code-signing
options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options).
