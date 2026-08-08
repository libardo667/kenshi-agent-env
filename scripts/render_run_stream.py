#!/usr/bin/env python3
"""Render one run bundle as a phone-readable turn stream.

Written for watching a live run from somewhere else. The operator cannot see the
machine, so the page leads with what went wrong and only then lists what
happened - the opposite order from the event log, which is chronological and
buries a refusal a thousand lines deep.

Usage: python scripts/render_run_stream.py [RUN_ID] [-o OUT.html]
"""

from __future__ import annotations

import argparse
import collections
import html
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


@dataclass
class Turn:
    plan_id: str
    step_index: int | None = None
    objective: str = ""
    action: str = ""
    target: str = ""
    status: str = "pending"
    reason: str = ""
    latency: float | None = None
    sequence: int | None = None
    lifecycle: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Run:
    run_id: str
    objective: str = ""
    control_mode: str = ""
    steps_completed: int = 0
    stop_reason: str = ""
    terminated: bool = False
    turns: list[Turn] = field(default_factory=list)

    fallbacks: collections.Counter[str] = field(default_factory=collections.Counter)

    @property
    def rough(self) -> list[Turn]:
        return [t for t in self.turns if t.status in {"failed", "aborted", "rejected"}]


def _read(run_id: str) -> Run:
    path = RUNS / run_id / "events.jsonl"
    run = Run(run_id=run_id)
    by_plan: dict[str, Turn] = {}
    order: list[str] = []

    def turn_for(plan_id: str) -> Turn:
        if plan_id not in by_plan:
            by_plan[plan_id] = Turn(plan_id=plan_id)
            order.append(plan_id)
        return by_plan[plan_id]

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("event_type")
        payload = event.get("payload") or {}
        plan_id = payload.get("plan_id")

        if kind == "run_started":
            run.control_mode = str(payload.get("control_mode") or "")
        elif kind == "observation" and not run.objective:
            run.objective = str(payload.get("objective") or "")
        elif kind == "strategic_planner_call" and plan_id is None:
            latency = payload.get("planner_latency_seconds")
            if order and latency is not None:
                by_plan[order[-1]].latency = float(latency)
        elif kind == "plan_proposed" and plan_id:
            turn = turn_for(plan_id)
            plan = (payload.get("evidence") or {}).get("plan") or {}
            turn.objective = str(plan.get("objective") or "")
            turn.step_index = event.get("step_index")
            revision = payload.get("world_revision") or {}
            turn.sequence = revision.get("telemetry_sequence")
        elif kind == "affordance_receipt" and plan_id:
            turn = turn_for(plan_id)
            receipt = payload.get("receipt") or {}
            affordance = receipt.get("affordance") or {}
            turn.action = str(affordance.get("operation_kind") or affordance.get("semantic") or "")
            target = affordance.get("target") or {}
            turn.target = str(target.get("label") or "")
            turn.status = str(receipt.get("status") or turn.status)
            turn.reason = str(receipt.get("message") or turn.reason)
            turn.lifecycle = [
                (str(s.get("status")), str(s.get("detail") or ""))
                for s in receipt.get("lifecycle") or []
            ]
        elif kind == "action_receipt":
            action = payload.get("action") or {}
            target_plan = (payload.get("input_boundary") or {}).get("plan_id")
            turn = turn_for(target_plan) if target_plan else (by_plan[order[-1]] if order else None)
            if turn is not None and not turn.action:
                turn.action = str(action.get("kind") or "")
        elif kind in {"plan_step_failed", "plan_aborted", "plan_rejected"} and plan_id:
            turn = turn_for(plan_id)
            turn.status = "aborted" if kind == "plan_aborted" else "failed"
            turn.reason = str(payload.get("reason") or turn.reason)
        elif kind == "plan_completed" and plan_id:
            turn = turn_for(plan_id)
            if turn.status == "pending":
                turn.status = "succeeded"
        elif kind == "planner_transport":
            # A proposal the runtime could not use. These turns *succeed* - the
            # planner substitutes an observe and carries on - so nothing lands in
            # the failure band and a permanently stuck run reads as a quiet
            # sequence of noops. Watched from a phone, that is indistinguishable
            # from the agent thinking.
            reason = payload.get("proposal_fallback_reason")
            if isinstance(reason, str) and reason:
                run.fallbacks[reason] += 1
        elif kind == "run_finished":
            run.steps_completed = int(payload.get("steps_completed") or 0)
            run.stop_reason = str(payload.get("stop_reason") or "")
            run.terminated = bool(payload.get("terminated"))

    run.turns = [by_plan[p] for p in order]
    return run


