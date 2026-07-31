from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kenshi_agent.continuity import ContinuityLedger
from kenshi_agent.models import (
    ActionOutcome,
    ActionOutcomeAssessment,
    ActionReceipt,
    ActivateVisibleControlAction,
    CharacterState,
    ControlMode,
    Disposition,
    GameState,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PlannerDecision,
    PurchaseEvidence,
    PurchaseItemAction,
    PurchaseStatus,
    SemanticActionReceipt,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
    WorldStateRevision,
)
from kenshi_agent.non_progress import (
    retry_state_fingerprint,
    unchanged_definitive_no_op_reason,
)
from kenshi_agent.runtime import AgentRuntime


def _bounds(
    min_x: float,
    min_y: float,
    *,
    width: float = 0.04,
    height: float = 0.08,
) -> NormalizedPointerBounds:
    return NormalizedPointerBounds(
        min_x=min_x,
        min_y=min_y,
        max_x=min_x + width,
        max_y=min_y + height,
    )


def _item(
    *,
    window: str,
    name: str,
    quantity: int,
    min_x: float,
    min_y: float,
    section: str = "main",
    item_base_value: int | None = None,
    selected_inventory_accepts_item: bool | None = None,
) -> VisibleUIControl:
    return VisibleUIControl(
        label=name,
        role="item",
        window=window,
        item_name=name,
        item_base_value=item_base_value,
        item_quantity=quantity,
        item_type=7,
        selected_inventory_accepts_item=selected_inventory_accepts_item,
        section=section,
        bounds=_bounds(min_x, min_y),
    )


def _trade_observation(
    *,
    sequence: int,
    player_item_x: float,
    recent_outcomes: list[ActionOutcome] | None = None,
) -> Observation:
    controls = [
        _item(
            window="STEYERFAST",
            name="Cooked Vegetables",
            quantity=1,
            min_x=player_item_x,
            min_y=0.55,
        ),
        VisibleUIControl(
            label="ARRANGE",
            role="button",
            window="STEYERFAST",
            bounds=_bounds(0.10, 0.80),
        ),
        _item(
            window="BARMAN",
            name="Greenfruit",
            quantity=1,
            min_x=0.65,
            min_y=0.55,
            section="backpack_content",
            item_base_value=33,
            selected_inventory_accepts_item=False,
        ),
    ]
    return Observation(
        run_id="purchase-retry",
        step_index=sequence,
        observed_at=datetime(2026, 7, 30, 12, sequence, tzinfo=UTC),
        mode="live",
        control_mode=ControlMode.NATIVE_ASSISTED,
        world_revision=WorldStateRevision(
            telemetry_sequence=sequence,
            frame_sequence=sequence,
            capability_epoch=1,
            observed_at_monotonic=float(sequence),
        ),
        telemetry=TelemetrySnapshot(
            sequence=sequence,
            identity_session_id="session-trade",
            capabilities=[
                "game.money",
                "identity.stable_handles",
                "nearby.characters",
                "nearby.shop_owners",
                "squad.basic",
                "squad.inventory",
                "ui.inventory",
                "ui.visible_controls",
            ],
            game=GameState(
                loaded=True,
                paused=True,
                money=344,
                elapsed_minutes=6713.0 + sequence,
            ),
            ui=UIState(
                active_screen="trade",
                open_inventory_windows=2,
                selected_character_id="character-steyerfast",
                selected_character_ids=["character-steyerfast"],
                visible_controls=controls,
                visible_controls_complete=True,
            ),
            squad=[
                CharacterState(
                    id="character-steyerfast",
                    name="Steyerfast",
                    selected=True,
                    inventory_complete=True,
                )
            ],
            nearby_entities=[
                NearbyEntity(
                    id="seller-barman",
                    name="Barman",
                    disposition=Disposition.NEUTRAL,
                    shop_inventory_owner=True,
                )
            ],
        ),
        recent_action_outcomes=recent_outcomes or [],
    )


def _purchase() -> PurchaseItemAction:
    return PurchaseItemAction(
        cell_label="Greenfruit",
        item_name="Greenfruit",
        expected_price=33,
        quantity=1,
        window="BARMAN",
        seller_id="seller-barman",
    )


