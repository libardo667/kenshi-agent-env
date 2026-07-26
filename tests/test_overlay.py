from __future__ import annotations

from kenshi_agent.overlay import (
    OverlayFeedState,
    WindowRect,
    companion_layout,
    format_event,
    ownership_banner,
)


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


def test_the_overlay_narrates_continuous_mode() -> None:
    """The operator must be able to see what a continuous run is doing.

    The overlay only rendered single-step `decision` events, so a continuous run
    that proposed and rejected plans repeatedly showed a blank window: the game
    visibly moved while the operator had no idea why.
    """

    proposed = format_event(
        {
            "event_type": "plan_proposed",
            "step_index": 0,
            "payload": {
                "plan_id": "goal-1",
                "evidence": {
                    "plan": {
                        "objective": "Buy food from the Barman",
                        "steps": [
                            {
                                "action": {
                                    "kind": "approach_dialogue_target",
                                    "target_id": "entity-" + "a" * 40,
                                }
                            },
                            {
                                "action": {
                                    "kind": "activate_visible_control",
                                    "exact_label": "Show me your goods.",
                                }
                            },
                        ],
                    }
                },
            },
        }
    )
    assert proposed is not None
    assert "Buy food from the Barman" in proposed
    assert "approach_dialogue_target" in proposed
    assert "activate_visible_control" in proposed
    # Long opaque ids are abbreviated so the line stays readable.
    assert "a" * 40 not in proposed

    rejected = format_event(
        {
            "event_type": "plan_rejected",
            "step_index": 1,
            "payload": {"reason": "the tooltip does not name that item"},
        }
    )
    assert rejected is not None and "the tooltip does not name that item" in rejected

    for event_type, payload, expected in (
        ("plan_step_started", {"step_id": "approach"}, "approach"),
        ("plan_completed", {"plan_id": "goal-1"}, "goal-1"),
        ("planner_error", {"message": "bad schema"}, "bad schema"),
        (
            "safety_supervisor_preempted",
            {"cause": "human_input", "reason": "human took over"},
            "human took over",
        ),
        (
            "strategic_planner_call",
            {"planner_latency_seconds": 18.4, "output_type": "PlanEnvelope"},
            "18.4",
        ),
    ):
        rendered = format_event(
            {"event_type": event_type, "step_index": 2, "payload": payload}
        )
        assert rendered is not None, event_type
        assert expected in rendered, event_type


def test_events_are_colour_coded_by_what_the_operator_must_notice() -> None:
    from kenshi_agent.overlay import EVENT_COLOURS, event_category

    assert event_category({"event_type": "plan_proposed"}) == "goal"
    assert event_category({"event_type": "plan_completed"}) == "progress"
    assert event_category({"event_type": "plan_rejected"}) == "refused"
    assert event_category({"event_type": "planner_error"}) == "error"
    assert event_category({"event_type": "safety_supervisor_preempted"}) == "safety"
    assert event_category({"event_type": "control_ownership_changed"}) == "control"
    assert event_category({"event_type": "strategic_planner_call"}) == "thinking"

    # A refusal is distinct from a failure: the agent will simply try again.
    assert EVENT_COLOURS["refused"] != EVENT_COLOURS["error"]

    # A receipt is progress unless the action actually failed.
    assert (
        event_category({"event_type": "action_receipt", "payload": {"accepted": True}})
        == "progress"
    )
    assert (
        event_category(
            {"event_type": "action_receipt", "payload": {"accepted": False, "error_type": "X"}}
        )
        == "error"
    )

    # Every category names a real colour.
    for record in ({"event_type": "plan_proposed"}, {"event_type": "unknown_event"}):
        assert event_category(record) in EVENT_COLOURS


def test_overlay_coalesces_progress_for_one_option_without_hiding_decisions() -> None:
    feed = OverlayFeedState()

    progress = {
        "event_type": "option_progress",
        "payload": {
            "reason": "Approaching: 0.0 units closer so far.",
            "evidence": {"option_id": "approach-metaru"},
        },
    }
    assert feed.operation(progress, "step 12 | ... 0.0 closer") == "append"
    assert feed.operation(progress, "step 12 | ... 0.0 closer") == "skip"

    changed = {
        "event_type": "option_progress",
        "payload": {
            "reason": "Approaching: 8.0 units closer so far.",
            "evidence": {"option_id": "approach-metaru"},
        },
    }
    assert feed.operation(changed, "step 12 | ... 8.0 closer") == "replace"

    decision = {"event_type": "plan_proposed", "payload": {}}
    assert feed.operation(decision, "step 13 | PLAN choose-food") == "append"
    # Progress after a real feed event gets a new row rather than overwriting it.
    assert feed.operation(changed, "step 13 | ... 8.0 closer") == "append"


def test_companion_uses_free_space_beside_terminal_without_resizing_it() -> None:
    layout = companion_layout(
        WindowRect(0, 0, 1200, 900),
        WindowRect(0, 0, 1920, 1040),
    )
    assert layout.resized_anchor is None
    assert layout.viewer.left > 1200
    assert layout.viewer.right <= 1920


def test_companion_splits_maximized_terminal_into_a_narrow_side_column() -> None:
    layout = companion_layout(
        WindowRect(0, 0, 1920, 1040),
        WindowRect(0, 0, 1920, 1040),
    )
    assert layout.resized_anchor is not None
    assert layout.resized_anchor.right < layout.viewer.left
    assert layout.viewer.right == 1920
    assert layout.viewer.width == 380