def _latest_run_id() -> str:
    candidates = [d for d in RUNS.iterdir() if (d / "events.jsonl").is_file()]
    if not candidates:
        raise SystemExit("no run bundles found")
    return max(candidates, key=lambda d: d.stat().st_mtime).name


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def render_fragment(run: Run) -> str:
    """Just the parts that change, for polling without a full reload."""

    rough = run.rough
    outcome = "stopped" if run.terminated else "ran to ceiling"
    fallback_band = ""
    if run.fallbacks:
        rows = "\n".join(
            f'<li><span class="tag tag--failed">unusable x{count}</span>'
            f'<p class="rough__why">{_esc(reason)}</p></li>'
            for reason, count in run.fallbacks.most_common(4)
        )
        total = sum(run.fallbacks.values())
        fallback_band = (
            '<section class="rough" aria-label="Proposals the runtime could not use">'
            f'<h2>Unusable proposals <span class="count">{total}</span></h2>'
            f"<ul>{rows}</ul></section>"
        )

    rough_band = ""
    if rough:
        items = "\n".join(
            f'<li><span class="tag tag--{_esc(t.status)}">{_esc(t.status)}</span>'
            f'<span class="rough__what">{_esc(t.action or t.plan_id)}</span>'
            f'<p class="rough__why">{_esc(t.reason or "no reason recorded")}</p></li>'
            for t in rough
        )
        rough_band = (
            f'<section class="rough" aria-label="Needs a look">'
            f'<h2>Needs a look <span class="count">{len(rough)}</span></h2>'
            f"<ul>{items}</ul></section>"
        )

    turn_rows = []
    for index, turn in enumerate(run.turns, start=1):
        detail = ""
        if turn.reason and turn.status != "succeeded":
            detail = f'<p class="turn__reason">{_esc(turn.reason)}</p>'
        elif turn.lifecycle:
            last = turn.lifecycle[-1][1]
            detail = f'<p class="turn__reason turn__reason--quiet">{_esc(last)}</p>'
        meta = []
        if turn.sequence is not None:
            meta.append(f"seq {turn.sequence}")
        if turn.latency is not None:
            meta.append(f"{turn.latency:.1f}s thinking")
        turn_rows.append(
            f'<li class="turn turn--{_esc(turn.status)}">'
            f'<div class="turn__head">'
            f'<span class="turn__n">{index:02d}</span>'
            f'<span class="turn__action">{_esc(turn.action or "—")}</span>'
            f'<span class="tag tag--{_esc(turn.status)}">{_esc(turn.status)}</span>'
            f"</div>"
            + (f'<p class="turn__objective">{_esc(turn.objective)}</p>' if turn.objective else "")
            + (f'<p class="turn__target">on {_esc(turn.target)}</p>' if turn.target else "")
            + detail
            + (f'<p class="turn__meta">{_esc(" · ".join(meta))}</p>' if meta else "")
            + "</li>"
        )

    return FRAGMENT.format(
        run_id=_esc(run.run_id),
        objective=_esc(run.objective or "no objective recorded"),
        control_mode=_esc(run.control_mode or "unknown"),
        steps=run.steps_completed,
        turn_count=len(run.turns),
        rough_count=len(rough),
        outcome=_esc(outcome),
        stop_reason=_esc(run.stop_reason or "still running"),
        rough_band=fallback_band + rough_band,
        turns="\n".join(turn_rows),
    )


