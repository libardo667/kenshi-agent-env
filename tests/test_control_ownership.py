from kenshi_agent.control_ownership import (
    ControlOwnershipEventType,
    ControlOwnershipMachine,
    ControlOwnershipState,
)


def test_human_input_resets_visible_takeover_countdown() -> None:
    machine = ControlOwnershipMachine(quiet_seconds=2.0, countdown_seconds=3.0)

    yielded = machine.yield_to_human(10.0, reason="Human input detected.")
    assert [event.event_type for event in yielded] == [
        ControlOwnershipEventType.CHANGED
    ]
    assert machine.state is ControlOwnershipState.HUMAN_CONTROL

    assert machine.advance(11.9) == ()
    pending = machine.advance(12.0)
    assert [event.event_type for event in pending] == [
        ControlOwnershipEventType.CHANGED,
        ControlOwnershipEventType.COUNTDOWN,
    ]
    assert pending[-1].seconds_remaining == 3

    reset = machine.advance(13.0, human_input=True)
    assert [event.event_type for event in reset] == [
        ControlOwnershipEventType.CANCELLED,
        ControlOwnershipEventType.CHANGED,
    ]
    assert machine.state is ControlOwnershipState.HUMAN_CONTROL
    assert machine.advance(14.9) == ()


def test_takeover_becomes_ready_only_after_full_countdown() -> None:
    machine = ControlOwnershipMachine(quiet_seconds=1.0, countdown_seconds=3.0)
    machine.yield_to_human(20.0, reason="Human input detected.")

    assert machine.advance(21.0)[-1].seconds_remaining == 3
    assert machine.advance(21.2) == ()
    assert machine.advance(22.0)[0].seconds_remaining == 2
    assert machine.advance(23.0)[0].seconds_remaining == 1
    ready = machine.advance(24.0)

    assert [event.event_type for event in ready] == [
        ControlOwnershipEventType.READY,
        ControlOwnershipEventType.CHANGED,
    ]
    assert machine.state is ControlOwnershipState.AGENT_ACTIVE


def test_emergency_stop_permanently_disarms_pending_takeover() -> None:
    machine = ControlOwnershipMachine(quiet_seconds=0.0, countdown_seconds=5.0)
    machine.yield_to_human(30.0, reason="Human input detected.")
    machine.advance(30.0)

    events = machine.advance(31.0, emergency_stop=True)

    assert [event.event_type for event in events] == [
        ControlOwnershipEventType.CANCELLED,
        ControlOwnershipEventType.CHANGED,
    ]
    assert machine.state is ControlOwnershipState.DISARMED
    assert machine.advance(100.0) == ()
