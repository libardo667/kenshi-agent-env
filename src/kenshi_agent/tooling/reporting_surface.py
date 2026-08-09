"""Which post-mortem questions a run bundle can actually answer.

A run bundle is the only durable account of what happened. Whether it is a good
one is not a matter of size - this project's bundles are large - but of which
questions survive into it. That has never been measured, so gaps were found the
expensive way: by needing an answer months later and discovering the evidence
was digested away.

Every question below is one that was actually asked of a bundle during
development, with the event that answers it or the reason nothing does. It is
a coverage report, not a wish list: a question earns a row by having been
needed, and its status is derived by inspecting a real bundle rather than
asserted here.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PostMortemQuestion:
    """One question, and how a bundle would have to answer it."""

    question: str
    # The event type that carries the answer, when one does.
    event_type: str
    # The dotted key path within the payload whose presence proves the answer is
    # recorded. Resolved structurally, not by substring: searching the rendered
    # JSON for "offers" quietly matched nothing while the field was named
    # "offered", and three built-and-working channels were reported as gaps. A
    # measuring tool that manufactures gaps is worse than no measurement, so a
    # path that resolves in no bundle is reported as a broken probe rather than
    # as missing evidence.
    probe: str
    why_it_matters: str


# Ordered by how often the absence actually cost something.
POST_MORTEM_QUESTIONS: tuple[PostMortemQuestion, ...] = (
    PostMortemQuestion(
        question="What did the agent choose, and did it work?",
        event_type="affordance_receipt",
        probe="receipt.affordance",
        why_it_matters="The minimum account of a run: every decision and its outcome.",
    ),
    PostMortemQuestion(
        question="Why did a chosen operation fail?",
        event_type="affordance_receipt",
        probe="receipt.lifecycle",
        why_it_matters=(
            "Distinguishes a refusal from a stall from a handler error, which "
            "otherwise all read as 'failed'."
        ),
    ),
    PostMortemQuestion(
        question="What else could it have chosen at that moment?",
        event_type="planner_context_prepared",
        probe="offered",
        why_it_matters=(
            "Separates 'the model ignored a good option' from 'the option was "
            "never on the menu'. These have completely different fixes, and "
            "guessing between them was wrong more than once in one session."
        ),
    ),
    PostMortemQuestion(
        question="Why was an expected affordance not offered?",
        event_type="planner_context_prepared",
        probe="withheld_unauthorable",
        why_it_matters=(
            "The most common question after a disappointing run, and the one "
            "that currently costs an hour of reading enumeration code."
        ),
    ),
    PostMortemQuestion(
        question="What retained work and current activity did Kenshi report?",
        event_type="observation",
        probe="telemetry.character_work",
        why_it_matters=(
            "A retained order pulled a character out of a trade conversation "
            "and made a move order look stalled. Neither was diagnosable from "
            "the bundle, because the digest kept no separate work channels."
        ),
    ),
    PostMortemQuestion(
        question="What did the native layer actually say?",
        event_type="observation",
        probe="telemetry.controller_commands",
        why_it_matters=(
            "Command results like target_already_reached are correct refusals "
            "that the plan layer reports as failures."
        ),
    ),
    PostMortemQuestion(
        question="What was the economic state over time?",
        event_type="observation",
        probe="telemetry.game.money",
        why_it_matters="Proves a sale moved money rather than merely returning success.",
    ),
    PostMortemQuestion(
        question="What was on screen when a UI choice was made?",
        event_type="observation",
        probe="telemetry.ui.item_cells",
        why_it_matters=(
            "Item cells are kept because a post-mortem must be able to say what "
            "was for sale. Other controls are not, so 'which buttons existed' "
            "is unanswerable."
        ),
    ),
)


def resolve_probe(payload: Any, path: str) -> bool:
    """Whether a dotted key path is present in one payload.

    Lists are descended into, because the evidence for several questions lives
    inside repeated entries. Presence is what is measured, not truthiness: a
    recorded `money: 0` answers "what was the economic state" exactly as well
    as a recorded `money: 4000`.
    """

    head, _, rest = path.partition(".")
    if isinstance(payload, list):
        return any(resolve_probe(entry, path) for entry in payload)
    if not isinstance(payload, dict) or head not in payload:
        return False
    return True if not rest else resolve_probe(payload[head], rest)


@dataclass(frozen=True, slots=True)
class QuestionCoverage:
    question: PostMortemQuestion
    events_present: int
    answered: bool
    # False when the probe resolved in no bundle anywhere, which means the path
    # is wrong rather than the evidence missing. Kept distinct so a typo can
    # never be read as a gap in the run bundle.
    probe_resolvable: bool = True

    @property
    def status(self) -> str:
        if not self.probe_resolvable:
            return "BROKEN PROBE"
        if not self.events_present:
            return "no evidence"
        return "answered" if self.answered else "recorded but silent"


@dataclass(frozen=True, slots=True)
class ReportingSurface:
    bundle: str
    total_events: int
    event_counts: tuple[tuple[str, int], ...]
    coverage: tuple[QuestionCoverage, ...]

    @property
    def answered(self) -> int:
        return sum(1 for entry in self.coverage if entry.answered)

    @property
    def unanswerable(self) -> tuple[QuestionCoverage, ...]:
        return tuple(entry for entry in self.coverage if not entry.answered)

    @property
    def observation_share(self) -> float:
        """How much of the bundle is raw observation.

        A bundle dominated by observations is not thereby informative; the
        questions above are answered by the small events, not the bulk.
        """

        if not self.total_events:
            return 0.0
        bulk = sum(
            count
            for name, count in self.event_counts
            if name in {"observation", "world_state_update", "world_state_event"}
        )
        return bulk / self.total_events


def _events(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def assess_reporting_surface(bundle: Path) -> ReportingSurface:
    """Measure which post-mortem questions one real bundle can answer."""

    counts: dict[str, int] = {}
    probes_seen: dict[str, bool] = {
        question.probe: False for question in POST_MORTEM_QUESTIONS
    }
    total = 0
    for event in _events(bundle):
        total += 1
        name = str(event.get("event_type", "?"))
        counts[name] = counts.get(name, 0) + 1
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        for question in POST_MORTEM_QUESTIONS:
            if question.event_type != name or probes_seen[question.probe]:
                continue
            if resolve_probe(payload, question.probe):
                probes_seen[question.probe] = True

    coverage = tuple(
        QuestionCoverage(
            question=question,
            events_present=counts.get(question.event_type, 0),
            answered=probes_seen[question.probe],
        )
        for question in POST_MORTEM_QUESTIONS
    )
    return ReportingSurface(
        bundle=bundle.parent.name,
        total_events=total,
        event_counts=tuple(sorted(counts.items())),
        coverage=coverage,
    )


def render_reporting_surface(surface: ReportingSurface) -> list[str]:
    """Render the coverage, leading with what cannot be answered."""

    lines = [
        f"bundle                {surface.bundle}",
        f"events                {surface.total_events:6d}",
        f"observation share     {surface.observation_share:6.1%}",
        f"questions answered    {surface.answered:3d} of {len(surface.coverage)}",
        "",
        "POST-MORTEM COVERAGE",
    ]
    for entry in surface.coverage:
        marker = "  " if entry.answered else ">>"
        lines.append(f"{marker}{entry.question.question}")
        lines.append(
            f"    {entry.status:<22} via {entry.question.event_type} "
            f"({entry.events_present} events)"
        )
    if surface.unanswerable:
        lines.extend(("", "WHY THE GAPS MATTER"))
        for entry in surface.unanswerable:
            lines.append(f"  {entry.question.question}")
            lines.append(f"    {entry.question.why_it_matters}")
    return lines
