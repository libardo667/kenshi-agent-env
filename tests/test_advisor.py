from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from kenshi_agent.advisor import (
    AdvisorDraft,
    AdvisorSession,
    GuideCorpus,
    OpenRouterStrategyAdvisor,
    advisor_world_payload,
)
from kenshi_agent.config import (
    AdvisorConfig,
    MockConfig,
    PlanningConfig,
    SafetyConfig,
)
from kenshi_agent.env import MockEnvironment
from kenshi_agent.evals import evaluate_log
from kenshi_agent.live_plan_policy import live_plan_policy_errors
from kenshi_agent.models import (
    Action,
    AdvisorFocus,
    AdvisorRecommendation,
    CharacterState,
    Condition,
    ConditionKind,
    ConditionOperator,
    ConsultAdvisorAction,
    ControlMode,
    GameState,
    IdempotencyPolicy,
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
    PlanStep,
    RiskBudget,
    StopAction,
    TelemetrySnapshot,
    Transition,
    WaitAction,
    WorldStateRevision,
)
from kenshi_agent.planners.base import Planner
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.safety import ActionGuard
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry
from kenshi_agent.world_state import WorldStateStore

ROOT = Path(__file__).resolve().parents[1]


class FakeStrategyAdvisor:
    provider = "test"
    model = "guide-reader"

    def __init__(self, source_id: str = "reddit_greenfruit_not_food") -> None:
        self.source_id = source_id
        self.calls = 0

    async def advise(
        self,
        *,
        action: ConsultAdvisorAction,
        observation: Observation,
        corpus: GuideCorpus,
    ) -> AdvisorDraft:
        del action, observation, corpus
        self.calls += 1
        return AdvisorDraft(
            summary="Buy an actually edible item before pursuing a larger goal.",
            recommendations=[
                AdvisorRecommendation(
                    rank=1,
                    goal="Acquire edible food.",
                    why_now="Greenfruit is an ingredient, while current food count is zero.",
                    prerequisites=["Open a trader's inventory."],
                    cautions=["Verify the item is edible before buying."],
                    source_ids=[self.source_id],
                )
            ],
            uncertainties=["The current shop inventory may not contain edible food."],
        )


class AdvancingStrategyAdvisor(FakeStrategyAdvisor):
    """Simulate live telemetry publishing while a hosted advisor is thinking."""

    def __init__(
        self,
        store: WorldStateStore,
        later_observation: Observation,
    ) -> None:
        super().__init__()
        self.store = store
        self.later_observation = later_observation

    async def advise(
        self,
        *,
        action: ConsultAdvisorAction,
        observation: Observation,
        corpus: GuideCorpus,
    ) -> AdvisorDraft:
        self.store.publish(self.later_observation)
        return await super().advise(
            action=action,
            observation=observation,
            corpus=corpus,
        )