STYLE = """<title>Run {run_id}</title>
<style>
:root {{
  --bone: #f1ebe0; --ground: #f7f3ea; --ink: #1a1613; --dust: #7d746b;
  --rule: #ddd3c4; --rust: #a8442a; --ok: #4a6b3f; --warn: #b0812a; --fail: #8f2f22;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --sans: ui-sans-serif, system-ui, "Segoe UI", Helvetica, sans-serif;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bone: #14110e; --ground: #1c1815; --ink: #ece4d7; --dust: #9a9086;
    --rule: #322b24; --rust: #d2704f; --ok: #7fa06d; --warn: #d5a54a; --fail: #d2685a; }}
}}
:root[data-theme="dark"] {{ --bone: #14110e; --ground: #1c1815; --ink: #ece4d7; --dust: #9a9086;
  --rule: #322b24; --rust: #d2704f; --ok: #7fa06d; --warn: #d5a54a; --fail: #d2685a; }}
:root[data-theme="light"] {{ --bone: #f1ebe0; --ground: #f7f3ea; --ink: #1a1613; --dust: #7d746b;
  --rule: #ddd3c4; --rust: #a8442a; --ok: #4a6b3f; --warn: #b0812a; --fail: #8f2f22; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bone); color: var(--ink); font-family: var(--sans);
  font-size: 17px; line-height: 1.5; -webkit-text-size-adjust: 100%; }}
.wrap {{ max-width: 44rem; margin: 0 auto; padding: 0 0.75rem 4rem; }}
@media (min-width: 34rem) {{ .wrap {{ padding: 0 1.25rem 4rem; }} }}
header {{ position: sticky; top: 0; z-index: 5; background: var(--bone);
  border-bottom: 1px solid var(--rule); padding: 0.9rem 0 0.7rem; }}
.runid {{ font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--dust); }}
h1 {{ font-size: 1.25rem; line-height: 1.35; margin: 0.35rem 0 0.6rem;
  font-weight: 600; text-wrap: balance; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 0.4rem 1rem; font-family: var(--mono);
  font-size: 0.75rem; color: var(--dust); font-variant-numeric: tabular-nums; }}
.stats b {{ color: var(--ink); font-weight: 600; }}
.rough {{ margin: 1.2rem 0 0; border: 1px solid var(--fail); border-radius: 2px;
  background: color-mix(in srgb, var(--fail) 7%, transparent); padding: 0.85rem 0.9rem; }}
.rough h2 {{ margin: 0 0 0.6rem; font-size: 0.78rem; font-family: var(--mono);
  text-transform: uppercase; letter-spacing: 0.09em; color: var(--fail); }}
.rough .count {{ font-variant-numeric: tabular-nums; }}
.rough ul {{ list-style: none; margin: 0; padding: 0; display: flex;
  flex-direction: column; gap: 0.7rem; }}
.rough__what {{ font-family: var(--mono); font-size: 0.85rem; margin-left: 0.5rem; }}
.rough__why {{ margin: 0.25rem 0 0; font-size: 0.86rem; color: var(--dust); }}
.stream {{ list-style: none; margin: 1.4rem 0 0; padding: 0;
  display: flex; flex-direction: column; gap: 0.7rem; }}
.turn {{ background: var(--ground); border: 1px solid var(--rule);
  border-left: 4px solid var(--dust); border-radius: 2px; padding: 0.85rem 0.9rem; }}
.turn--succeeded {{ border-left-color: var(--ok); }}
.turn--failed, .turn--aborted, .turn--rejected {{ border-left-color: var(--fail); }}
.turn--pending {{ border-left-color: var(--warn); }}
.turn__head {{ display: flex; align-items: baseline; gap: 0.55rem; }}
.turn__n {{ font-family: var(--mono); font-size: 0.78rem; color: var(--dust);
  font-variant-numeric: tabular-nums; }}
.turn__action {{ font-family: var(--mono); font-size: 0.98rem; font-weight: 600;
  overflow-wrap: anywhere; }}
.tag {{ margin-left: auto; font-family: var(--mono); font-size: 0.66rem;
  text-transform: uppercase; letter-spacing: 0.07em; padding: 0.12rem 0.4rem;
  border: 1px solid currentColor; border-radius: 999px; white-space: nowrap; }}
.tag--succeeded {{ color: var(--ok); }}
.tag--failed, .tag--aborted, .tag--rejected {{ color: var(--fail); }}
.tag--pending {{ color: var(--warn); }}
.turn__objective {{ margin: 0.5rem 0 0; font-size: 1rem; }}
.turn__target {{ margin: 0.2rem 0 0; font-size: 0.85rem; color: var(--rust);
  font-family: var(--mono); }}
.turn__reason {{ margin: 0.4rem 0 0; font-size: 0.85rem; color: var(--fail); }}
.turn__reason--quiet {{ color: var(--dust); }}
.turn__meta {{ margin: 0.45rem 0 0; font-family: var(--mono); font-size: 0.7rem;
  color: var(--dust); font-variant-numeric: tabular-nums; }}
footer {{ margin-top: 1.6rem; padding-top: 0.9rem; border-top: 1px solid var(--rule);
  font-family: var(--mono); font-size: 0.72rem; color: var(--dust); }}
</style>
"""