def _replace_seller_cell(
    observation: Observation,
    **updates: object,
) -> Observation:
    telemetry = observation.telemetry
    assert telemetry is not None
    controls = telemetry.ui.visible_controls
    assert controls is not None
    seller_cell = controls[-1]
    return observation.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "ui": telemetry.ui.model_copy(
                        update={
                            "visible_controls": [
                                *controls[:-1],
                                seller_cell.model_copy(update=updates),
                            ]
                        }
                    )
                }
            )
        }
    )


def _outcome(
    *,
    outcome_id: str,
    action: PurchaseItemAction | ActivateVisibleControlAction,
    assessment: ActionOutcomeAssessment,
    retry_fingerprint: str | None = None,
    semantic_status: str | None = None,
) -> ActionOutcome:
    return ActionOutcome(
        outcome_id=outcome_id,
        run_id="purchase-retry",
        plan_id=f"plan-{outcome_id}",
        plan_version=1,
        step_id="step",
        step_index=1,
        intent="Exercise the trade retry boundary.",
        action=action,
        executed=True,
        assessment=assessment,
        causal_revision_advanced=True,
        semantic_status=semantic_status,
        feedback="The action produced no relevant state change.",
        identity_session_id="session-trade",
        retry_state_fingerprint=retry_fingerprint,
    )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("item_base_value", 44),
        ("item_name", "Riceweed"),
        ("item_quantity", 2),
        ("item_type", 8),
        ("selected_inventory_accepts_item", True),
        ("section", "main"),
    ],
)
def test_every_purchase_relevant_cell_fact_changes_the_fingerprint(
    field: str,
    changed: object,
) -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    initial_fingerprint = retry_state_fingerprint(action, initial)
    assert initial_fingerprint is not None

    changed_observation = _replace_seller_cell(initial, **{field: changed})

    assert retry_state_fingerprint(action, changed_observation) != initial_fingerprint


def test_purchase_fingerprint_conserves_session_money_and_selected_buyer() -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    initial_fingerprint = retry_state_fingerprint(action, initial)
    assert initial_fingerprint is not None
    telemetry = initial.telemetry
    assert telemetry is not None

    different_session = initial.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={"identity_session_id": "session-other"}
            )
        }
    )
    different_money = initial.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={"game": telemetry.game.model_copy(update={"money": 343})}
            )
        }
    )
    other_buyer = telemetry.squad[0].model_copy(
        update={"id": "character-other"}
    )
    different_buyer = initial.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "squad": [other_buyer],
                    "ui": telemetry.ui.model_copy(
                        update={
                            "selected_character_id": other_buyer.id,
                            "selected_character_ids": [other_buyer.id],
                        }
                    ),
                }
            )
        }
    )

    assert retry_state_fingerprint(action, different_session) != initial_fingerprint
    assert retry_state_fingerprint(action, different_money) != initial_fingerprint
    assert retry_state_fingerprint(action, different_buyer) != initial_fingerprint


def test_seller_cell_order_is_not_purchase_progress() -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    telemetry = initial.telemetry
    assert telemetry is not None
    controls = telemetry.ui.visible_controls
    assert controls is not None
    second_cell = controls[-1].model_copy(
        update={"item_quantity": 2, "bounds": _bounds(0.75, 0.55)}
    )
    ordered = initial.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "ui": telemetry.ui.model_copy(
                        update={"visible_controls": [*controls, second_cell]}
                    )
                }
            )
        }
    )
    reversed_controls = [*controls[:-1], second_cell, controls[-1]]
    reversed_observation = initial.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "ui": telemetry.ui.model_copy(
                        update={"visible_controls": reversed_controls}
                    )
                }
            )
        }
    )

    assert retry_state_fingerprint(action, ordered) == retry_state_fingerprint(
        action, reversed_observation
    )


