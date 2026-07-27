import json
from pathlib import Path

from kenshi_agent.evals import evaluate_log


def test_evaluate_log_counts_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    records = [
        {
            "event_type": "run_started",
            "payload": {"control_mode": "native_assisted"},
        },
        {
            "event_type": "decision",
            "payload": {"source": "reflex", "planner_latency_seconds": 0.001},
        },
        {
            "event_type": "decision",
            "payload": {"source": "planner", "planner_latency_seconds": 1.0},
        },
        {
            "event_type": "decision",
            "payload": {"source": "planner", "planner_latency_seconds": 3.0},
        },
        {
            "event_type": "action_receipt",
            "payload": {
                "primitive_actions": 2,
                "dry_run": False,
                "executed": True,
                "native_acknowledgement": {
                    "command_id": "cmd-0123456789abcdef0123456789abcdef",
                    "status": "accepted",
                    "based_on_telemetry_sequence": 10,
                    "acknowledged_at_telemetry_sequence": 11,
                },
            },
        },
        {
            "event_type": "observation",
            "payload": {
                "telemetry_stale": True,
                "telemetry": {
                    "native_control": {
                        "acknowledgements": [
                            {
                                "command_id": ("cmd-0123456789abcdef0123456789abcdef"),
                                "status": "completed",
                                "based_on_telemetry_sequence": 10,
                                "acknowledged_at_telemetry_sequence": 11,
                            },
                            {
                                "command_id": ("cmd-ffffffffffffffffffffffffffffffff"),
                                "status": "rejected",
                                "based_on_telemetry_sequence": 12,
                                "acknowledged_at_telemetry_sequence": 14,
                            },
                        ]
                    }
                },
            },
        },
        {
            "event_type": "safety_supervisor_preempted",
            "payload": {"cause": "reflex"},
        },
        {
            "event_type": "strategic_planner_cancelled",
            "payload": {"cause": "reflex"},
        },
        {
            "event_type": "safety_cleanup_started",
            "payload": {"cause": "reflex"},
        },
        {
            "event_type": "safety_cleanup_completed",
            "payload": {"cause": "reflex"},
        },
        {
            "event_type": "safety_supervisor_terminal",
            "payload": {"cause": "reflex", "status": "safe_paused"},
        },
        {
            "event_type": "plan_patch_staged",
            "payload": {"plan_id": "plan"},
        },
        {
            "event_type": "plan_patched",
            "payload": {"plan_id": "plan"},
        },
        {
            "event_type": "option_prepared",
            "payload": {"option_id": "option-1"},
        },
        {
            "event_type": "option_started",
            "payload": {"option_id": "option-1"},
        },
        {
            "event_type": "option_progress",
            "payload": {"option_id": "option-1"},
        },
        {
            "event_type": "option_succeeded",
            "payload": {"option_id": "option-1"},
        },
        {
            "event_type": "run_finished",
            "payload": {"success": True, "steps_completed": 1, "stop_reason": "done"},
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    metrics = evaluate_log(path)
    assert metrics.decisions == 3
    assert metrics.reflex_decisions == 1
    assert metrics.primitive_actions == 2
    assert metrics.stale_observations == 1
    assert metrics.success is True
    assert metrics.control_mode == "native_assisted"
    assert metrics.mean_planner_latency_seconds == 2.0
    assert metrics.p50_planner_latency_seconds == 2.0
    assert metrics.p95_planner_latency_seconds == 3.0
    assert metrics.safety_supervisor_preemptions == 1
    assert metrics.strategic_planner_cancellations == 1
    assert metrics.safety_cleanups_started == 1
    assert metrics.safety_cleanups_completed == 1
    assert metrics.safety_cleanups_failed == 0
    assert metrics.safety_supervisor_terminals == 1
    assert metrics.safety_supervisor_safe_paused == 1
    assert metrics.safety_cleanup_success_percentage == 100.0
    assert metrics.plan_patches_staged == 1
    assert metrics.plan_patches_applied == 1
    assert metrics.options_prepared == 1
    assert metrics.options_started == 1
    assert metrics.option_progress_updates == 1
    assert metrics.options_succeeded == 1
    assert metrics.options_failed == 0
    assert metrics.options_cancelled == 0
    assert metrics.option_success_percentage == 100.0
    assert metrics.native_command_acknowledgements == 2
    assert metrics.native_commands_accepted == 1
    assert metrics.native_commands_completed == 1
    assert metrics.native_commands_rejected == 1
    assert metrics.native_commands_cancelled == 0
    assert metrics.mean_native_ack_sequence_lag == 1.5
    assert metrics.native_command_completion_percentage == 100.0


def test_evaluate_log_measures_repeated_dialogue_approaches_by_target(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    records = [
        {
            "event_type": "action_receipt",
            "payload": {
                "action": {
                    "kind": "approach_dialogue_target",
                    "target_id": target_id,
                },
                "executed": executed,
            },
        }
        for target_id, executed in (
            ("entity-barman", True),
            ("entity-guard", True),
            ("entity-barman", True),
            ("entity-barman", False),
        )
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    metrics = evaluate_log(path)

    assert metrics.dialogue_approach_attempts_by_target == {
        "entity-barman": 3,
        "entity-guard": 1,
    }
    assert metrics.dialogue_approach_attempts == 4
    assert metrics.repeated_dialogue_approach_attempts == 2
    assert metrics.max_dialogue_approach_attempts_per_target == 3


def test_the_observation_digest_keeps_what_the_evaluator_reads() -> None:
    """A digest must not silently break replay metrics.

    Logging every full observation produced a 112 MB file in ten minutes, but
    the evaluator reads only a few fields from them and a human reading the log
    wants orientation, not two hundred control bounds.
    """

    from kenshi_agent.models import (
        GameState,
        NativeCommandAcknowledgement,
        NativeCommandStatus,
        NativeControlState,
        Observation,
        TelemetrySnapshot,
        UIState,
        WorldStateRevision,
    )

    command_id = "cmd-" + "a" * 32
    observation = Observation(
        run_id="digest-test",
        step_index=4,
        mode="live",
        world_revision=WorldStateRevision(telemetry_sequence=12, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=12,
            identity_session_id="session-digest",
            capabilities=["game.pause", "identity.stable_handles"],
            game=GameState(loaded=True, paused=True, money=1000),
            ui=UIState(active_screen="trade"),
            native_control=NativeControlState(
                available=True,
                acknowledgements=[
                    NativeCommandAcknowledgement(
                        command_id=command_id,
                        command="approach_confirmed_vendor",
                        status=NativeCommandStatus.ACCEPTED,
                        reason="issued",
                        target_id="entity-barman",
                        selected_character_ids=["entity-hep"],
                        based_on_telemetry_sequence=10,
                        acknowledged_at_telemetry_sequence=11,
                        accepted_at_telemetry_sequence=11,
                    )
                ],
            ),
        ),
        telemetry_stale=True,
    )

    digest = observation.log_digest()

    # Exactly the fields metrics.py reads off an observation.
    assert digest["telemetry_stale"] is True
    acknowledgements = digest["telemetry"]["native_control"]["acknowledgements"]
    assert [item["command_id"] for item in acknowledgements] == [command_id]

    # Enough to orient a human reading the log.
    assert digest["telemetry"]["ui"]["active_screen"] == "trade"
    assert digest["telemetry"]["game"]["money"] == 1000
    assert digest["world_revision"]["telemetry_sequence"] == 12

    # And it is marked, so replay can refuse it instead of failing obscurely.
    assert digest["digest"] is True

    import json

    full_size = len(json.dumps(observation.model_dump(mode="json"), default=str))
    assert len(json.dumps(digest, default=str)) < full_size
