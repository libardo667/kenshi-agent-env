# Upstream and host lock

Verified on 2026-07-22 before modifying the Kenshi installation. Paths are kept
generic where possible; hashes and commits are the reproducibility anchors.

| Component | Version/tag/commit | Source or installed path | Verified UTC | Notes |
|---|---|---|---|---|
| Kenshi executable | 1.0.68 Steam, build 13871665 | `C:\Program Files (x86)\Steam\steamapps\common\Kenshi\kenshi_x64.exe` | 2026-07-22 | SHA-256 `a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1` |
| RE_Kenshi | v0.3.4, tag commit `be107d258618974d56b7373f0f86c82daa2196a9` | [upstream release](https://github.com/BFrizzleFoShizzle/RE_Kenshi/releases/tag/v0.3.4) | 2026-07-22 | Not installed; upstream lists Kenshi 1.0.68 Steam as supported |
| KenshiLib | v0.4.0, tag commit `18f75fecb93cfead6029efe0d5fe199d6618bcc9` | [upstream release](https://github.com/BFrizzleFoShizzle/KenshiLib/releases/tag/v0.4.0) | 2026-07-22 | Not installed; upstream says this version ships with RE_Kenshi v0.3.4 |
| KenshiLib examples | `548b3eaf779c1b2feb25416f1db757320d04ec6c` | [upstream repository](https://github.com/BFrizzleFoShizzle/KenshiLib_Examples) | 2026-07-22 | Dependency layout reference |
| Example dependencies | `b566d74bf3d74629cc2fb632a97595b8202993f1` | [upstream repository](https://github.com/BFrizzleFoShizzle/KenshiLib_Examples_deps) | 2026-07-22 | Not downloaded |
| Visual Studio Build Tools | 2022 17.14.35 | Windows installation | 2026-07-22 | MSBuild present |
| Visual C++ v100 x64 toolset | compiler 16.00.40219.01, VS2010 SP1 | `C:\Program Files (x86)\Microsoft Visual Studio 10.0\VC` | 2026-07-22 | x64 compiler and `v100` MSBuild integration pass native doctor |
| Windows SDK | 7.0A for VS2010; Windows 10 SDK 10.0.26100.0 for VS2022 | Windows installation | 2026-07-22 | Standalone Windows SDK 7.1 is not installed |
| Python | CPython 3.12.13 x64, uv-managed | Windows user installation | 2026-07-22 | `python` resolves successfully; `py -3.11` is unavailable |

The current upstream examples require Boost 1.60 headers and v100 libraries in
addition to KenshiLib. The maintained examples also link plugins with
`kenshilib.lib` and use an empty `.mod` file plus `RE_Kenshi.json` beside the
plugin DLL.

## Active mods during validation

- Not yet recorded. Native installation and live validation have not started.

## Dependency bundle checksums

```text
a5df733f576eade3c3293ca5c4dd2764fd334f9557743962cf5dc6cb03395bc3  RE_Kenshi_v0.3.4.zip
bf41d42df17118d7d65f6cd996a4401e806beb0e0ee3a6ec2de88a4e45aefbba  KenshiLib_v0.4.0.zip
```

These are upstream release digests. Recompute them after download before use.

## Native build command

```powershell
# Blocked until KENSHILIB_DIR and BOOST_INCLUDE_PATH are provisioned.
```

## Plugin staging/install layout

```text
KenshiAgentTelemetry/
  KenshiAgentTelemetry.dll
  KenshiAgentTelemetry.mod
  RE_Kenshi.json
```