def test_purchase_fingerprint_uses_the_exact_priced_cell_when_labels_collide() -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    telemetry = initial.telemetry
    assert telemetry is not None
    controls = telemetry.ui.visible_controls
    assert controls is not None
    other_price = controls[-1].model_copy(
        update={
            "item_base_value": 44,
            "item_quantity": 9,
            "bounds": _bounds(0.75, 0.55),
        }
    )
    with_collision = initial.model_copy(
        update={
            "telemetry": telemetry.model_copy(
                update={
                    "ui": telemetry.ui.model_copy(
                        update={"visible_controls": [*controls, other_price]}
                    )
                }
            )
        }
    )
    changed_other_price = with_collision.model_copy(
        update={
            "telemetry": with_collision.telemetry.model_copy(
                update={
                    "ui": with_collision.telemetry.ui.model_copy(
                        update={
                            "visible_controls": [
                                *with_collision.telemetry.ui.visible_controls[:-1],
                                other_price.model_copy(update={"item_quantity": 8}),
                            ]
                        }
                    )
                }
            )
        }
    )

    assert retry_state_fingerprint(
        action, with_collision
    ) == retry_state_fingerprint(action, changed_other_price)


def test_purchase_fingerprint_fails_closed_without_complete_exact_authority() -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    telemetry = initial.telemetry
    assert telemetry is not None
    controls = telemetry.ui.visible_controls
    assert controls is not None
    selected = telemetry.squad[0]
    seller = telemetry.nearby_entities[0]

    variants = [
        initial.model_copy(update={"telemetry": None}),
        initial.model_copy(update={"telemetry_stale": True}),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "ui": telemetry.ui.model_copy(
                            update={"active_screen": "inventory"}
                        )
                    }
                )
            }
        ),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "ui": telemetry.ui.model_copy(
                            update={"visible_controls": None}
                        )
                    }
                )
            }
        ),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "ui": telemetry.ui.model_copy(
                            update={"visible_controls_complete": False}
                        )
                    }
                )
            }
        ),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "ui": telemetry.ui.model_copy(
                            update={"selected_character_id": None}
                        )
                    }
                )
            }
        ),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "game": telemetry.game.model_copy(update={"money": None})
                    }
                )
            }
        ),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "squad": [
                            selected.model_copy(update={"selected": False})
                        ]
                    }
                )
            }
        ),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"squad": [selected, selected]}
                )
            }
        ),
        _replace_seller_cell(initial, role="button"),
        _replace_seller_cell(initial, label="Riceweed"),
        _replace_seller_cell(initial, window="GENERAL STORAGE"),
        _replace_seller_cell(initial, selected_inventory_accepts_item=None),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "nearby_entities": [
                            seller.model_copy(update={"shop_inventory_owner": False})
                        ]
                    }
                )
            }
        ),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "nearby_entities": [
                            seller.model_copy(update={"name": "Trade Ninja"})
                        ]
                    }
                )
            }
        ),
        initial.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={"nearby_entities": [seller, seller]}
                )
            }
        ),
    ]

    assert all(retry_state_fingerprint(action, variant) is None for variant in variants)


def test_no_op_arrange_does_not_clear_a_purchase_retry_barrier() -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    fingerprint = retry_state_fingerprint(action, initial)
    assert fingerprint is not None
    failed_purchase = _outcome(
        outcome_id="ao-1",
        action=action,
        assessment=ActionOutcomeAssessment.NO_OP,
        retry_fingerprint=fingerprint,
        semantic_status=PurchaseStatus.NOT_PURCHASED,
    )
    no_op_arrange = _outcome(
        outcome_id="ao-2",
        action=ActivateVisibleControlAction(
            exact_label="ARRANGE",
            window="STEYERFAST",
        ),
        assessment=ActionOutcomeAssessment.NO_OP,
    )
    unchanged = _trade_observation(
        sequence=9,
        player_item_x=0.15,
        recent_outcomes=[failed_purchase, no_op_arrange],
    )

    reason = unchanged_definitive_no_op_reason(action, unchanged)

    assert reason is not None
    assert "ao-1" in reason


