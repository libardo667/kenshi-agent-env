# Static evidence

The exact executable and KenshiLib identities are recorded in
`call_sites.json`. The pinned KenshiLib headers declare the visible reply rows
as `DialogueWindow::replyTexts` and expose the engine-owned conversation as
`DialogueWindow::dialogue`. They also declare the public
`Dialogue::replyClicked(int)` method at RVA `0x683670`.

Current native source copies every visible reply caption in order. Dispatch
requires the same open dialogue target, an in-range index, and a byte-for-byte
caption match immediately before calling `replyClicked(int)`. The command is
then retained until a later update proves that the dialogue closed, its target
changed, or its complete option list changed.
