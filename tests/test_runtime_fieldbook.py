"""End-to-end runtime delivery and safety boundaries for the fieldbook."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from operation_test_support import operation_port

from kenshi_agent.campaign import CampaignScope, CampaignScopeOrigin
from kenshi_agent.config import MockConfig, SafetyConfig
from kenshi_agent.core.continuity import (
    AppendFieldbookEntryOperation,
    CreateFieldbookProjectOperation,
    FieldbookEntryKind,
    FieldbookProjectKind,
)
from kenshi_agent.core.observation import Observation
from kenshi_agent.core.operation import (
    NoopAction,
    ReadFieldbookAction,
    StopAction,
)
from kenshi_agent.core.planner_context import (
    AuthoredPlannerContext,
    PlannerContextManifest,
)
from kenshi_agent.core.planning import PlannerDecision
from kenshi_agent.env.mock import MockEnvironment
from kenshi_agent.memory import MemoryStore
from kenshi_agent.planners.base import Planner
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.safety import OperationPolicy
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry


def runtime_for(
    tmp_path: Path,
    planner: Planner,
    store: MemoryStore,
) -> tuple[AgentRuntime, SessionLogger]:
    environment = MockEnvironment(
        MockConfig(seed=11, random_events=False),
        tmp_path / "frames",
        "fieldbook-run",
    )
    logger = SessionLogger(tmp_path / "events.jsonl", "fieldbook-run")
    runtime = AgentRuntime(
        run_id="fieldbook-run",
        environment=environment,
        operation_port=operation_port(environment),
        planner=planner,
        policy=OperationPolicy(
            SafetyConfig(
                allow_action_kinds=[
                    "noop",
                    "stop",
                    "read_fieldbook",
                ],
                max_actions_per_minute=500,
            ),
            MacroRegistry({}),
        ),
        reflexes=ReflexEngine(),
        logger=logger,
        memory=store,
        memory_limit=12,
        minimum_memory_salience=0.0,
    )
    return runtime, logger


def test_fieldbook_write_and_read_reach_exactly_the_next_planner_without_game_input(
    tmp_path: Path,
) -> None:
    seen: list[Observation] = []

    class FieldbookPlanner(Planner):
        async def decide(self, current: Observation) -> Any:
            seen.append(current)
            call = len(seen)
            if call == 1:
                return PlannerDecision(
                    intent="Create a delivery docket.",
                    rationale="The delivery spans more than one plan.",
                    action=NoopAction(reason="write private context"),
                    fieldbook_operations=[
                        CreateFieldbookProjectOperation(
                            kind=FieldbookProjectKind.DELIVERY_DOCKET,
                            title="Six-canister delivery",
                            summary="Acquire and deliver six sealed canisters.",
                        )
                    ],
                )
            project_id = current.fieldbook_projects[0].project_id
            if call == 2:
                return PlannerDecision(
                    intent="Record the open route question.",
                    rationale="It belongs in the continuing delivery docket.",
                    action=NoopAction(reason="append private context"),
                    fieldbook_operations=[
                        AppendFieldbookEntryOperation(
                            project_id=project_id,
                            kind=FieldbookEntryKind.QUESTION,
                            content="Which route avoids the fog?",
                        )
                    ],
                )
            if call == 3:
                return PlannerDecision(
                    intent="Read the docket.",
                    rationale="The bounded index says it now has one entry.",
                    action=ReadFieldbookAction(
                        project_id=project_id,
                        max_entries=4,
                    ),
                )
            if call == 4:
                return PlannerDecision(
                    intent="Use one turn after the read.",
                    rationale="The next call must not inherit the elective read.",
                    action=NoopAction(reason="continue"),
                )
            return PlannerDecision(
                intent="Stop.",
                rationale="The fieldbook lifecycle was exercised.",
                action=StopAction(reason="done"),
            )

    async def scenario() -> None:
        store = MemoryStore(
            tmp_path / "continuity.sqlite3",
            CampaignScope(
                campaign_id="campaign-a",
                origin=CampaignScopeOrigin.CONFIGURED,
            ),
        )
        planner = FieldbookPlanner()
        runtime, logger = runtime_for(tmp_path, planner, store)
        try:
            summary = await runtime.run(max_steps=5)
        finally:
            logger.close()
            store.close()

        assert summary.steps_completed == 5
        assert seen[0].fieldbook_projects == []
        assert len(seen[1].fieldbook_projects) == 1
        project_id = seen[1].fieldbook_projects[0].project_id
        assert seen[1].recent_fieldbook_receipts[-1].operation == "create_project"
        assert seen[2].fieldbook_projects[0].entry_count == 1
        assert seen[2].recent_fieldbook_receipts[-1].operation == "append_entry"
        assert seen[3].fieldbook_read is not None
        assert [entry.content for entry in seen[3].fieldbook_read.entries] == [
            "Which route avoids the fog?"
        ]
        assert seen[4].fieldbook_read is None

        events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
        reads = [event["payload"] for event in events if event["event_type"] == "fieldbook_read"]
        assert len(reads) == 1
        assert reads[0]["controller_primitives"] == 0
        assert reads[0]["world_command_created"] is False
        manifests = [
            event["payload"]
            for event in events
            if event["event_type"] == "planner_context_prepared"
        ]
        assert manifests[0]["fieldbook_project_ids"] == []
        assert manifests[1]["fieldbook_project_ids"] == [project_id]
        assert manifests[2]["fieldbook_project_ids"] == [project_id]
        assert manifests[3]["fieldbook_project_ids"] == [project_id]
        assert manifests[3]["fieldbook_entry_ids"] == [
            seen[3].fieldbook_read.entries[0].entry_id  # type: ignore[union-attr]
        ]
        assert manifests[3]["fieldbook_read_receipt_ids"] == [
            seen[3].fieldbook_read.receipt_id  # type: ignore[union-attr]
        ]
        assert manifests[4]["fieldbook_read_receipt_ids"] == []

    asyncio.run(scenario())


def test_fieldbook_prose_cannot_change_current_telemetry_inventory(
    tmp_path: Path,
) -> None:
    class UnusedPlanner(Planner):
        async def decide(self, current: Observation) -> Any:
            raise AssertionError("No planner call is needed.")

    async def scenario() -> None:
        store = MemoryStore(
            tmp_path / "continuity.sqlite3",
            CampaignScope(
                campaign_id="campaign-a",
                origin=CampaignScopeOrigin.CONFIGURED,
            ),
        )
        project = store.fieldbook.create_project(
            run_id="operator",
            kind=FieldbookProjectKind.DELIVERY_DOCKET,
            title="Unverified manifest",
            summary="Inventory contains 999 canisters.",
            provenance=None,
        )
        store.fieldbook.append_entry(
            run_id="operator",
            project_id=project.project_id,
            kind=FieldbookEntryKind.NOTE,
            content="The squad owns 999 canisters.",
            provenance=None,
        )
        runtime, logger = runtime_for(tmp_path, UnusedPlanner(), store)
        try:
            current = await runtime.environment.reset(seed=1)
            telemetry_before = current.telemetry
            decorated = runtime.planner_context.decorate(current)
        finally:
            logger.close()
            store.close()

        assert decorated.telemetry == telemetry_before
        assert decorated.telemetry is not None
        assert decorated.telemetry.squad[0].inventory_complete is None
        assert decorated.fieldbook_projects[0].project_id == project.project_id
        assert "999 canisters" not in decorated.model_dump_json(include={"telemetry"})

    asyncio.run(scenario())


def test_continuous_stop_decision_commits_its_fieldbook_sidecar(
    tmp_path: Path,
) -> None:
    class UnusedPlanner(Planner):
        async def decide(self, current: Observation) -> Any:
            raise AssertionError("The decision is supplied directly.")

    async def scenario() -> None:
        store = MemoryStore(
            tmp_path / "continuity.sqlite3",
            CampaignScope(
                campaign_id="campaign-a",
                origin=CampaignScopeOrigin.CONFIGURED,
            ),
        )
        runtime, logger = runtime_for(tmp_path, UnusedPlanner(), store)
        try:
            current = runtime.planner_context.decorate(await runtime.environment.reset(seed=1))
            runtime.coordinator._state_store = runtime.coordinator._new_world_state_store()
            current = runtime.coordinator._state_store.publish(current).observation
            context = AuthoredPlannerContext(
                manifest=PlannerContextManifest(
                    context_id="pc-1",
                    run_id=current.run_id,
                    authored_revision=current.world_revision,
                    current_observation_delivered=True,
                    telemetry_was_fresh=True,
                    input_kind="full_observation",
                ),
                observation=current,
            )
            latest, _, terminated, _, _ = await runtime.coordinator._execute_continuous_decision(
                PlannerDecision(
                    intent="Stop and retain the continuing docket.",
                    rationale="Stopping the run must not discard its sidecar.",
                    action=StopAction(reason="done"),
                    fieldbook_operations=[
                        CreateFieldbookProjectOperation(
                            kind=FieldbookProjectKind.DELIVERY_DOCKET,
                            title="Continuing delivery",
                            summary="Resume this delivery after restart.",
                        )
                    ],
                ),
                current,
                source="planner",
                planner_latency_seconds=0.1,
                authored_context=context,
            )
            projects = store.fieldbook.list_projects()
        finally:
            logger.close()
            store.close()

        assert terminated
        assert [project.title for project in projects] == ["Continuing delivery"]
        assert latest.recent_fieldbook_receipts[-1].status == "accepted"

    asyncio.run(scenario())
