from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from kenshi_agent.advisor import AdvisorDraft, AdvisorSession, GuideCorpus
from kenshi_agent.config import (
    AdvisorConfig,
    MockConfig,
    PlanningConfig,
    SafetyConfig,
)
from kenshi_agent.env import MockEnvironment
from kenshi_agent.evals import evaluate_log
from kenshi_agent.models import (
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
    WorldStateRevision,
)
from kenshi_agent.planners.base import Planner
from kenshi_agent.reflexes import ReflexEngine
from kenshi_agent.runtime import AgentRuntime
from kenshi_agent.safety import ActionGuard
from kenshi_agent.session_log import SessionLogger
from kenshi_agent.skills import MacroRegistry

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


def test_corpus_keeps_claims_and_attribution_together() -> None:
    corpus = GuideCorpus.load(ROOT / "knowledge" / "kenshi_strategy_v1.yaml")

    greenfruit = next(fact for fact in corpus.facts if fact.fact_id == "greenfruit_is_ingredient")
    source = corpus.source_map()[greenfruit.source_ids[0]]

    assert corpus.corpus_version == "kenshi-strategy-v1"
    assert "not an edible food" in greenfruit.claim
    assert source.creator == "r/Kenshi community"
    assert source.url.startswith("https://www.reddit.com/r/Kenshi/")


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


class ConsultThenStopPlanner(Planner):
    def __init__(self) -> None:
        self.calls = 0
        self.observations: list[Observation] = []

    async def decide(self, current: Observation) -> PlannerOutput:
        self.calls += 1
        self.observations.append(current)
        if self.calls == 1:
            return PlanEnvelope(
                schema_version="1.0",
                plan_id="ask-guide",
                plan_version=1,
                objective="Get a source-grounded next goal.",
                control_mode=current.control_mode,
                based_on_revision=current.world_revision,
                assumptions=[fresh()],
                steps=[
                    PlanStep(
                        step_id="consult",
                        action=ConsultAdvisorAction(
                            question="What is the most useful safe next goal?",
                            focus=AdvisorFocus.NEXT_GOAL,
                        ),
                        preconditions=[fresh()],
                        success_conditions=[],
                        timeout_seconds=30.0,
                        idempotency=IdempotencyPolicy.AT_MOST_ONCE,
                    )
                ],
                entry_step_id="consult",
                max_actions=1,
                max_wall_seconds=30.0,
                max_game_seconds=12.0,
                risk_budget=RiskBudget(
                    max_pointer_actions=0,
                    max_purchase_actions=0,
                    max_native_assisted_actions=0,
                ),
            )
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
            planning_config=PlanningConfig(
                mode=PlanningMode.CONTINUOUS,
                observation_pump_enabled=False,
                max_plan_steps=1,
                max_actions_per_plan=1,
            ),
        )
        try:
            summary = await runtime.run(max_steps=2)
        finally:
            logger.close()

        assert summary.steps_completed == 2
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
