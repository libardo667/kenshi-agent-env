# Guide: attended mutation testing

Mutation testing asks whether the suite notices wrong production behavior. It is
not a coverage percentage and a green `pytest` run is not a substitute.

## Install and discover

Install the development dependencies, then list the complete production scope:

```bash
uv sync --extra dev
./mutate list
```

The list is one non-overlapping shard per production Python module. `__init__.py`
and `__main__.py` contain import or entrypoint plumbing and are excluded.

## Run one attended shard

```bash
./mutate run memory
```

The wrapper:

1. creates `.mutation-workspaces/<module>/`;
2. scopes `mutmut` to exactly that module;
3. fingerprints source, tests, configuration, and copied project inputs;
4. discards the generated tree when any input changes or generation is partial,
   so removed files and bytecode cannot survive into the next campaign;
5. runs the shard and writes a transient JSON result under `runs/mutation/`;
6. exits nonzero for an empty result or any status other than `killed` or
   `caught by type check`.

Use cached results without rerunning mutants:

```bash
./mutate results memory
```

Both commands are gates by default. During the first classification pass only,
`--allow-actionable` permits a successful shell exit while still printing every
result that needs attention:

```bash
./mutate run memory --allow-actionable
```

Never use that flag as a merge or completion gate.

`mutmut` skips behavior inside decorated classes. An empty shard is therefore
not evidence even when ordinary tests execute that class. Preserve the public
data contract while moving executable behavior into mutation-visible functions
or undecorated classes, then require a nonempty strict campaign.

## Attend every result

For each survivor, timeout, or missing-test result:

- inspect the exact mutation with `mutmut show <mutant>` from the shard workspace;
- decide whether it exposes missing behavior, equivalent behavior, or diagnostic
  presentation;
- for missing behavior, add the invariant and rerun the exact mutant red before
  rerunning the shard;
- simplify equivalent implementation choices when possible;
- exclude only declarative data or diagnostic presentation, narrowly, with an
  adjacent rationale and behavioral tests around the consuming boundary.

Do not exclude a branch, authority check, state transition, identity comparison,
or conservation rule because it is inconvenient to kill.

## Completion

A shard is complete only when the same checkout passes without
`--allow-actionable`:

```bash
./mutate run memory
```

Then run the ordinary project gates. Any later change to source, tests, config,
or copied inputs invalidates the shard's cached associations, so rerun affected
claims before citing them.