def test_cosmetic_inventory_shuffle_does_not_clear_a_purchase_retry_barrier() -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    fingerprint = retry_state_fingerprint(action, initial)
    assert fingerprint is not None
    failed_purchase = _outcome(
        outcome_id="ao-1",
        action=action,
        assessment=ActionOutcomeAssessment.NO_OP,
        retry_fingerprint=fingerprint,
        semantic_status=PurchaseStatus.NOT_PURCHASED,
    )
    cosmetically_shuffled = _trade_observation(
        sequence=2,
        player_item_x=0.25,
        recent_outcomes=[failed_purchase],
    )

    assert (
        unchanged_definitive_no_op_reason(action, cosmetically_shuffled)
        is not None
    )


def test_kenshi_fit_verdict_clears_one_purchase_retry_barrier() -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    fingerprint = retry_state_fingerprint(action, initial)
    assert fingerprint is not None
    failed_purchase = _outcome(
        outcome_id="ao-1",
        action=action,
        assessment=ActionOutcomeAssessment.NO_OP,
        retry_fingerprint=fingerprint,
        semantic_status=PurchaseStatus.NOT_PURCHASED,
    )
    rearranged = _trade_observation(
        sequence=2,
        player_item_x=0.25,
        recent_outcomes=[failed_purchase],
    )
    assert rearranged.telemetry is not None
    assert rearranged.telemetry.ui.visible_controls is not None
    seller_cell = rearranged.telemetry.ui.visible_controls[-1]
    rearranged = rearranged.model_copy(
        update={
            "telemetry": rearranged.telemetry.model_copy(
                update={
                    "ui": rearranged.telemetry.ui.model_copy(
                        update={
                            "visible_controls": [
                                *rearranged.telemetry.ui.visible_controls[:-1],
                                seller_cell.model_copy(
                                    update={
                                        "selected_inventory_accepts_item": True
                                    }
                                ),
                            ]
                        }
                    )
                }
            )
        }
    )

    assert retry_state_fingerprint(action, rearranged) != fingerprint
    assert unchanged_definitive_no_op_reason(action, rearranged) is None


def test_retry_barrier_requires_the_complete_definitive_no_op_terminal() -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    fingerprint = retry_state_fingerprint(action, initial)
    assert fingerprint is not None
    failed_purchase = _outcome(
        outcome_id="ao-1",
        action=action,
        assessment=ActionOutcomeAssessment.NO_OP,
        retry_fingerprint=fingerprint,
        semantic_status=PurchaseStatus.NOT_PURCHASED,
    )
    blocked = _trade_observation(
        sequence=2,
        player_item_x=0.15,
        recent_outcomes=[failed_purchase],
    )
    assert unchanged_definitive_no_op_reason(action, blocked) == (
        "repeats definitive no-op ao-1; relevant purchase state is unchanged "
        "or cannot be proved changed"
    )

    non_terminals = [
        failed_purchase.model_copy(update={"executed": False}),
        failed_purchase.model_copy(
            update={"assessment": ActionOutcomeAssessment.CHANGED}
        ),
        failed_purchase.model_copy(update={"causal_revision_advanced": False}),
        failed_purchase.model_copy(
            update={"semantic_status": PurchaseStatus.PURCHASED}
        ),
    ]
    for outcome in non_terminals:
        observation = blocked.model_copy(update={"recent_action_outcomes": [outcome]})
        assert unchanged_definitive_no_op_reason(action, observation) is None


def test_retry_barrier_clears_only_for_a_provably_new_session_or_state() -> None:
    action = _purchase()
    initial = _trade_observation(sequence=1, player_item_x=0.15)
    fingerprint = retry_state_fingerprint(action, initial)
    assert fingerprint is not None
    failed_purchase = _outcome(
        outcome_id="ao-1",
        action=action,
        assessment=ActionOutcomeAssessment.NO_OP,
        retry_fingerprint=fingerprint,
        semantic_status=PurchaseStatus.NOT_PURCHASED,
    )

    changed_session = _trade_observation(
        sequence=2,
        player_item_x=0.15,
        recent_outcomes=[
            failed_purchase.model_copy(
                update={"identity_session_id": "session-before-reload"}
            )
        ],
    )
    assert unchanged_definitive_no_op_reason(action, changed_session) is None

    missing_stored_fingerprint = _trade_observation(
        sequence=2,
        player_item_x=0.15,
        recent_outcomes=[
            failed_purchase.model_copy(update={"retry_state_fingerprint": None})
        ],
    )
    assert (
        unchanged_definitive_no_op_reason(action, missing_stored_fingerprint)
        is not None
    )

    missing_current_fingerprint = _replace_seller_cell(
        _trade_observation(
            sequence=2,
            player_item_x=0.15,
            recent_outcomes=[failed_purchase],
        ),
        selected_inventory_accepts_item=None,
    )
    assert (
        unchanged_definitive_no_op_reason(action, missing_current_fingerprint)
        is not None
    )


