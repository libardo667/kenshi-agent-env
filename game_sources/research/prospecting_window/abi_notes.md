# ABI notes

The direct `ZoneManager` calls require an engine-owned `AreaBiomeGroup*`; null
was not a safe stand-in. The current path instead obtains the engine-owned
`ProspectingWindow` singleton, invokes the same public window entry point, and
copies values immediately. It retains no window, line-panel, button, or string
pointer across frames.

`ResourceLinePanel::button` and the `lines` vector are layout-sensitive member
declarations without independent runtime layout recovery. Their signature and
layout confidence is medium. A successful call to `showT` is not completion;
the record is valid only after the window has populated its lines and the
captions have been copied.
