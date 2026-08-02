# Kenshi Wiki text snapshot

This directory is a generated, text-only snapshot of the Kenshi Wiki main
namespace on Fandom. Run `python scripts/sync_kenshi_wiki.py` to refresh it.
Search it without dumping whole JSONL rows with
`python scripts/search_kenshi_wiki.py "shift right-click job"`.

The snapshot intentionally contains current MediaWiki wikitext rather than
images, page chrome, user/talk pages, or historical revisions. Each JSONL row
preserves the article title, canonical URL, page ID, exact revision ID,
revision timestamp, and text. `manifest.json` records the corpus digest and
snapshot boundary.

Wiki text is written by Kenshi Wiki contributors and is reused under the
[Creative Commons Attribution-ShareAlike 3.0 Unported license](https://creativecommons.org/licenses/by-sa/3.0/).
The canonical URL in every row provides article-level attribution and access to
the contributor history. This separately licensed corpus is evidence to
consult, not an assertion that every community claim is correct. Mechanics that
control code, prompt semantics, or completion evidence still require a reviewed
fact citation and, where practical, direct game evidence.
