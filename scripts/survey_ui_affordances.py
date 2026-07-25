"""Survey what Kenshi's interface actually advertises, screen by screen.

The agent can only bind to references the telemetry exposes, so "what can it
reach?" is an empirical question about MyGUI, not a design question. This tool
opens each major screen, records every advertised control with its role and
bounds, and writes both raw JSON and a readable summary.

It is deliberately a diagnostic, not an agent path: it drives the controller
directly rather than going through plans, policy, or contracts, so the survey
cannot be confused with evidence about what the *agent* can do. It only clicks
controls the interface already advertises, plus Escape to back out.

Usage (Windows Python, Kenshi running and loaded):
    python -m scripts.survey_ui_affordances --config <live config> --out <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kenshi_agent.config import load_config
from kenshi_agent.control.base import InputController
from kenshi_agent.live_dev import _controller, _telemetry_read
from kenshi_agent.models import ClickAction, KeyAction, normalize_control_label
from kenshi_agent.telemetry import TelemetryReader

# Screens reachable from the world HUD by activating one advertised button.
# Each entry is (label to click, human name). Escape backs out of each.
HUD_SCREENS: list[tuple[str, str]] = [
    ("INV", "inventory"),
    ("MAP", "map"),
    ("SQD", "squad"),
    ("STA", "stats"),
    ("TEC", "tech"),
]

SETTLE_SECONDS = 1.2

# Kenshi's ESC menu. Pressing Escape with nothing open *opens* this rather than
# backing out, so the survey must recognize it and leave via RESUME instead of
# pressing Escape again. Its presence in a sample means the sample is not the
# plain world HUD.
ESC_MENU_MARKERS = ("RESUME", "SAVE GAME", "LOAD GAME", "OPTIONS")


def read_snapshot(reader: TelemetryReader) -> Any:
    for _ in range(40):
        try:
            result = reader.read()
        except Exception:
            continue
        if not result.stale:
            return result.snapshot
    raise RuntimeError("Telemetry never became fresh during the survey.")


def describe(snapshot: Any) -> dict[str, Any]:
    ui = snapshot.ui
    controls = ui.visible_controls or []
    labels = Counter(
        (normalize_control_label(control.label), control.role) for control in controls
    )
    return {
        "telemetry_sequence": snapshot.sequence,
        "active_screen": ui.active_screen,
        "modal_open": ui.modal_open,
        "dialogue_open": ui.dialogue_open,
        "tooltip_visible": ui.tooltip_visible,
        "active_shop_trader_count": snapshot.active_shop_trader_count,
        "stats_window_open": ui.stats_window_open,
        "open_inventory_windows": ui.open_inventory_windows,
        "management_screen_open": ui.management_screen_open,
        "management_tab": ui.management_tab,
        "esc_menu_open": all(
            any(control.label == marker for control in controls)
            for marker in ESC_MENU_MARKERS
        ),
        "control_count": len(controls),
        # The plug-in caps the exported set; saturation means real affordances
        # may be crowded out by HUD text, which is the crucial fact for
        # "can the agent reach this screen's contents?".
        "at_export_cap": len(controls) >= 64,
        "buttons": sorted(
            {control.label for control in controls if control.role == "button"}
        ),
        "ambiguous_labels": sorted(
            label for (label, _role), count in labels.items() if count > 1
        ),
        "controls": [
            {
                "label": control.label,
                "role": control.role,
                "bounds": control.bounds.model_dump(),
            }
            for control in controls
        ],
    }


async def click_label(
    controller: InputController,
    reader: TelemetryReader,
    label: str,
) -> bool:
    """Click exactly one advertised control by label; refuse if not unique."""

    snapshot = read_snapshot(reader)
    wanted = normalize_control_label(label)
    matches = [
        control
        for control in (snapshot.ui.visible_controls or [])
        if normalize_control_label(control.label) == wanted
    ]
    if len(matches) != 1:
        return False
    control = matches[0]
    x = (control.bounds.min_x + control.bounds.max_x) / 2.0
    y = (control.bounds.min_y + control.bounds.max_y) / 2.0
    async with controller.input_lease(alt_tab_on_restore=False):
        # Kenshi's MyGUI ignores an instantaneous press.
        await controller.execute(ClickAction(x=x, y=y, hold_seconds=0.12))
    return True


async def press_escape(controller: InputController) -> None:
    async with controller.input_lease(alt_tab_on_restore=False):
        await controller.execute(KeyAction(key="escape", hold_seconds=0.08))


async def survey(config_path: Path, out_dir: Path) -> int:
    config = load_config(config_path)
    # Reuse the launcher's own constructors so the survey drives Kenshi exactly
    # the way every other live path does.
    reader = _telemetry_read(config)
    controller = _controller(config)

    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "screens": {},
    }

    # Whatever is on screen right now, before the survey touches anything.
    results["screens"]["initial"] = describe(read_snapshot(reader))
    print(f"initial: screen={results['screens']['initial']['active_screen']}")

    # Reach a clean world HUD. Escape is not a "back out" key here: with nothing
    # open it opens the ESC menu, so leave that menu by its own RESUME button.
    for _ in range(4):
        current = describe(read_snapshot(reader))
        if current["esc_menu_open"]:
            await click_label(controller, reader, "RESUME")
        elif current["active_screen"] in ("dialogue", "trade", "inventory"):
            await press_escape(controller)
        else:
            break
        await asyncio.sleep(SETTLE_SECONDS)

    world = describe(read_snapshot(reader))
    results["screens"]["world"] = world
    if world["esc_menu_open"]:
        print("WARNING: could not reach a clean world HUD; ESC menu still open.")
    print(f"world: {world['control_count']} controls, esc_menu={world['esc_menu_open']}")

    for label, name in HUD_SCREENS:
        if controller.user_input_detected():
            print("Human input detected; ending the survey with no further input.")
            break
        opened = await click_label(controller, reader, label)
        if not opened:
            results["screens"][name] = {"error": f"{label} was not uniquely advertised"}
            print(f"{name}: {label} not uniquely advertised")
            continue
        await asyncio.sleep(SETTLE_SECONDS)
        described = describe(read_snapshot(reader))
        results["screens"][name] = described
        print(
            f"{name}: screen={described['active_screen']} "
            f"controls={described['control_count']} "
            f"buttons={len(described['buttons'])} "
            f"esc_menu={described['esc_menu_open']} "
            f"cap={'YES' if described['at_export_cap'] else 'no'}"
        )
        # These HUD buttons toggle, so close with the same button rather than
        # Escape, which would open the ESC menu instead.
        await click_label(controller, reader, label)
        await asyncio.sleep(SETTLE_SECONDS)

    raw = out_dir / "ui_affordance_survey.json"
    raw.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {raw}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    return asyncio.run(survey(Path(args.config), Path(args.out)))


if __name__ == "__main__":
    raise SystemExit(main())
