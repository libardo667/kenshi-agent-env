# Checkpoint: reproducible state before the next native and lifecycle changes

This records exactly what works right now, verified rather than asserted, so the
next stage starts from a known position instead of an assumed one.

Written after a live run found three defects at once (see the commit history for
`394b677` and `09e9998`). It is not a documentation exercise: every line below
was produced by running the thing it describes.

## Repository

```text
commit                394b677a292c78d2c5c5c9519d4522f5b3a4c1d1
branch                interaction-scope-order-lifecycle
tree state            DIRTY at capture time
python (gate)         Python 3.12.13
```

## Portable gate

Every check below was run in a **fresh tree extracted from this commit** with an
empty `runs/`, not only in the working directory:

```text
uv run pytest -q                      passed
uv run ruff check .                   passed
uv run mypy src                       passed (151 source files)
uv run python scripts/export_schemas.py   byte-identical to checked-in schemas/
uv run python scripts/export_docs.py      byte-identical to checked-in docs/generated/
```

Generated documentation is reproducible from checked-in inputs. It previously
was not: the reporting-surface report read whichever bundle under `runs/` was
newest, and `runs/` is gitignored, so the committed file could only be
regenerated on the machine that last ran the agent. It now reads a checked-in
slice of a real live run under `tests/fixtures/run_bundles/`.

## Native artefact

The one component the portable gate cannot rebuild or execute. Verified with
`uv run python scripts/check_native_provenance.py`:

```text
declared protocol     1.15.0
source sha256         a24f462808a455631f7e74595629efa3a67eb24ad31b312b5a566ef7a0d9092d
built sha256          c467192f0db8521d3c6ef741d15857049b194bfa42a1ab683351b98a9b737de3
installed sha256      c467192f0db8521d3c6ef741d15857049b194bfa42a1ab683351b98a9b737de3
chain consistent      YES

CHECKS
  [ok] installed DLL declares the source's protocol
       source declares 1.15.0; found in binary
  [ok] installed DLL carries every advertised capability
       39 declared; all present
  [ok] installed DLL is the built DLL
       hashes match
  [ok] generated header matches manifest
       header is current
```

Command request schema: 1.

The hash equality proves the installed file is the built file. The version and
capability strings found inside the binary prove the build carried this source's
declarations. What this does **not** prove is that the built DLL came from this
checkout rather than an identical-looking one; the source hash is recorded for a
human to compare, not silently trusted.

## Target behaviour still outstanding

Six `xfail(strict=True)` tests in `tests/test_interaction_scope_targets.py`
encode Slices 2-4 and are genuinely failing, not silently passing - strict mode
turns an unexpected pass into a suite failure, and the suite is green. Slice 1's
three targets have been retired and assert plainly.

## Unresolved live proof, stated plainly

These are not covered by any gate above and should not be treated as working:

- **Mining has not been proven live since the fix.** The selection-cardinality
  fix in `394b677` is proven by unit tests that fail without it, and the live
  bundle that motivated it is checked in. No live run has issued a successful
  `operate` on a resource with a two-character party.
- **The survey capability has never been exercised live.** It is declared,
  advertised, compiled into the installed binary, and pinned by a wire fixture,
  but no live run has dispatched `survey_local_resources` and received an
  acknowledgement. The command counter has never incremented for it.
- **Two Kenshi crashes remain unexplained.** One GPU device-removal while
  creating a rasterizer state, one silent termination during asset streaming
  after a map travel. Both occurred with ~1.7 GiB of free host memory against
  this project's own 4096 MiB floor. Memory pressure is the better-supported
  hypothesis and is not proven.
- **The launch memory floor does not guard attaching.**
  `min_free_physical_memory_mib` is checked only in
  `_validate_launch_preconditions`. Both crashed runs attached to an
  already-loaded game, so headroom was never checked - the exact configuration
  that crashed twice.
- **`respond_to_immediate_threat` requires `nearby.visible_entities`**, which no
  producer advertises. It is correctly withheld from the agent rather than
  offered and broken.

## Restoring this state

```bash
git checkout 394b677
uv sync --extra dev
uv run pytest -q && uv run ruff check . && uv run mypy src
uv run python scripts/check_native_provenance.py
```

The last command needs a Windows Kenshi installation; the first three do not.
