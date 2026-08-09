# Checkpoint: reproducible current main

This checkpoint is the durable state boundary for the current goal commit. It
describes the actual `main` parent from which this slice was made, the portable gate,
the native artifact currently installed, and the behavior that remains unproven.

## Repository

```text
parent commit          965a4952b0ad204ee8daf2abbce5101f2853bb95
integration branch     main
remote state           main was one commit ahead of origin/main
tree state             clean before this slice
supported Python       CPython 3.11, 3.12, 3.13, and 3.14
package constraint     >=3.11,<3.15
native protocol        1.18.0
```

Commit `965a495` made this checkpoint a tested part of every future goal. This
follow-on gives the hosted playing model one current-affordance output contract and
separates that policy from the multi-step ceiling for runtime-authored envelopes.

The checkpoint revision is the commit containing this file. Its literal hash cannot
be embedded without changing itself, so the repository block records its parent.
The portable test accepts an in-progress candidate only when this file is modified
and its parent equals the current `HEAD`. In a clean checkout it requires this file
to have been modified by `HEAD` and the recorded parent to equal `HEAD^`.

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
closed version set. CI checks out the source commit rather than GitHub's synthetic PR
merge commit and fetches full history so the checkpoint ratchet has authoritative
commit ancestry.

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

- `PlannerOutputPolicy.current_affordances_per_deliberation` is a typed literal and
  owns the Pydantic bounds, schema description and example shape, injected prompt
  text, per-call request, token allowance, and compiler cardinality diagnostic.
- The prompt template contains one injection marker and no independent numeric
  output rule. Both hosted adapters use the shared renderer and request builder.
- `compile_hosted_plan_proposal` no longer converts a current choice into a future
  `PlanPatch`; it rejects planning against `active_plan`, and both canonical configs
  disable concurrent option planning.
- `max_runtime_plan_steps` now names the distinct internal-envelope bound. The
  superseded `max_plan_steps` planner-looking configuration and constructor arguments
  are absent.
- `pyproject.toml` and `uv.lock` reject Python before 3.11 and at or after 3.15.
- `./dev verify-portable` is a portable-only branch in the WSL entrypoint. It does not
  locate Windows Python, start a transport, synthesize input, or touch Kenshi.
- `scripts/export_docs.py` is the sole writer for all checked-in generated Markdown,
  including `DEV_CLI.md`; `scripts/export_schemas.py` is the sole schema writer.
- The workflow matrix and local command share the same gate implementation rather
  than restating its commands in YAML.
- `tests/test_checkpoint_freshness.py` makes a checkpoint edit mandatory in every
  dirty goal candidate and every clean completed goal commit.
- Native source still declares protocol 1.18.0 and the generated capability header
  still matches its 43-entry manifest.

### Test-proven

- A structural test compares the typed policy with the schema min/max, generated
  schema example, rendered prompt, request text, and exact compiler rejection. It
  also proves that configuration cannot select another cardinality.
- Hosted-adapter tests prove an active-plan response cannot reserve a future
  affordance, while a broad objective remains valid beside the current selection.
- The full portable gate passes independently under every declared Python version.
- The test suite renders schemas and generated documents into temporary directories,
  compares their exact file sets and bytes with the checkout, and checks the generated
  `./dev` reference through the same documentation exporter.
- Portable-gate tests pin the complete command sequence and prove that a generated
  byte change fails the gate.
- The checkpoint ratchet proves this file belongs to the current goal commit and
  records that commit's exact parent and intended integration branch.
- Six strict xfails remain explicit reconstruction targets; they are not counted as
  evidence for the behavior they name.

### Live-proven

This slice adds no live-game proof and makes no new claim about Kenshi behavior. The
planner contract is source- and portable-test-proven only. The latest durable
gameplay evidence remains the
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

- No native source, command fixture, protocol, built DLL, installed DLL, or live run
  changed. Native request/acknowledgement/later-engine evidence is therefore not
  applicable to this portable planner-contract slice.
- A runtime-authored or scripted plan may still contain multiple steps under
  `max_runtime_plan_steps`; that is deliberately not permission for the hosted
  playing model to select more than the current affordance.
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