def test_retry_barrier_ignores_other_actions_and_absent_history() -> None:
    action = _purchase()
    empty = _trade_observation(sequence=1, player_item_x=0.15)
    assert unchanged_definitive_no_op_reason(action, empty) is None
    assert (
        unchanged_definitive_no_op_reason(
            ActivateVisibleControlAction(
                exact_label="ARRANGE",
                window="STEYERFAST",
            ),
            empty,
        )
        is None
    )

    different_purchase = action.model_copy(update={"quantity": 2})
    fingerprint = retry_state_fingerprint(action, empty)
    assert fingerprint is not None
    unrelated = _outcome(
        outcome_id="ao-9",
        action=different_purchase,
        assessment=ActionOutcomeAssessment.NO_OP,
        retry_fingerprint=fingerprint,
        semantic_status=PurchaseStatus.NOT_PURCHASED,
    )
    with_unrelated = empty.model_copy(
        update={"recent_action_outcomes": [unrelated]}
    )
    assert unchanged_definitive_no_op_reason(action, with_unrelated) is None


def test_runtime_records_the_post_action_purchase_retry_state() -> None:
    action = _purchase()
    before = _trade_observation(sequence=1, player_item_x=0.15)
    after = _trade_observation(sequence=2, player_item_x=0.15)
    expected = retry_state_fingerprint(action, after)
    assert expected is not None

    class Logger:
        def write(self, *_args: object, **_kwargs: object) -> None:
            return None

    runtime = object.__new__(AgentRuntime)
    runtime.run_id = "purchase-retry"
    runtime._ledger = ContinuityLedger(  # noqa: SLF001
        run_id=runtime.run_id,
        action_outcome_limit=4,
    )
    runtime.logger = Logger()
    receipt = ActionReceipt(
        action=action,
        control_mode=ControlMode.NATIVE_ASSISTED,
        accepted=True,
        executed=True,
        dry_run=False,
        primitive_actions=1,
        message="Kenshi refused the purchase: No room for that item.",
        causal_revision_advanced=True,
        completed_at_revision=after.world_revision,
        semantic=SemanticActionReceipt(
            action_kind=action.kind,
            contract_version="2",
            target_id=action.seller_id,
            revalidation="Re-bound the exact seller cell.",
            purchase=PurchaseEvidence(
                status=PurchaseStatus.NOT_PURCHASED,
                seller_id=action.seller_id,
                selected_character_id="character-steyerfast",
                item_name=action.item_name,
                requested_quantity=1,
                purchased_quantity=0,
                money_before=344,
                money_after=344,
                inventory_quantity_before=0,
                inventory_quantity_after=0,
                observed_after_sequence=2,
                reason="Kenshi refused the purchase: No room for that item.",
            ),
        ),
    )
    decision = PlannerDecision(
        intent="Attempt one Greenfruit purchase.",
        rationale="Exercise the controller-owned refusal.",
        action=action,
        confidence=1.0,
    )

    runtime._record_action_outcome(  # noqa: SLF001
        decision,
        receipt,
        before,
        after,
        plan_id="purchase-plan",
        plan_version=1,
        step_id="purchase",
    )

    recorded = runtime._ledger.recent_action_outcomes[-1]  # noqa: SLF001
    assert recorded.assessment is ActionOutcomeAssessment.NO_OP
    assert recorded.retry_state_fingerprint == expected