FRAGMENT = """
  <header>
    <div class="runid">{run_id} · {control_mode}</div>
    <h1>{objective}</h1>
    <div class="stats">
      <span><b>{steps}</b> steps</span>
      <span><b>{turn_count}</b> turns</span>
      <span><b>{rough_count}</b> rough</span>
      <span>{outcome}</span>
    </div>
  </header>
  {rough_band}
  <ol class="stream">
{turns}
  </ol>
  <footer>Ended: {stop_reason}</footer>
"""


def render_document(run: Run) -> str:
    """A complete standalone document, for serving this ourselves.

    The viewport meta is the whole point of this wrapper: without it a phone
    lays the page out at a notional ~980px and scales the result down, so every
    turn renders as unreadable thumbnail text. An artifact gets a head from the
    publish wrapper; a page served from our own socket gets nothing unless it
    says so.
    """

    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>Run {_esc(run.run_id)}</title>\n"
        "</head>\n<body>\n" + render_page(run) + "\n</body>\n</html>\n"
    )


def render_page(run: Run) -> str:
    """The whole document: shell, styles, and the live region inside it."""

    return (
        STYLE.format(run_id=_esc(run.run_id))
        + '<div class="wrap" id="live">'
        + render_fragment(run)
        + "</div>"
        + LIVE_SCRIPT
    )


LIVE_SCRIPT = """
<script>
(function () {
  var live = document.getElementById("live");
  var failures = 0;
  function tick() {
    fetch("fragment", {cache: "no-store"})
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        failures = 0;
        document.documentElement.classList.remove("offline");
        var atBottom =
          window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
        live.innerHTML = html;
        if (atBottom) window.scrollTo(0, document.body.scrollHeight);
      })
      .catch(function () {
        failures += 1;
        if (failures > 2) document.documentElement.classList.add("offline");
      });
  }
  setInterval(tick, 3000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) tick();
  });
})();
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", nargs="?", default=None)
    parser.add_argument("-o", "--out", default=None)
    args = parser.parse_args()
    run_id = args.run_id or _latest_run_id()
    run = _read(run_id)
    out = Path(args.out) if args.out else ROOT / "runs" / run_id / "stream.html"
    out.write_text(render_page(run), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
