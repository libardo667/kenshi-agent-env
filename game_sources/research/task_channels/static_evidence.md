# Static evidence

The inspected target is Kenshi 1.0.65 x64, Steam build `13871665`, executable
SHA-256 `a596ab4e407c67b58599c54ffb32dc1bf2b64510cdebd3fa9359ef05a576aeb1`.
KenshiLib 0.4.0 is pinned by commit, library hash, and include-tree hash in
`call_sites.json`. Its three directly relevant headers additionally hash to:

- `AITaskSystem.h`: `3f7396d23e3cbedd0d75669d65af53e05467712e9e3d3b7b802999170af5e5db`;
- `Tasker.h`: `348f80e4f2b335ac0f8fdb995785025aa15f7467a5841606e971eac457c94e1d`;
- `OrdersPanel.h`: `38547ef7168b786bc220f9372804ddfa96f4145cfa119facf4634f9ad167bfad`.

`OrdersReceiver` separately declares `orders`, `jobs`, `permajobs`,
`doJobsEnabled`, and `currentGoal`. The public read surface separately exposes
`hasPlayerOrders()`, `isJobsEnabled()`, and `getCurrentGoal()`. `Tasker` supplies
the task enum key, subject handle, and description.

`ActionDeque` exposes first, second, and last task accessors plus only empty and
one-item predicates. It exposes no size method. The current producer therefore
knows an empty queue and a one-entry queue completely. For a larger queue it can
prove the first two entries and the tail, but not the total or the tail's numeric
position. Those facts are null rather than derived from the sample length.

ForgottenGUI's `OrdersPanel` was inspected as supporting executable/UI evidence.
Its `OrderData` carries a task, index, and enabled flag, while the panel exposes
`moveJob` and `removeJob`. This supports configured/reorderable Jobs as a distinct
UI concept. It is not used as authority for the ordinary queue or current goal.
