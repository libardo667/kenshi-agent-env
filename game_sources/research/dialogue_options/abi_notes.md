# ABI notes

`DialogueWindow::replyTexts` and `DialogueWindow::dialogue` are layout-sensitive
members whose offsets come from the pinned KenshiLib headers; their confidence
is medium rather than independently recovered from the executable. The public
integer overload of `Dialogue::replyClicked` is preferred over the separate
private string overload.

No `DialogueWindow`, `Dialogue`, reply widget, or caption pointer is retained
across frames. The producer copies captions into project-owned strings. The
dispatch path re-resolves the current window and target, copies the current
ordered captions again, calls the public integer overload once, and retains
only strings and indices while awaiting later engine evidence.
