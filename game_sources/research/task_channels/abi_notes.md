# ABI notes

All RVAs are relative to the 64-bit Kenshi image. Signatures come from the exact
KenshiLib 0.4.0 headers and successful native linkage; the executable has no
public symbols, so inferred signature confidence remains medium.

The plug-in deliberately calls `ActionDeque` accessors rather than iterating its
public `std::deque` member. Crossing a compiler-owned STL layout would make the
plug-in's container ABI an unproved part of the read. The cost is explicit:
ordinary queues larger than one are truncated with `known_total: null`.

The first ordinary order has source position 0 and the second has position 1.
The separately sampled last task has `position: null`; its address is not a
numeric index. Jobs and permanent Jobs use their directly indexed `lektor`
containers, so retained entries carry their observed index and exact container
size. Any channel truncated by the producer's eight-entry wire bound retains the
exact source total.

An invalid or targetless task subject produces `subject_id: null`. The handle API
does not distinguish those cases, so the producer makes no stronger claim.
`NULL_TASK` is serialized as `current_activity: null`; current activity never
gains a queue position or a fabricated description.

The current singular native command telemetry remains the controller-issued
command record. Matching task names in work telemetry do not establish causal
ownership. Until a later plural registry supplies an exact link, such ordinary
work is reported as observed and unattributed.
