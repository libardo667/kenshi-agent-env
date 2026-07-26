# Guide: upstream and host lock

Reproducibility anchors for the native build. Hashes and commits are the
authority; paths are kept generic where possible. Do not record secrets, account
names, or unnecessary absolute user paths.

Per-build DLL digests are **not** kept here — they belong in the commit that
landed the build. This file drifted for exactly that reason.

Verified 2026-07-22 during the first native build and live bring-up.

| Component | Version/tag/commit | Source or installed path | Notes |
|---|---|---|---|
| Kenshi executable | 1.0.68 Steam, build 13871665 | `…\steamapps\common\Kenshi\kenshi_x64.exe` | SHA-256 `a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1` |
| RE_Kenshi | v0.3.4, commit `be107d258618974d56b7373f0f86c82daa2196a9` | [release](https://github.com/BFrizzleFoShizzle/RE_Kenshi/releases/tag/v0.3.4) | Log reports 0.3.4, Steam 1.0.65 compatibility runtime |
| KenshiLib | v0.4.0, commit `18f75fecb93cfead6029efe0d5fe199d6618bcc9` | [release](https://github.com/BFrizzleFoShizzle/KenshiLib/releases/tag/v0.4.0) | Installed with RE_Kenshi; log reports 0.4.0 and loaded RVAs |
| KenshiLib examples | `548b3eaf779c1b2feb25416f1db757320d04ec6c` | [repository](https://github.com/BFrizzleFoShizzle/KenshiLib_Examples) | Dependency layout reference |
| Example dependencies | `b566d74bf3d74629cc2fb632a97595b8202993f1` | local checkout | Detached at pinned commit; `git lfs fsck` passes; Boost extracted |
| Visual Studio Build Tools | 2022 17.14.35 | Windows installation | MSBuild present |
| Visual C++ v100 x64 toolset | compiler 16.00.40219.01, VS2010 SP1 | `C:\Program Files (x86)\Microsoft Visual Studio 10.0\VC` | x64 compiler and `v100` integration pass the native doctor |
| Windows SDK | 7.0A (VS2010); Windows 10 SDK 10.0.26100.0 (VS2022) | Windows installation | Standalone Windows SDK 7.1 is **not** installed |
| Python | CPython 3.12.13 x64, uv-managed | Windows user installation | `py -3.11` unavailable |

Upstream examples require Boost 1.60 headers and v100 libraries in addition to
KenshiLib, link plugins with `kenshilib.lib`, and use a 46-byte native-only
`.mod` stub plus `RE_Kenshi.json` beside the DLL. Stub SHA-256
`ebdab65d330e46e1ff9725ac5d0ed87fd8c718cfb41ef85b27b86eb3d35b79c0`.

## Active mods during validation

`KenshiAgentTelemetry.mod` was the only enabled non-core mod.

## Dependency bundle checksums

```text
a5df733f576eade3c3293ca5c4dd2764fd334f9557743962cf5dc6cb03395bc3  RE_Kenshi_v0.3.4.zip
bf41d42df17118d7d65f6cd996a4401e806beb0e0ee3a6ec2de88a4e45aefbba  KenshiLib_v0.4.0.zip
```

Upstream release digests — recompute after download before use.

## Build and staging

```powershell
.\scripts\build_native.ps1
```

Release x64 on VS2010 SP1. The only expected compiler warning is C4091 in the
upstream MyGUI header `BaseLayout.h`. Staged package layout:

```text
KenshiAgentTelemetry/
  KenshiAgentTelemetry.dll
  KenshiAgentTelemetry.mod
  RE_Kenshi.json
```

Staging lives under the ignored local `staging\KenshiAgentTelemetry`. Rebuild and
re-stage before each install, and never infer installed or live state from that
directory alone. Back up the DLL being replaced, and record the replaced and
installed digests in the commit that makes the change.

Updating any pinned row above is a deliberate act, not a side effect. See
[`GUIDE_WINDOWS_NATIVE_SETUP.md`](GUIDE_WINDOWS_NATIVE_SETUP.md) for the toolchain
install procedure.
