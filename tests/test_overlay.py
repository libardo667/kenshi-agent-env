from __future__ import annotations

from kenshi_agent.overlay import format_event, ownership_banner


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


def test_format_event_labels_control_mode_in_run_lifecycle() -> None:
    started = format_event(
        {
            "event_type": "run_started",
            "payload": {"max_steps": 4, "control_mode": "native_assisted"},
        }
    )
    finished = format_event(
        {
            "event_type": "run_finished",
            "payload": {
                "steps_completed": 2,
                "control_mode": "native_assisted",
                "stop_reason": "done",
            },
        }
    )

    assert started is not None and "CONTROL native_assisted" in started
    assert finished is not None and "CONTROL native_assisted" in finished


def test_takeover_countdown_is_prominent_in_feed_and_banner() -> None:
    record = {
        "event_type": "agent_takeover_countdown",
        "payload": {
            "state": "takeover_pending",
            "seconds_remaining": 4,
            "reason": "Do not touch input.",
        },
    }

    rendered = format_event(record)
    banner = ownership_banner(record)

    assert rendered is not None and "AGENT TAKEOVER IN 4s" in rendered
    assert banner is not None and "MOVE MOUSE TO CANCEL" in banner[0]


def test_human_and_disarmed_states_have_distinct_banners() -> None:
    human = ownership_banner(
        {
            "event_type": "control_ownership_changed",
            "payload": {"state": "human_control"},
        }
    )
    disarmed = ownership_banner(
        {
            "event_type": "control_ownership_changed",
            "payload": {"state": "disarmed"},
        }
    )

    assert human is not None and "HUMAN CONTROL" in human[0]
    assert disarmed is not None and "DISARMED" in disarmed[0]
    assert human[1] != disarmed[1]
