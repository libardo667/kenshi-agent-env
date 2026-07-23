# Live stability incident: DirectX device reset

## Summary

On 2026-07-23, Kenshi presented its generic `BAD STUFF` out-of-video-memory
dialog during the stable-identity live run. The renderer log recorded:

- `0x887A0005`, which the installed Windows SDK defines as
  `DXGI_ERROR_DEVICE_REMOVED`;
- reason `0x887A0020`, which the SDK defines as
  `DXGI_ERROR_DRIVER_INTERNAL_ERROR`; and
- simultaneous failures creating depth-stencil, rasterizer, and sampler state
  for sky, lighting, and water materials.

The stable-identity plugin does not call DirectX or allocate GPU resources. More
importantly, the same DirectX error was reproduced with the prior plugin DLL.
This incident is therefore classified as a renderer/graphics-driver reset under
high shared-memory pressure, not as evidence that stable identity corrupted
Kenshi. That classification does not claim that a plugin can never affect
timing; it records what the controlled evidence rules out.

The save was paused. No gameplay action was dispatched during any incident
run.

## Environment

- Kenshi compatibility executable: `1.0.65 x64`
- Renderer: Direct3D 11
- GPU: Intel Iris Xe Graphics, approximately 2 GiB reported adapter memory
- Driver observed: `32.0.101.6737`
- Window: borderless 1920x1080
- Fast zone hopping: disabled
- Shadows: disabled
- View distance: approximately 4000
- System commit observed during diagnosis: approximately 38.9 GiB of a
  41.8 GiB limit
- Page-file usage observed: approximately 7.5 GiB

Kenshi and the integrated GPU therefore shared a memory-constrained machine.
The Windows System and Reliability logs had no matching event; Kenshi caught
the device-removal error and emitted the decisive details in `kenshi.log`.

## Controlled comparison

| Run | Plugin SHA-256 prefix | Graphics settings | Result |
| --- | --- | --- | --- |
| Original identity run | `2227f3d97124` | Medium textures, all water reflections | Device reset at 10:20:58 after about 7 minutes |
| Prior-DLL baseline | `42a81f27c12e` | Medium textures, all water reflections | Stable for 60 samples over 10m32s, then reproduced the same reset during normal exit |
| Mitigated identity run | `2227f3d97124` | Low textures, water reflections disabled | 52 samples over 9m34s, more than 10 minutes total uptime, fresh telemetry throughout, and clean normal exit |

The prior-DLL baseline held private memory between 4547.3 and 4548.5 MiB and
GPU-local usage between 1171.7 and 2055.7 MiB. The mitigated identity run held
private memory between 4330.3 and 4367.4 MiB and GPU-local usage between 1088.7
and 1848.0 MiB. Neither run showed a monotonic plugin-side memory leak. The
mitigated run remained stable despite physical free memory falling as low as
609.4 MiB.

The baseline and mitigated sample files are retained outside the repository:

- `%LOCALAPPDATA%\KenshiAgent\p5-crash-baseline-soak.csv`
- `%LOCALAPPDATA%\KenshiAgent\p5-crash-identity-mitigated-soak.csv`

## Operational decision

Automated live validation now uses Low texture quality and disabled water
reflections on this machine. After a later recurrence, view distance was
reduced from approximately 4000 to 2500. These settings were changed through
Kenshi's own options/configuration surfaces. The prior configuration is
recoverable from:

`C:\Program Files (x86)\Steam\steamapps\common\Kenshi\settings.cfg.kenshi-agent-pre-gpu-mitigation-20260723T1048`

If the same reset recurs, preserve the dialog and `kenshi.log`, stop the frozen
client, and treat the live stability gate as open again. Reduce view distance
before expanding the native surface further, and evaluate the Intel graphics
driver separately from plugin code.

## Later recurrence and rejected resolution trial

After roughly forty minutes of later P6 work, the mitigated profile produced
the same `BAD STUFF` out-of-video-memory dialog. The installed settings had not
rolled back. The frozen client and complete desktop dialog were preserved at:

`runs/p6-live-continuous-dry-continuation-20260723T2120Z/bad_stuff_desktop.png`

The pre-restart configuration was copied into that run's
`pre-restart-config/` directory. A clean restart at Low textures, disabled
water reflections/shadows, disabled fast zone hopping, and view distance 2500
reported fresh advancing telemetry. At 1280x720, one settled sample showed
approximately 4246 MiB private memory, 1792 MiB GPU-local usage, and only
740 MiB free physical memory. This was not enough evidence to close the
stability gate.

The 1280x720 trial also invalidated the current 1920x1080 UI calibration. The
user observed incorrect startup clicks, then attempted to interrupt while the
developer launcher repeatedly reclaimed focus. Inspection found that
`live_dev` had disabled polite input, called the controller outside an input
lease, retried the title sequence every four seconds, and made an additional
post-load click. The game was paused and stopped, and the renderer was restored
to 1920x1080 while retaining view distance 2500.

The rejected trial establishes two operational rules:

- graphics mitigations must preserve the calibrated client size until a new
  profile is explicitly calibrated;
- launcher input must be human-interruptible and non-retrying, and calibrated
  pointer actions must recheck exact client dimensions inside the acquired
  input lease.

Portable tests now cover both zero-input failure paths. A fresh supervised
Windows launch remains required before treating the launcher fix or reduced
view-distance profile as live-validated.

## Effect on P5 evidence

The earlier identity assertions remain valid: strict protocol parsing, exact
selection agreement, duplicate-name separation, and identity-set stability
were all observed before the reset. This incident adds a separate stability
qualification. Stable identity passed a subsequent mitigated soak and clean
exit; broad or long-duration live stability is still not claimed.
