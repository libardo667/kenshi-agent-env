"""A repeated identical offer is one choice seen twice, not an ambiguous one.

An affordance id is derived from what a choice *is*, so an adapter that yields
the same offer more than once yields one id several times. `offered_affordances`
collapses those and shows the planner a single choice, which it then picks
correctly - and `bind_affordance` used to walk the adapters raw, count the
copies, and refuse because the count was not one.

That refusal reported the affordance as *absent*, which is the opposite of what
had happened. Three live runs died at step zero and the diagnosis went to the
model inventing identifiers, because the message said absent and nobody counted.
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "tests")

from test_recipient_identity import A, B, _observation  # noqa: E402

from kenshi_agent import affordances as module  # noqa: E402
from kenshi_agent.affordances import (  # noqa: E402
    AffordanceSelection,
    bind_affordance,
    offered_affordances,
)


@pytest.fixture()
def observation():  # type: ignore[no-untyped-def]
    return _observation(primary=A, selected=[A, B])


def _duplicate_every_offer(monkeypatch: pytest.MonkeyPatch, copies: int) -> None:
    """Make one adapter emit each of its offers `copies` times, unchanged."""

    import dataclasses

    target = module.AFFORDANCE_ADAPTERS[0]
    original = target.enumerate

    def repeated(observation):  # type: ignore[no-untyped-def]
        for offer in original(observation):
            for _ in range(copies):
                yield offer

    doubled = dataclasses.replace(target, enumerate=repeated)
    monkeypatch.setattr(
        module,
        "AFFORDANCE_ADAPTERS",
        (doubled,) + module.AFFORDANCE_ADAPTERS[1:],
    )


@pytest.mark.parametrize("copies", [2, 10])
def test_repeated_offers_are_shown_to_the_planner_once(
    monkeypatch: pytest.MonkeyPatch,
    observation,  # type: ignore[no-untyped-def]
    copies: int,
) -> None:
    before = {offer.affordance_id for offer in offered_affordances(observation)}
    _duplicate_every_offer(monkeypatch, copies)

    after = [offer.affordance_id for offer in offered_affordances(observation)]

    assert len(after) == len(set(after)), "duplicates must not reach the planner"
    assert set(after) == before, "and duplication must not change what is offered"


@pytest.mark.parametrize("copies", [2, 10])
def test_a_repeated_offer_still_binds(
    monkeypatch: pytest.MonkeyPatch,
    observation,  # type: ignore[no-untyped-def]
    copies: int,
) -> None:
    """The regression: the choice the planner was shown must still bind."""

    _duplicate_every_offer(monkeypatch, copies)
    offer = next(
        candidate
        for candidate in offered_affordances(observation)
        if candidate.semantic == "observe"
    )

    bound = bind_affordance(
        AffordanceSelection(
            semantic=offer.semantic,
            target_id=offer.target.target_id if offer.target else None,
        ),
        observation,
    )

    assert bound.operation.kind == "noop"


@pytest.mark.parametrize("copies", [2, 10])
def test_a_repeated_offer_still_rebinds_at_the_input_boundary(
    monkeypatch: pytest.MonkeyPatch,
    observation,  # type: ignore[no-untyped-def]
    copies: int,
) -> None:
    """The same miscount, at the boundary rather than at compile time.

    `_rebind_affordance_operation` is a third walk over raw adapter output, and
    it counted duplicates too - reporting the choice as *ambiguous* where the
    compile path reported it *absent*. One live run died on each wording before
    anyone noticed they were the same defect.
    """

    from kenshi_agent.affordances import OPERATION_BINDING_AUTHORITY

    offer = next(
        candidate
        for candidate in offered_affordances(observation)
        if candidate.semantic == "observe"
    )
    bound = bind_affordance(
        AffordanceSelection(
            semantic=offer.semantic,
            target_id=offer.target.target_id if offer.target else None,
        ),
        observation,
    )

    _duplicate_every_offer(monkeypatch, copies)
    rebound = OPERATION_BINDING_AUTHORITY.rebind(bound, observation)

    assert rebound.operation == bound.operation
