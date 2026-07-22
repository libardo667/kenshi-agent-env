# Windows native contributor setup

This guide is for contributors who need to rebuild `KenshiAgentTelemetry.dll`.
People installing a published plugin release should not need Visual Studio,
KenshiLib headers, Boost, or legacy compiler media.

The project does not redistribute Microsoft installation media. Contributors
must acquire Visual Studio under an applicable Microsoft license. The exact
media identities are recorded in the [native media lock](native-media.lock.json);
ISO files are ignored by Git and must stay outside the repository.

## Why both Visual Studio versions are present

Visual Studio 2022 supplies the maintained IDE/MSBuild host. The native project
selects the Visual C++ 2010 `v100` x64 compiler because the upstream KenshiLib
and Boost binaries use that ABI. Installing VS2010 under `Program Files (x86)`
does not make the output 32-bit; the selected compiler target is x64.

## 1. Install the maintained build host

Install Visual Studio 2019 or newer. Visual Studio 2022 Build Tools is enough.
In Visual Studio Installer, enable **Desktop development with C++** so that
MSBuild and the current x64 C++ tools are present.

## 2. Acquire and verify the legacy media

Prefer Microsoft's authenticated downloads at
[My.VisualStudio.com](https://my.visualstudio.com/Downloads). The lock manifest
also records archival reference pages because Microsoft no longer exposes every
legacy download anonymously. An archival reference is not a grant of a Visual
Studio license.

Keep downloaded media in a directory outside this repository. Before opening
anything, verify it from a PowerShell prompt at the repository root:

```powershell
.\scripts\verify_native_media.ps1 -MediaDirectory C:\path\to\media
```

Verify only one item while downloading or diagnosing:

```powershell
.\scripts\verify_native_media.ps1 `
  -MediaDirectory C:\path\to\media `
  -MediaId vs2010-sp1-multilanguage
```

The verifier requires both the exact byte size and SHA-256 digest. Do not remove
the browser's Mark of the Web until verification passes.

## 3. Install Visual C++ 2010 x64

After verification, right-click the VS2010 Professional ISO, choose
**Properties**, check **Unblock**, and apply the change. Mount the ISO and run
only the root-level `setup.exe`.

Choose a custom installation and enable:

- **Visual C++**
- **Visual C++ > X64 Compilers and Tools**

Keep the default product directory:

```text
C:\Program Files (x86)\Microsoft Visual Studio 10.0\
```

Do not substitute a Visual C++ redistributable. Redistributables run already
compiled programs; they do not contain `cl.exe`, the linker, headers, or MSBuild
toolset files.

## 4. Apply Visual Studio 2010 SP1

Verify and unblock the SP1 ISO the same way. Mount it and run only its root-level
`Setup.exe`; do not launch individual `.msi` or `.msp` payloads. SP1 is
Microsoft KB983509 and updates the compiler to `16.00.40219.01`.

Microsoft documented a conflict with x64/IA64 compilers installed by the
standalone Windows SDK 7.1. Resolve that condition before applying SP1 if the
standalone SDK is present. The normal SDK components installed with Visual
Studio are a different case.

## 5. Obtain the KenshiLib dependency bundle

The maintained upstream dependency repository uses Git LFS. Clone it; do not use
GitHub's source ZIP, because the ZIP does not hydrate its `.lib`, `.dll`, and
dependency archives.

```powershell
git lfs install
git clone --no-checkout https://github.com/BFrizzleFoShizzle/KenshiLib_Examples_deps.git
cd KenshiLib_Examples_deps
git checkout b566d74bf3d74629cc2fb632a97595b8202993f1
git lfs pull
.\Setup.bat
```

`Setup.bat` extracts Boost 1.60 and sets these user environment variables:

```text
KENSHILIB_DIR=<dependency checkout>\KenshiLib
KENSHILIB_DEPS_DIR=<dependency checkout>
BOOST_INCLUDE_PATH=<dependency checkout>\boost_1_60_0
BOOST_ROOT=<dependency checkout>\boost_1_60_0
```

Open a new PowerShell window after `setx` updates the environment. The dependency
commit is pinned in `docs/UPSTREAM_LOCK.md`; update that lock intentionally when
upstream changes.

## 6. Diagnose and build

From the project root in Windows PowerShell:

```powershell
.\scripts\native_doctor.ps1
```

Resolve every failure. Then build only the supported configuration:

```powershell
.\scripts\build_native.ps1
```

The build script discovers MSBuild, reloads the upstream user environment
variables, and writes intermediates and outputs beneath
`%LOCALAPPDATA%\KenshiAgent\build\native`. Keeping the output on a normal local
Windows path is required when the source checkout is accessed through WSL: the
VS2010 compiler cannot create its program database on a WSL UNC path.

Upstream documents the Debug configuration as broken. Do not retarget the
project to a newer compiler to get around a `v100` failure; fix the toolchain or
dependency mismatch instead.

## 7. Stage without modifying Kenshi

Build output and game installation are separate steps. First stage a reviewable
folder:

```powershell
.\scripts\stage_native.ps1 `
  -BuiltDll "$env:LOCALAPPDATA\KenshiAgent\build\native\bin\KenshiAgentTelemetry.dll"
```

Inspect the staged DLL, `.mod` marker, `RE_Kenshi.json`, notices, and README.
Only then follow the [live validation checklist](LIVE_VALIDATION_CHECKLIST.md)
to install against a supported Kenshi/RE_Kenshi combination and a disposable
save.
