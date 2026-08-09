# Checkpoint: reproducible current main

This checkpoint replaces the pre-integration closure candidate. It describes the
actual `main` checkout from which the reproducibility slice was made, the single
portable gate now used locally and in CI, the native artifact currently installed,
and the behavior that remains unproven.

## Repository

```text
main at slice start    6e8b7d13b1b228917513c27f186245a9af0bb8cd
branch                 main
remote state           matched origin/main
tree state             clean before this slice
supported Python       CPython 3.11, 3.12, 3.13, and 3.14
package constraint     >=3.11,<3.15
native protocol        1.18.0
```

Commit `59e5e0b` integrated the reconstructed operation-surface closure into
`main`; commit `6e8b7d1` then changed only `README.md`. The old checkpoint's
`a73e06c` base, `interaction-scope-order-lifecycle` branch, and dirty-candidate
state are historical and no longer authoritative.

The checkpoint revision is the commit containing this file. The literal hash above
is the verified `main` parent on which that revision is based; embedding a commit's
own hash in its contents would change the hash it claims to record.

## Portable gate

The one supported command is:

```bash
./dev verify-portable
```

It installs the development dependency set from `uv.lock`, runs Ruff over the whole
tree, runs strict mypy over `src`, regenerates every JSON schema, regenerates every
document under `docs/generated/`, rejects any generated-byte change, runs the full
pytest suite, and rejects whitespace errors. The partial PowerShell test script and
separate DEV CLI documentation writer were deleted rather than retained as partial
or competing gates.

`.github/workflows/portable.yml` runs this exact command on Linux for each declared
Python version: 3.11, 3.12, 3.13, and 3.14. `pyproject.toml`, `uv.lock`, the Windows
bootstrap guards, the README, the local command, and the workflow all state the same
closed version set.

## Native artifact

No native source, fixture, protocol, build, or installation changed in this slice.
Read-only provenance was rechecked against the current source, build output, and
installed DLL:

```text
declared protocol     1.18.0
source sha256         e1be701af23f4002be81b663791c83753b20122eb3127ad9a0fa7763a0758e2f
built sha256          048f1726c068da362c0fd3601387b6ed8858f65216f1e15c0cbe92d685df73bc
installed sha256      048f1726c068da362c0fd3601387b6ed8858f65216f1e15c0cbe92d685df73bc
declared capabilities 43
chain consistent      YES
```

The matching DLL hashes prove that the installed file is the recorded build output.
The embedded protocol and capability strings prove that binary carries the declared
source contract. They do not prove that any command changed live game state.

## Evidence classification

### Source-proven

- `pyproject.toml` and `uv.lock` reject Python before 3.11 and at or after 3.15.
- `./dev verify-portable` is a portable-only branch in the WSL entrypoint. It does not
  locate Windows Python, start a transport, synthesize input, or touch Kenshi.
- `scripts/export_docs.py` is the sole writer for all checked-in generated Markdown,
  including `DEV_CLI.md`; `scripts/export_schemas.py` is the sole schema writer.
- The workflow matrix and local command share the same gate implementation rather
  than restating its commands in YAML.
- Native source still declares protocol 1.18.0 and the generated capability header
  still matches its 43-entry manifest.

### Test-proven

- The full portable gate passes independently under every declared Python version.
- The test suite renders schemas and generated documents into temporary directories,
  compares their exact file sets and bytes with the checkout, and checks the generated
  `./dev` reference through the same documentation exporter.
- Portable-gate tests pin the complete command sequence and prove that a generated
  byte change fails the gate.
- Six strict xfails remain explicit reconstruction targets; they are not counted as
  evidence for the behavior they name.

### Live-proven

This slice adds no live-game proof. The latest durable gameplay evidence remains the
named bundles already classified in
`docs/reconstruction/interaction_proof_status.json`:

- `closure-loot-20260808` for paired-inventory opening and equipped-item transfer;
- `closure-harvest-fixed-20260808` for adopting existing two-character resource work,
  observing `resource_output_ready`, opening the resource inventory, and transferring
  one `Raw Iron`;
- recovery acknowledgements `cmd-bec439c9589c4a4fb4fc72a06b561722` and
  `cmd-e5d5b8b05f354f768f4b14866c2c2d79` for closing those exact trade windows.

Those conclusions depend on later engine evidence recorded in the bundles, not on a
request returning or an acknowledgement alone.

### Withheld and surviving limitations

- Python 3.15 and later are unsupported until added to both the package range and the
  passing portable matrix. Python 3.10 and earlier remain unsupported.
- GitHub-hosted runs prove the portable Linux surface. They do not prove Windows live
  transport, native compilation, native fixture execution, DLL installation, or live
  Kenshi behavior on every supported Python version.
- `close_trade_window` remains a recovery-only native command, not a planner-visible
  general close-window operation.
- The proof ledger still withholds several navigation, threat-response,
  current-selection, group-recipient, and order-lifecycle conclusions. One successful
  two-person resource run does not generalize across those cases.
- The generated interface audit still names human UI surfaces that are not
  planner-visible. The deleted pointer handlers are not a fallback for those gaps.
- `survey_local_resources` remains source-proven but not live-proven for its null-biome
  call or returned value scale.
