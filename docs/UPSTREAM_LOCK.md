# Upstream and host lock

Verified on 2026-07-22 during the first native build and live bring-up. Paths are
kept generic where possible; hashes and commits are the reproducibility anchors.

| Component | Version/tag/commit | Source or installed path | Verified UTC | Notes |
|---|---|---|---|---|
| Kenshi executable | 1.0.68 Steam, build 13871665 | `C:\Program Files (x86)\Steam\steamapps\common\Kenshi\kenshi_x64.exe` | 2026-07-22 | SHA-256 `a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1` |
| RE_Kenshi | v0.3.4, tag commit `be107d258618974d56b7373f0f86c82daa2196a9` | [upstream release](https://github.com/BFrizzleFoShizzle/RE_Kenshi/releases/tag/v0.3.4) | 2026-07-22 | Installed; log reports RE_Kenshi 0.3.4 and supported Steam 1.0.65 compatibility runtime |
| KenshiLib | v0.4.0, tag commit `18f75fecb93cfead6029efe0d5fe199d6618bcc9` | [upstream release](https://github.com/BFrizzleFoShizzle/KenshiLib/releases/tag/v0.4.0) | 2026-07-22 | Installed with RE_Kenshi; log reports KenshiLib 0.4.0 and loaded RVAs |
| KenshiLib examples | `548b3eaf779c1b2feb25416f1db757320d04ec6c` | [upstream repository](https://github.com/BFrizzleFoShizzle/KenshiLib_Examples) | 2026-07-22 | Dependency layout reference |
| Example dependencies | `b566d74bf3d74629cc2fb632a97595b8202993f1` | `C:\Hub\Projects\CppProjects\KenshiLib_Examples_deps` | 2026-07-22 | Detached at the pinned commit; `git lfs fsck` passes; Boost archive extracted |
| Visual Studio Build Tools | 2022 17.14.35 | Windows installation | 2026-07-22 | MSBuild present |
| Visual C++ v100 x64 toolset | compiler 16.00.40219.01, VS2010 SP1 | `C:\Program Files (x86)\Microsoft Visual Studio 10.0\VC` | 2026-07-22 | x64 compiler and `v100` MSBuild integration pass native doctor |
| Windows SDK | 7.0A for VS2010; Windows 10 SDK 10.0.26100.0 for VS2022 | Windows installation | 2026-07-22 | Standalone Windows SDK 7.1 is not installed |
| Python | CPython 3.12.13 x64, uv-managed | Windows user installation | 2026-07-22 | `python` resolves successfully; `py -3.11` is unavailable |

The current upstream examples require Boost 1.60 headers and v100 libraries in
addition to KenshiLib. The maintained examples also link plugins with
`kenshilib.lib` and use the same 46-byte native-only `.mod` stub plus
`RE_Kenshi.json` beside the plugin DLL. The stub SHA-256 is
`ebdab65d330e46e1ff9725ac5d0ed87fd8c718cfb41ef85b27b86eb3d35b79c0`.

## Active mods during validation

- `KenshiAgentTelemetry.mod` was the only enabled non-core mod.
- The installed native mod stub SHA-256 was
  `ebdab65d330e46e1ff9725ac5d0ed87fd8c718cfb41ef85b27b86eb3d35b79c0`.
- A pre-install recovery snapshot is at
  `C:\Hub\Archive\kenshi-agent-env\pre-re-kenshi-20260722-103917` on the
  validation host.

## Dependency bundle checksums

```text
a5df733f576eade3c3293ca5c4dd2764fd334f9557743962cf5dc6cb03395bc3  RE_Kenshi_v0.3.4.zip
bf41d42df17118d7d65f6cd996a4401e806beb0e0ee3a6ec2de88a4e45aefbba  KenshiLib_v0.4.0.zip
```

These are upstream release digests. Recompute them after download before use.

## Native build command

```powershell
.\scripts\build_native.ps1
```

Result: VS2010 SP1 `Release | x64` build succeeded. The corrected, live-loaded
DLL SHA-256 was
`61693dc6489f371eb151f638be4eba7c5922086d4ffcf62e421983c6776751e1`.
The only compiler warning was C4091 in upstream MyGUI header `BaseLayout.h`.

The P5 stable-identity rebuild on 2026-07-23 used the same pinned dependency
and compiler set. Release x64 succeeded with only that same upstream C4091
warning. The live-loaded DLL SHA-256 was
`2227f3d97124149917d1c5736fb69bf29100b4ac1d6af4badcb76455ff478e16`.
The previously installed DLL was copied to the Windows-local recovery folder
recorded in the live checklist before replacement.

The protocol `0.3.0` causal-command build uses the same pinned dependencies and
compiler. The offline Release x64 output built on 2026-07-23 is 175,104 bytes
with SHA-256
`9bbeea1826216365c5492ee94db4b692848a105fbb36bc794b02723e953a293b`.
The build emitted the existing upstream MyGUI C4091 warning plus Boost 1.60
property-tree C4715 under whole-program code generation. The identical hash was
installed for the supervised run. The prior `0.2.0` DLL was backed up at
`%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T184326Z-p5-causal`.
RE_Kenshi loaded the new DLL, the bounded causal proof passed, and Kenshi closed
normally without a new plugin, renderer, or Windows Application error.

The additive protocol `0.4.0` conditional-food build uses the same pinned
dependencies/compiler plus the pinned KenshiLib bundle's
`MyGUIEngine_x64.lib` import library for public caption, widget-coordinate, and
render-size APIs. The offline Release x64 output built on 2026-07-23 is 182,784
bytes with SHA-256
`64a3cf3c22fc4ee04152c6a70a143f16cb59e82ebb8d62e5a2cc885acfb77cfe`.
The build emitted only the same upstream MyGUI C4091 and Boost C4715 warnings.
The exact artifact was installed, without launching Kenshi, after the full
protocol `0.3.0` plug-in was copied to
`%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T193734Z-p6-protocol-0.4`.
The installed DLL hash was verified against the offline artifact. Supervised
initial load and fresh-telemetry evidence are recorded in the live checklist.

During the first long protocol `0.4.0` session, the latest snapshot stopped at
sequence 3985 while Kenshi remained foreground, responsive, paused, and
visually intact. A bounded native hotfix makes the sampling latch
exception-safe and retries transient Windows target-replacement sharing/lock
failures four times. The pinned Release x64 rebuild is 183,296 bytes with
SHA-256
`0096082215cbc1f842a8947291570328481c78cab9c23b8ae00a4dcdf6e888a3`.
The full replaced plug-in is recoverable at
`%LOCALAPPDATA%\KenshiAgent\backups\native\20260723T202819Z-p6-stream-hotfix`.
The hotfix retains protocol `0.4.0`; its wire schema and pinned upstream
dependencies are unchanged.

The protocol `0.5.0` semantic-control build uses the same pinned dependencies,
compiler, and MyGUI import library. The first installed 185,344-byte candidate
had SHA-256
`a1ea4c2a3c6c6e596b3bc8654b901511da1808979d49758d49e852bd0ad6da24`.
It loaded safely but could not sample title controls because sampling was tied
to `PlayerInterface::update`. A 186,368-byte direct MyGUI function-detour build
with SHA-256
`ace964357eaa93c8844d1b564447bf85650dba97434f67f7875cdb03f1de88d5`
crashed during startup and is rejected. Its exact dump/binary evidence is under
`runs/p0-title-telemetry-frame-hook-crash-20260723T224758Z/`, and the complete
preinstall package is under
`runs/p0-title-telemetry-hotfix-preinstall-20260723T224713Z/`.

The next replacement used MyGUI's supported `eventFrameStart` subscription
rather than detouring third-party code. Its pinned Release x64 output was
189,440 bytes
with SHA-256
`6bb2af414406cfd708635b74ecb8e742233a556dcb70724ef916e058a5c5da0c`.
The build emitted only the same upstream MyGUI C4091 and Boost C4715 warnings.
It reproduced the same immediate startup crash and is rejected. Exact evidence
is under
`runs/p0-title-telemetry-event-subscription-crash-20260723T230002Z/`, with its
full preinstall package under
`runs/p0-title-telemetry-event-subscription-preinstall-20260723T225933Z/`.

The split-lifecycle replacement hooks Kenshi's pinned
`TitleScreen::_NV_update`, emits a minimal title/control-only snapshot, and
retains loaded-game sampling on `PlayerInterface::update` only after
`GameWorld::initialized`. It neither detours MyGUI nor subscribes to its
delegate list. Its pinned Release x64 output is 188,416 bytes with SHA-256
`33e54224f4b4729ba5b96c85db8b8f81137b5e153a7a97b3d4b8125813a89a7c`.
The build emitted only the same upstream warnings. At this checkpoint the
original 185,344-byte DLL is restored on disk and the split-lifecycle candidate
is uninstalled.

## Plugin staging/install layout

```text
KenshiAgentTelemetry/
  KenshiAgentTelemetry.dll
  KenshiAgentTelemetry.mod
  RE_Kenshi.json
```

The verified package is staged under `staging\KenshiAgentTelemetry` and was
copied to `<Kenshi>\mods\KenshiAgentTelemetry` for live validation.
