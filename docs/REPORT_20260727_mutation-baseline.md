# Report: first attended mutation baseline

## Question

Could the existing Python suite detect wrong behavior, and could mutation testing
run repeatably across the project on this host?

## Monolithic attempt

An initial project-wide `mutmut` run generated 30,722 mutants. Test association
had not reached target execution after 19 minutes and consumed about 1.9 GiB.
The run was stopped without a result. A single global cache was not a viable
attended feedback loop on this device.

## Sharded design

The runner discovers 62 non-overlapping production-module shards and gives each
an isolated workspace and association cache. Its input fingerprint covers all
configured source, tests, configuration, documentation, and copied resources.
Changed inputs invalidate cached associations. A zero-result shard and every
actionable `mutmut` status fail closed.

The runner's own baseline was 510 mutants: 206 killed, 249 with no associated
test, and 55 survivors. CLI orchestration, artifact writing, symlink identity,
cache invalidation, and failure propagation were then specified behaviorally.
Narrow exclusions cover only declarative CLI help, diagnostic wording, and
equivalent cache serialization choices. The resulting runner campaign killed
368 of 368 generated mutants.

## Production findings

Three behaviorally different modules were attended:

| Module | Initial result | Final result |
| --- | ---: | ---: |
| `resource_transfer` | 176 killed / 62 survived | 197 killed / 0 actionable |
| `memory` | 144 killed / 76 survived | 274 killed / 0 actionable |
| `affordance_requests` | 365 killed / 105 survived | 435 killed / 0 actionable |

The survivors exposed real missing invariants: destination ownership and
conservation, recall partitioning and chunk boundaries, demand-merge truth
tables, invalid scenario identities, representative selection, and exact
coverage accounting.

SQL text is declarative input to SQLite, so it is excluded from Python mutation
after behavior-level schema, ordering, partition, round-trip, and idempotence
tests. Diagnostic-only exception and evidence prose is excluded where changing
the wording cannot alter authority or state. These exclusions do not cover
branches or values used to make decisions.

## Conclusion

Mutation testing found confidence gaps that test count and line execution did
not. Module sharding makes the work bounded and resumable, and the wrapper itself
now fails closed.

This is not project-wide mutation adequacy. Four of 62 shards are attended:
the runner plus resource transfer, memory, and affordance aggregation. The
remaining production modules require the same survivor-by-survivor treatment.
