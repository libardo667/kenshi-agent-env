from kenshi_agent.config import load_config
from kenshi_agent.live_dev import _telemetry_read

s = _telemetry_read(load_config("config/live.longform.yaml")).read().snapshot
cells = [c for c in (s.ui.visible_controls or []) if c.role == "item"]
from collections import Counter
dupes = [lbl for lbl, n in Counter(c.label for c in cells).items() if n > 1]
print("duplicate labels:", dupes)
for lbl in dupes:
    print(f"\n{lbl!r}:")
    for c in [x for x in cells if x.label == lbl]:
        print(f"   window={c.window!r} name={c.item_name!r} value={c.item_value} "
              f"qty={c.item_quantity} type={c.item_type!r} section={c.section!r} "
              f"bounds=({c.bounds.min_x:.3f},{c.bounds.min_y:.3f})")
