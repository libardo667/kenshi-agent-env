"""Read-only, guide-grounded strategic advice for the playing planner.

The advisor is deliberately outside the environment boundary. It can interpret
the current observation and an attributed guide corpus, but it cannot author a
plan, dispatch an action, or emit a controller primitive.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import yaml
from pydantic import Field, ValidationError, model_validator

from .config import AdvisorConfig
from .hosted_continuation import (
    CONTINUE_STRUCTURED_JSON_SUFFIX,
    TRUNCATED_FINISH_REASONS,
    assistant_continuation,
    message_field,
    structured_json_was_truncated,
)
from .models import (
    ActionOutcome,
    AdvisorAttribution,
    AdvisorAvailability,
    AdvisorBrief,
    AdvisorConsultEvidence,
    AdvisorConsultStatus,
    AdvisorFocus,
    AdvisorRecommendation,
    ConsultAdvisorAction,
    Observation,
    StrictModel,
)
from .nutrition import model_facing_telemetry_payload
from .planners.schema_dialect import portable_response_format


class GuideSource(StrictModel):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    title: str = Field(min_length=1, max_length=300)
    creator: str | None = Field(default=None, max_length=200)
    url: str = Field(min_length=1, max_length=1000)
    source_type: str = Field(min_length=1, max_length=80)
    accessed_on: date

    def attribution(self) -> AdvisorAttribution:
        return AdvisorAttribution(
            source_id=self.source_id,
            title=self.title,
            creator=self.creator,
            url=self.url,
        )


class GuideFact(StrictModel):
    fact_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    claim: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(min_length=1, max_length=8)
    tags: list[str] = Field(min_length=1, max_length=12)
    confidence: str = Field(pattern=r"^(high|medium|contested)$")
    note: str | None = Field(default=None, max_length=800)


class GuideCorpus(StrictModel):
    corpus_version: str = Field(min_length=1, max_length=80)
    updated_on: date
    sources: list[GuideSource] = Field(min_length=1, max_length=100)
    facts: list[GuideFact] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def references_are_unique_and_resolved(self) -> GuideCorpus:
        source_ids = [source.source_id for source in self.sources]
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("guide source IDs must be unique")
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("guide fact IDs must be unique")
        known = set(source_ids)
        for fact in self.facts:
            unknown = set(fact.source_ids) - known
            if unknown:
                raise ValueError(
                    f"guide fact {fact.fact_id!r} cites unknown sources: {sorted(unknown)}"
                )
        return self

    @classmethod
    def load(cls, path: Path) -> GuideCorpus:
        with path.open("r", encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle) or {})

    def source_map(self) -> dict[str, GuideSource]:
        return {source.source_id: source for source in self.sources}

    def prompt_payload(self, focus: AdvisorFocus) -> dict[str, Any]:
        focused = [
            fact
            for fact in self.facts
            if focus.value in fact.tags or "general" in fact.tags
        ]
        facts = focused or self.facts
        cited = {source_id for fact in facts for source_id in fact.source_ids}
        return {
            "corpus_version": self.corpus_version,
            "sources": [
                source.model_dump(mode="json")
                for source in self.sources
                if source.source_id in cited
            ],
            "facts": [fact.model_dump(mode="json") for fact in facts],
        }


class AdvisorDraft(StrictModel):
    summary: str = Field(min_length=1, max_length=1200)
    recommendations: list[AdvisorRecommendation] = Field(min_length=1, max_length=4)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)


class StrategyAdvisor(Protocol):
    provider: str
    model: str

    async def advise(
        self,
        *,
        action: ConsultAdvisorAction,
        observation: Observation,
        corpus: GuideCorpus,
    ) -> AdvisorDraft: ...


class OpenRouterStrategyAdvisor:
    """Small structured-output client for one read-only advisory call."""

    provider = "openrouter"

    def __init__(self, config: AdvisorConfig) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The OpenRouter advisor requires the optional dependency: "
                "pip install -e '.[openai]'"
            ) from exc
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required when advisor.enabled=true.")
        self.config = config
        self.model = config.model
        self.client: Any = AsyncOpenAI(api_key=api_key, base_url=config.base_url)

    async def advise(
        self,
        *,
        action: ConsultAdvisorAction,
        observation: Observation,
        corpus: GuideCorpus,
    ) -> AdvisorDraft:
        extra: dict[str, Any] = {}
        if self.config.reasoning_effort != "none":
            extra["reasoning_effort"] = self.config.reasoning_effort
        response_format = portable_response_format(AdvisorDraft)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a read-only creative strategic collaborator to another "
                    "model playing Kenshi. Offer materially different possibilities, "
                    "not a progression script or a conventional beginner playbook. "
                    "The observation shows what is currently happening and what is "
                    "currently possible; help the playing model imagine freely within "
                    "that evidence. Deliberate recoverable risk, experimentation, and "
                    "unusual goals are valid. The rank field orders this answer by fit "
                    "to the question, not by a universal idea of correct play. Do not "
                    "author controller actions, plans, keys, clicks, coordinates, or "
                    "native commands. Treat the observation as current world evidence "
                    "and the attributed corpus as fallible sourced possibilities. The "
                    "current world's `telemetry_semantics` are authoritative "
                    "definitions of exported fields. Never invent a mechanic or source "
                    "ID. Make uncertainty explicit, especially where community advice "
                    "is contested."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": action.question,
                        "focus": action.focus.value,
                        "current_world": advisor_world_payload(observation),
                        "guide_corpus": corpus.prompt_payload(action.focus),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        request_base = {
            "model": self.model,
            "max_completion_tokens": self.config.max_output_tokens,
            "extra_body": {
                "provider": {
                    "sort": self.config.provider_sort,
                    "require_parameters": self.config.require_parameters,
                }
            },
            **extra,
        }
        sent_messages = messages
        async with asyncio.timeout(self.config.timeout_seconds):
            response = await self.client.chat.completions.create(
                messages=sent_messages,
                response_format=response_format,
                **request_base,
            )

        response_parts: list[str] = []
        continuations = 0
        while True:
            choice = response.choices[0]
            message = choice.message
            content = message_field(message, "content")
            if not isinstance(content, str) or not content:
                raise RuntimeError("OpenRouter advisor response contained no text.")
            response_parts.append(content)
            combined = "".join(response_parts)
            finish_reason = message_field(choice, "finish_reason")
            try:
                draft = AdvisorDraft.model_validate_json(_json_body(combined))
            except ValidationError as exc:
                truncated = (
                    isinstance(finish_reason, str)
                    and finish_reason in TRUNCATED_FINISH_REASONS
                    or structured_json_was_truncated(exc)
                )
                if not truncated:
                    raise
                validation_error = exc
            else:
                # A valid complete object is authoritative even if a provider
                # mislabeled its terminal reason as length.
                return draft

            if continuations >= self.config.max_output_continuations:
                raise RuntimeError(
                    "Advisor structured response remained truncated after "
                    f"{continuations} continuation(s): {validation_error}"
                ) from validation_error
            continuations += 1
            sent_messages = [
                *sent_messages,
                assistant_continuation(message),
                {
                    "role": "user",
                    "content": CONTINUE_STRUCTURED_JSON_SUFFIX,
                },
            ]
            async with asyncio.timeout(self.config.timeout_seconds):
                response = await self.client.chat.completions.create(
                    messages=sent_messages,
                    **request_base,
                )


class AdvisorSession:
    """Per-run policy, budget, suppression, and latest-brief state."""

    def __init__(
        self,
        config: AdvisorConfig,
        corpus: GuideCorpus,
        client: StrategyAdvisor,
    ) -> None:
        self.config = config
        self.corpus = corpus
        self.client = client
        self.calls_used = 0
        self.last_call_step: int | None = None
        self.last_state_fingerprint: str | None = None
        self.latest_brief: AdvisorBrief | None = None
        self.request_pending = False

    def availability(self, observation: Observation) -> AdvisorAvailability:
        fingerprint = advisor_state_fingerprint(observation)
        remaining = max(self.config.max_calls_per_run - self.calls_used, 0)
        cooldown = self._cooldown_remaining(observation.step_index)
        if self.request_pending:
            return self._availability(
                may_request=False,
                suggested=False,
                reason=(
                    "An advisor request is already pending; keep playing and "
                    "use the brief after it arrives."
                ),
                cooldown=cooldown,
            )
        if remaining == 0:
            return self._availability(
                may_request=False,
                suggested=False,
                reason="The per-run advisor call budget is exhausted.",
                cooldown=cooldown,
            )
        if cooldown > 0:
            return self._availability(
                may_request=False,
                suggested=False,
                reason=f"Advisor cooldown has {cooldown} strategic turn(s) remaining.",
                cooldown=cooldown,
            )
        if fingerprint == self.last_state_fingerprint:
            return self._availability(
                may_request=False,
                suggested=False,
                reason="Meaningful state has not changed since the last advisor call.",
                cooldown=0,
            )

        repeated = repeated_action_signal(
            observation.recent_action_outcomes[-self.config.stall_window_actions :],
            threshold=self.config.stall_repeat_threshold,
        )
        cadence_due = (
            observation.step_index >= self.config.cadence_steps
            if self.last_call_step is None
            else observation.step_index - self.last_call_step >= self.config.cadence_steps
        )
        if repeated is not None:
            reason = f"Suggested: recent actions repeat the same strategy ({repeated})."
            suggested = True
        elif cadence_due:
            reason = "Suggested: the periodic strategic review interval is due."
            suggested = True
        else:
            reason = "Available on request; no periodic or repetition signal is due."
            suggested = False
        return self._availability(
            may_request=True,
            suggested=suggested,
            reason=reason,
            cooldown=0,
        )

    async def consult(
        self,
        action: ConsultAdvisorAction,
        observation: Observation,
    ) -> AdvisorConsultEvidence:
        fingerprint = advisor_state_fingerprint(observation)
        availability = self.availability(observation)
        if not availability.may_request:
            if availability.request_pending:
                status = AdvisorConsultStatus.PENDING
            elif self.calls_used >= self.config.max_calls_per_run:
                status = AdvisorConsultStatus.BUDGET_EXHAUSTED
            elif availability.cooldown_steps_remaining > 0:
                status = AdvisorConsultStatus.COOLDOWN
            else:
                status = AdvisorConsultStatus.UNCHANGED_STATE
            return AdvisorConsultEvidence(
                status=status,
                reason=availability.reason,
                calls_used=self.calls_used,
                max_calls=self.config.max_calls_per_run,
                state_fingerprint=fingerprint,
            )

        self.calls_used += 1
        self.last_call_step = observation.step_index
        self.last_state_fingerprint = fingerprint
        self.request_pending = True
        try:
            try:
                draft = await self.client.advise(
                    action=action,
                    observation=observation,
                    corpus=self.corpus,
                )
                source_ids = _validate_source_ids(draft, self.corpus)
                sources = self.corpus.source_map()
                brief = AdvisorBrief(
                    brief_id=f"advisor-{uuid4().hex}",
                    question=action.question,
                    focus=action.focus,
                    based_on_revision=observation.world_revision,
                    summary=draft.summary,
                    recommendations=draft.recommendations,
                    uncertainties=draft.uncertainties,
                    sources=[
                        sources[source_id].attribution() for source_id in source_ids
                    ],
                    corpus_version=self.corpus.corpus_version,
                    provider=self.client.provider,
                    model=self.client.model,
                )
            except Exception as exc:
                return AdvisorConsultEvidence(
                    status=AdvisorConsultStatus.FAILED,
                    reason=f"Advisor call failed: {type(exc).__name__}: {exc}",
                    calls_used=self.calls_used,
                    max_calls=self.config.max_calls_per_run,
                    state_fingerprint=fingerprint,
                )

            self.latest_brief = brief
            return AdvisorConsultEvidence(
                status=AdvisorConsultStatus.ANSWERED,
                reason="The read-only advisor returned a source-attributed strategic brief.",
                calls_used=self.calls_used,
                max_calls=self.config.max_calls_per_run,
                state_fingerprint=fingerprint,
                brief=brief,
            )
        finally:
            self.request_pending = False

    def _cooldown_remaining(self, step_index: int) -> int:
        if self.last_call_step is None:
            return 0
        elapsed = max(step_index - self.last_call_step, 0)
        return max(self.config.cooldown_steps - elapsed, 0)

    def _availability(
        self,
        *,
        may_request: bool,
        suggested: bool,
        reason: str,
        cooldown: int,
    ) -> AdvisorAvailability:
        return AdvisorAvailability(
            enabled=True,
            may_request=may_request,
            suggested=suggested,
            request_pending=self.request_pending,
            reason=reason,
            calls_used=self.calls_used,
            max_calls=self.config.max_calls_per_run,
            cooldown_steps_remaining=cooldown,
            corpus_version=self.corpus.corpus_version,
            latest_brief=self.latest_brief,
        )


def disabled_advisor_availability() -> AdvisorAvailability:
    return AdvisorAvailability()


def advisor_state_fingerprint(observation: Observation) -> str:
    """Hash meaningful strategy state, excluding frame/telemetry tick churn."""

    telemetry = observation.telemetry
    selected = None
    if telemetry is not None:
        selected = next((item for item in telemetry.squad if item.selected), None)
    payload: dict[str, Any] = {
        "objective": observation.objective,
        "money": telemetry.game.money if telemetry is not None else None,
        "location": telemetry.game.location_name if telemetry is not None else None,
        "selected": (
            {
                "id": selected.id,
                "alive": selected.alive,
                "conscious": selected.conscious,
                "in_combat": selected.in_combat,
                "hunger": selected.hunger,
                "indoors": selected.indoors,
                "food_items": selected.food_items,
                "position": (
                    {
                        "x": round(selected.position.x, 1),
                        "y": round(selected.position.y, 1),
                        "z": round(selected.position.z, 1),
                    }
                    if selected.position is not None
                    else None
                ),
                "inventory": [
                    {
                        "name": item.name,
                        "quantity": item.quantity,
                    }
                    for item in selected.inventory
                ],
            }
            if selected is not None
            else None
        ),
        "ui": (
            {
                "active_screen": telemetry.ui.active_screen,
                "dialogue_open": telemetry.ui.dialogue_open,
                "dialogue_target_id": telemetry.ui.dialogue_target_id,
                "dialogue_options": telemetry.ui.dialogue_options,
                "open_inventory_windows": telemetry.ui.open_inventory_windows,
                "management_screen_open": telemetry.ui.management_screen_open,
            }
            if telemetry is not None
            else None
        ),
        "recent_actions": [
            _action_signal(outcome) for outcome in observation.recent_action_outcomes[-12:]
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def repeated_action_signal(
    outcomes: Sequence[ActionOutcome],
    *,
    threshold: int,
) -> str | None:
    signals = [_action_signal(outcome) for outcome in outcomes]
    if not signals:
        return None
    signal, count = Counter(signals).most_common(1)[0]
    return signal if count >= threshold else None


def advisor_world_payload(observation: Observation) -> dict[str, Any]:
    digest = observation.log_digest()
    return {
        "objective": observation.objective,
        "step_index": observation.step_index,
        "world_revision": observation.world_revision.model_dump(mode="json"),
        "telemetry": model_facing_telemetry_payload(digest.get("telemetry")),
        "squad_nutrition": observation.squad_nutrition_digest(),
        "telemetry_semantics": {
            "selected.nutrition_reserve": (
                "The current reserve on the squad_nutrition scale. Use that "
                "digest's status and thresholds to decide urgency."
            ),
            "selected.food_items": (
                "A fallible native scalar that has disagreed with carried item "
                "telemetry. Use named inventory plus guide facts to decide whether "
                "a carried item is edible."
            ),
            "selected.indoors": (
                "Current building-handle membership. It can remain true just "
                "outside a doorway and is not authoritative visual containment."
            ),
        },
        "dialogue_targets": observation.dialogue_target_digest(),
        "travel_destinations": observation.travel_destination_digest(),
        "known_map_destinations": observation.known_map_destination_digest(),
        "recent_action_outcomes": [
            outcome.model_dump(mode="json")
            for outcome in observation.recent_action_outcomes[-12:]
        ],
        "memories": [
            {
                "kind": memory.kind.value,
                "content": memory.content,
                "grounding": memory.grounding,
                "target_id": memory.target_id,
            }
            # Runtime orders exact current-target matches before the general
            # salience list. Keep that ordering when this smaller advisor view
            # needs to truncate.
            for memory in observation.memories[:16]
        ],
    }


def _action_signal(outcome: ActionOutcome) -> str:
    action = outcome.action.model_dump(mode="json")
    stable = {
        key: action[key]
        for key in (
            "kind",
            "target_id",
            "exact_label",
            "binding",
            "item_name",
            "focus",
        )
        if key in action
    }
    return json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_source_ids(draft: AdvisorDraft, corpus: GuideCorpus) -> list[str]:
    known = corpus.source_map()
    ordered: list[str] = []
    for recommendation in draft.recommendations:
        for source_id in recommendation.source_ids:
            if source_id not in known:
                raise ValueError(f"advisor cited unknown source ID {source_id!r}")
            if source_id not in ordered:
                ordered.append(source_id)
    if not ordered:
        raise ValueError("advisor returned no guide attribution")
    return ordered


def _json_body(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        _, _, remainder = text.partition("\n")
        text = remainder.rpartition("```")[0].strip() or remainder.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start > 0 and end > start:
        text = text[start : end + 1]
    return text