class BlockingStrategyAdvisor(FakeStrategyAdvisor):
    """Hold the hosted answer so foreground dispatch can prove independence."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.returned = asyncio.Event()

    async def advise(
        self,
        *,
        action: ConsultAdvisorAction,
        observation: Observation,
        corpus: GuideCorpus,
    ) -> AdvisorDraft:
        self.started.set()
        await self.release.wait()
        draft = await super().advise(
            action=action,
            observation=observation,
            corpus=corpus,
        )
        self.returned.set()
        return draft


class BlockingWaitMockEnvironment(MockEnvironment):
    def __init__(self, run_dir: Path, run_id: str) -> None:
        super().__init__(
            MockConfig(random_events=False),
            run_dir,
            run_id,
        )
        self.world_action_started = asyncio.Event()
        self.release_world_action = asyncio.Event()

    async def step(self, action: Action) -> Transition:
        if isinstance(action, WaitAction):
            self.world_action_started.set()
            await self.release_world_action.wait()
        return await super().step(action)


def observation(step_index: int = 0) -> Observation:
    return Observation(
        run_id="advisor-test",
        step_index=step_index,
        mode="mock",
        control_mode=ControlMode.INTERFACE_ONLY,
        planning_mode=PlanningMode.CONTINUOUS,
        world_revision=WorldStateRevision(
            telemetry_sequence=1,
            frame_sequence=1,
            capability_epoch=1,
            observed_at_monotonic=1.0,
        ),
        telemetry=TelemetrySnapshot(
            sequence=1,
            captured_at=datetime.now(UTC),
            capabilities=["game.money", "game.time"],
            game=GameState(
                loaded=True,
                paused=True,
                money=135,
                elapsed_minutes=10.0,
                location_name="The Hub",
            ),
            squad=[
                CharacterState(
                    id="hep",
                    name="Hep",
                    selected=True,
                    alive=True,
                    conscious=True,
                    hunger=2.5,
                    food_items=0,
                )
            ],
        ),
        telemetry_stale=False,
        telemetry_age_seconds=0.0,
    )


def advisor_config(**updates: object) -> AdvisorConfig:
    values: dict[str, object] = {
        "enabled": True,
        "corpus_file": ROOT / "knowledge" / "kenshi_strategy_v1.yaml",
        "max_calls_per_run": 2,
        "cooldown_steps": 3,
        "cadence_steps": 5,
    }
    values.update(updates)
    return AdvisorConfig.model_validate(values)


def fresh() -> Condition:
    return Condition(
        kind=ConditionKind.TELEMETRY_FRESH,
        operator=ConditionOperator.EQUALS,
        expected=True,
        max_age_seconds=3.0,
    )


def consult_while_playing_plan(current: Observation) -> PlanEnvelope:
    return PlanEnvelope(
        schema_version="1.0",
        plan_id="ask-while-playing",
        plan_version=1,
        objective="Ask for a next goal without stopping the current activity.",
        control_mode=current.control_mode,
        based_on_revision=current.world_revision,
        assumptions=[fresh()],
        steps=[
            PlanStep(
                step_id="consult",
                action=ConsultAdvisorAction(
                    question="What should follow the current income loop?",
                    focus=AdvisorFocus.NEXT_GOAL,
                ),
                preconditions=[fresh()],
                success_conditions=[],
                timeout_seconds=30.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                on_success="continue-playing",
            ),
            PlanStep(
                step_id="continue-playing",
                action=WaitAction(seconds=0.1),
                preconditions=[fresh()],
                success_conditions=[],
                timeout_seconds=2.0,
                idempotency=IdempotencyPolicy.AT_MOST_ONCE,
            ),
        ],
        entry_step_id="consult",
        max_actions=2,
        max_wall_seconds=30.0,
        max_game_seconds=12.0,
        risk_budget=RiskBudget(
            max_pointer_actions=0,
            max_purchase_actions=0,
            max_native_assisted_actions=0,
        ),
    )


def test_advisor_request_can_share_a_plan_with_independent_world_work() -> None:
    current = observation()

    assert live_plan_policy_errors(
        consult_while_playing_plan(current),
        current,
    ) == []


def test_foreground_world_action_runs_while_advisor_is_still_thinking(
    tmp_path: Path,
) -> None:
    class BackgroundAdvisorPlanner(Planner):
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, current: Observation) -> PlannerOutput:
            self.calls += 1
            if self.calls == 1:
                return consult_while_playing_plan(current)
            assert current.advisor.latest_brief is not None
            return PlannerDecision(
                intent="End the background-advisor integration proof.",
                rationale="The completed brief reached a later planner call.",
                action=StopAction(reason="Background advisor integration proved."),
                confidence=1.0,
            )

    async def scenario() -> None:
        corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")
        client = BlockingStrategyAdvisor()
        session = AdvisorSession(advisor_config(cooldown_steps=0), corpus, client)
        planner = BackgroundAdvisorPlanner()
        environment = BlockingWaitMockEnvironment(tmp_path, "advisor-background")
        logger = SessionLogger(tmp_path / "events.jsonl", "advisor-background")
        runtime = AgentRuntime(
            run_id="advisor-background",
            environment=environment,
            planner=planner,
            advisor=session,
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["consult_advisor", "wait", "stop"],
                    max_actions_per_minute=500,
                ),
                MacroRegistry({}),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            planning_config=PlanningConfig(
                mode=PlanningMode.CONTINUOUS,
                observation_pump_enabled=False,
                max_plan_steps=2,
                max_actions_per_plan=2,
            ),
        )
        run = asyncio.create_task(runtime.run(max_steps=3))
        try:
            await asyncio.wait_for(client.started.wait(), timeout=1.0)
            assert session.availability(observation()).request_pending
            await asyncio.wait_for(environment.world_action_started.wait(), timeout=1.0)
            assert not client.returned.is_set()

            client.release.set()
            await asyncio.wait_for(client.returned.wait(), timeout=1.0)
            environment.release_world_action.set()
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            client.release.set()
            environment.release_world_action.set()
            if not run.done():
                await asyncio.wait_for(run, timeout=1.0)
            logger.close()

        assert summary.steps_completed == 3
        assert planner.calls == 2
        assert client.calls == 1
        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert sum(event["event_type"] == "advisor_queued" for event in events) == 1
        assert sum(event["event_type"] == "advisor_result" for event in events) == 1

    asyncio.run(scenario())


def test_run_end_cancels_a_pending_advisor_without_leaving_session_state(
    tmp_path: Path,
) -> None:
    class OnePlanPlanner(Planner):
        async def decide(self, current: Observation) -> PlannerOutput:
            return consult_while_playing_plan(current)

    async def scenario() -> None:
        corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")
        client = BlockingStrategyAdvisor()
        session = AdvisorSession(advisor_config(cooldown_steps=0), corpus, client)
        environment = BlockingWaitMockEnvironment(tmp_path, "advisor-cancel")
        logger = SessionLogger(tmp_path / "events.jsonl", "advisor-cancel")
        runtime = AgentRuntime(
            run_id="advisor-cancel",
            environment=environment,
            planner=OnePlanPlanner(),
            advisor=session,
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["consult_advisor", "wait"],
                    max_actions_per_minute=500,
                ),
                MacroRegistry({}),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            planning_config=PlanningConfig(
                mode=PlanningMode.CONTINUOUS,
                observation_pump_enabled=False,
                max_plan_steps=2,
                max_actions_per_plan=2,
            ),
        )
        run = asyncio.create_task(runtime.run(max_steps=2))
        try:
            await asyncio.wait_for(client.started.wait(), timeout=1.0)
            await asyncio.wait_for(environment.world_action_started.wait(), timeout=1.0)
            environment.release_world_action.set()
            summary = await asyncio.wait_for(run, timeout=1.0)
        finally:
            client.release.set()
            environment.release_world_action.set()
            if not run.done():
                run.cancel()
                with suppress(asyncio.CancelledError):
                    await run
            logger.close()

        assert summary.steps_completed == 2
        assert not client.returned.is_set()
        assert not session.request_pending
        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert sum(event["event_type"] == "advisor_cancelled" for event in events) == 1

    asyncio.run(scenario())


def test_corpus_keeps_claims_and_attribution_together() -> None:
    corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")

    greenfruit = next(fact for fact in corpus.facts if fact.fact_id == "greenfruit_is_ingredient")
    source = corpus.source_map()[greenfruit.source_ids[0]]

    assert corpus.corpus_version == "kenshi-strategy-v1"
    assert "not an edible food" in greenfruit.claim
    assert source.creator == "r/Kenshi community"
    assert source.url.startswith("https://www.reddit.com/r/Kenshi/")


def test_advisor_payload_defines_live_hunger_and_fallible_food_semantics() -> None:
    current = observation()
    payload = advisor_world_payload(current)
    semantics = payload["telemetry_semantics"]

    assert payload["squad_nutrition"] == current.squad_nutrition_digest()
    selected = payload["telemetry"]["selected"]
    assert selected["nutrition_reserve"] == 2.5
    assert "hunger" not in selected
    assert semantics["selected.nutrition_reserve"] == (
        "The current reserve on the squad_nutrition scale. Use that "
        "digest's status and thresholds to decide urgency."
    )
    assert "fallible" in semantics["selected.food_items"]
    assert "not authoritative visual containment" in semantics["selected.indoors"]


def test_session_answers_with_validated_source_attribution_and_suppresses_repeats() -> None:
    async def scenario() -> None:
        corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")
        client = FakeStrategyAdvisor()
        session = AdvisorSession(advisor_config(), corpus, client)
        current = observation(step_index=5)

        assert session.availability(current).suggested
        result = await session.consult(
            ConsultAdvisorAction(
                question="What should I do after buying Greenfruit?",
                focus=AdvisorFocus.FOOD,
            ),
            current,
        )

        assert result.status.value == "answered"
        assert result.brief is not None
        assert result.brief.sources[0].source_id == "reddit_greenfruit_not_food"
        assert result.brief.sources[0].creator == "r/Kenshi community"
        assert client.calls == 1

        cooldown = session.availability(observation(step_index=6))
        assert not cooldown.may_request
        assert cooldown.cooldown_steps_remaining == 2

        unchanged = session.availability(observation(step_index=9))
        assert not unchanged.may_request
        assert "not changed" in unchanged.reason

    asyncio.run(scenario())


def test_unknown_advisor_source_fails_closed() -> None:
    async def scenario() -> None:
        corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")
        client = FakeStrategyAdvisor(source_id="invented_guide")
        session = AdvisorSession(advisor_config(), corpus, client)

        result = await session.consult(
            ConsultAdvisorAction(question="What now?", focus=AdvisorFocus.NEXT_GOAL),
            observation(),
        )

        assert result.status.value == "failed"
        assert result.brief is None
        assert "unknown source ID" in result.reason

    asyncio.run(scenario())


def test_openrouter_advisor_continues_an_exact_truncated_json_suffix() -> None:
    corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")
    draft = AdvisorDraft(
        summary="Rotate from the proven income loop into a bounded exploration goal.",
        recommendations=[
            AdvisorRecommendation(
                rank=1,
                goal="Explore one unfamiliar settlement interior.",
                why_now="The current copper loop is already repeatable.",
                prerequisites=["Retain enough food and travel money."],
                cautions=["Retreat from overwhelming hostiles."],
                source_ids=["reddit_greenfruit_not_food"],
            )
        ],
        uncertainties=["The nearest safe unexplored settlement is not known."],
    )
    encoded = draft.model_dump_json()
    split_at = len(encoded) // 2
    prefix = encoded[:split_at]
    suffix = encoded[split_at:]
    reasoning_details = [
        {
            "type": "reasoning.encrypted",
            "data": "opaque-advisor-thought",
            "format": "openrouter-v1",
        }
    ]

    class Continues:
        def __init__(self, first_finish_reason: str) -> None:
            self.first_finish_reason = first_finish_reason
            self.calls: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason=self.first_finish_reason,
                            message=SimpleNamespace(
                                content=prefix,
                                reasoning_details=reasoning_details,
                            ),
                        )
                    ]
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=suffix,
                            reasoning_details=[],
                        ),
                    )
                ]
            )

    for first_finish_reason in ("length", "stop"):
        completions = Continues(first_finish_reason)
        advisor = object.__new__(OpenRouterStrategyAdvisor)
        advisor.config = advisor_config()
        advisor.model = advisor.config.model
        advisor.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        result = asyncio.run(
            advisor.advise(
                action=ConsultAdvisorAction(
                    question="What should replace the proven copper loop?",
                    focus=AdvisorFocus.NEXT_GOAL,
                ),
                observation=observation(),
                corpus=corpus,
            )
        )

        assert result == draft
        assert len(completions.calls) == 2
        continuation = completions.calls[1]
        assert "response_format" not in continuation
        assistant = continuation["messages"][-2]
        assert assistant["content"] == prefix
        assert assistant["reasoning_details"] == reasoning_details
        assert "exact next character" in continuation["messages"][-1]["content"]


def test_openrouter_advisor_does_not_continue_a_complete_invalid_answer() -> None:
    class RejectSecondCall:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **kwargs: Any) -> SimpleNamespace:
            del kwargs
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("schema-invalid output is not a truncation")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content='{"summary":3}',
                            reasoning_details=[],
                        ),
                    )
                ]
            )

    completions = RejectSecondCall()
    advisor = object.__new__(OpenRouterStrategyAdvisor)
    advisor.config = advisor_config()
    advisor.model = advisor.config.model
    advisor.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )

    with pytest.raises(ValidationError):
        asyncio.run(
            advisor.advise(
                action=ConsultAdvisorAction(
                    question="What should happen next?",
                    focus=AdvisorFocus.NEXT_GOAL,
                ),
                observation=observation(),
                corpus=GuideCorpus.load(
                    ROOT / "knowledge" / "kenshi_strategy_v1.yaml"
                ),
            )
        )

    assert completions.calls == 1


class ConsultThenStopPlanner(Planner):
    def __init__(self) -> None:
        self.calls = 0
        self.observations: list[Observation] = []

    async def decide(self, current: Observation) -> PlannerOutput:
        self.calls += 1
        self.observations.append(current)
        if self.calls == 1:
            return consult_while_playing_plan(current)
        assert current.advisor.latest_brief is not None
        return PlannerDecision(
            intent="End the bounded advisor integration proof.",
            rationale="The next planner observation contains the attributed brief.",
            action=StopAction(reason="Advisor integration proved."),
            confidence=1.0,
        )


def test_continuous_runtime_never_dispatches_consult_to_the_environment(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")
        client = FakeStrategyAdvisor()
        session = AdvisorSession(advisor_config(cooldown_steps=0), corpus, client)
        planner = ConsultThenStopPlanner()
        environment = MockEnvironment(
            MockConfig(random_events=False),
            tmp_path,
            "advisor-runtime",
        )
        logger = SessionLogger(tmp_path / "events.jsonl", "advisor-runtime")
        runtime = AgentRuntime(
            run_id="advisor-runtime",
            environment=environment,
            planner=planner,
            advisor=session,
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["consult_advisor", "wait", "stop"],
                    max_actions_per_minute=500,
                ),
                MacroRegistry({}),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            planning_config=PlanningConfig(
                mode=PlanningMode.CONTINUOUS,
                observation_pump_enabled=False,
                max_plan_steps=2,
                max_actions_per_plan=2,
            ),
        )
        try:
            summary = await runtime.run(max_steps=3)
        finally:
            logger.close()

        assert summary.steps_completed == 3
        assert planner.calls == 2
        assert planner.observations[1].advisor.latest_brief is not None
        assert client.calls == 1

        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        advisor_receipt = next(
            event
            for event in events
            if event["event_type"] == "action_receipt"
            and event["payload"]["action"]["kind"] == "consult_advisor"
        )
        assert advisor_receipt["payload"]["primitive_actions"] == 0
        assert advisor_receipt["payload"]["command_id"] is None
        assert next(
            event for event in events if event["event_type"] == "advisor_result"
        )["payload"]["world_command_created"] is False
        metrics = evaluate_log(tmp_path / "events.jsonl")
        assert metrics.advisor_requests == 1
        assert metrics.advisor_hosted_calls == 1
        assert metrics.advisor_answers == 1
        assert metrics.advisor_suppressions == 0
        assert metrics.advisor_failures == 0

    asyncio.run(scenario())


def test_advisor_handoff_rebases_context_after_telemetry_advances(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        current = observation(step_index=20)
        later = observation(step_index=20).model_copy(
            update={
                "world_revision": WorldStateRevision(
                    telemetry_sequence=2,
                    frame_sequence=20,
                    capability_epoch=1,
                    observed_at_monotonic=2.0,
                ),
                "telemetry": current.telemetry.model_copy(update={"sequence": 2})
                if current.telemetry is not None
                else None,
            },
            deep=True,
        )
        store = WorldStateStore()
        store.publish(current)
        corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")
        session = AdvisorSession(
            advisor_config(cooldown_steps=0),
            corpus,
            AdvancingStrategyAdvisor(store, later),
        )
        logger = SessionLogger(tmp_path / "events.jsonl", "advisor-rebase")
        runtime = AgentRuntime(
            run_id="advisor-rebase",
            environment=MockEnvironment(
                MockConfig(random_events=False),
                tmp_path,
                "advisor-rebase",
            ),
            planner=ConsultThenStopPlanner(),
            advisor=session,
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["consult_advisor", "stop"],
                    max_actions_per_minute=500,
                ),
                MacroRegistry({}),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
            planning_config=PlanningConfig(mode=PlanningMode.CONTINUOUS),
        )
        runtime._state_store = store
        try:
            result = await runtime._execute_advisor_action(
                ConsultAdvisorAction(
                    question="What is the safest useful next goal?",
                    focus=AdvisorFocus.NEXT_GOAL,
                ),
                current,
                plan_id="advisor-race",
                plan_version=1,
                step_id="consult",
            )
            task = runtime._advisor_task
            assert task is not None
            await task
        finally:
            logger.close()

        assert result.receipt.advisor is not None
        assert result.receipt.advisor.status.value == "pending"
        assert result.observation.advisor.request_pending
        latest = store.latest
        assert latest is not None
        assert latest.world_revision == later.world_revision
        assert latest.advisor.latest_brief is not None
        assert (
            latest.advisor.latest_brief.based_on_revision
            == current.world_revision
        )

    asyncio.run(scenario())


def test_runtime_never_shortens_the_configured_advisor_timeout(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    async def scenario() -> None:
        corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")
        session = AdvisorSession(
            advisor_config(
                cooldown_steps=0,
                timeout_seconds=90.0,
            ),
            corpus,
            FakeStrategyAdvisor(),
        )

        class RejectShortTimeout:
            def __init__(self, delay: float | None) -> None:
                self.delay = delay

            async def __aenter__(self) -> None:
                if self.delay is not None and self.delay < session.config.timeout_seconds:
                    raise TimeoutError

            async def __aexit__(self, *args: object) -> None:
                return None

        monkeypatch.setattr(
            "kenshi_agent.runtime.asyncio.timeout",
            lambda delay: RejectShortTimeout(delay),
        )

        logger = SessionLogger(tmp_path / "events.jsonl", "advisor-timeout-owner")
        runtime = AgentRuntime(
            run_id="advisor-timeout-owner",
            environment=MockEnvironment(
                MockConfig(random_events=False),
                tmp_path,
                "advisor-timeout-owner",
            ),
            planner=ConsultThenStopPlanner(),
            advisor=session,
            guard=ActionGuard(
                SafetyConfig(
                    supervisor_enabled=False,
                    allow_action_kinds=["consult_advisor", "stop"],
                    max_actions_per_minute=500,
                ),
                MacroRegistry({}),
            ),
            reflexes=ReflexEngine(),
            logger=logger,
            memory=None,
            memory_limit=0,
            minimum_memory_salience=0.0,
        )
        try:
            result = await runtime._execute_advisor_action(
                ConsultAdvisorAction(
                    question="What is the safest useful next goal?",
                    focus=AdvisorFocus.NEXT_GOAL,
                ),
                observation(),
                plan_id="advisor-timeout-owner",
                plan_version=1,
                step_id="consult",
            )
            task = runtime._advisor_task
            assert task is not None
            await task
        finally:
            logger.close()

        assert result.receipt.advisor is not None
        assert result.receipt.advisor.status.value == "pending"
        assert session.latest_brief is not None

    asyncio.run(scenario())
