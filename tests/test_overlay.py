from __future__ import annotations

from kenshi_agent.overlay import format_event


def test_format_event_renders_decision_for_overlay() -> None:
    rendered = format_event(
        {
            "event_type": "decision",
            "step_index": 4,
            "payload": {
                "source": "planner",
                "planner_latency_seconds": 1.234,
                "decision": {
                    "intent": "Scout the nearby road.",
                    "rationale": "It is visible and close to The Hub.",
                    "action": {
                        "kind": "skill",
                        "name": "move_visible_terrain",
                        "args": {"x": 0.45, "y": 0.5},
                    },
                    "confidence": 0.75,
                    "memory_writes": [],
                },
            },
        }
    )

    assert rendered is not None
    assert "step 04 | DECIDE 1.23s | planner" in rendered
    assert "WHY     It is visible and close to The Hub." in rendered
    assert "ACTION  move_visible_terrain(x=0.45, y=0.5)" in rendered


def test_format_event_ignores_observations() -> None:
    assert format_event({"event_type": "observation", "payload": {}}) is None
