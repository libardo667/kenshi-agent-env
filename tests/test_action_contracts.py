"""Reusable semantic actions and their authoritative contracts.

The point of these tests is reuse, not coverage of one scenario: the same
approach action must bind a vendor and a non-vendor identically, and the same
activation action must work for unrelated labels. Anything that would only pass
for the calibrated Barman chain is a regression of the whole milestone.
"""

from __future__ import annotations

from kenshi_agent.action_contracts import (
    ACTION_CONTRACTS,
    ACTIVATE_VISIBLE_CONTROL_CONTRACT,
    APPROACH_DIALOGUE_TARGET_CONTRACT,
    PURCHASE_ITEM_CONTRACT,
    LegacyCompatibilityLedger,
    contract_for,
    planner_visible_contracts,
    translate_legacy_skill,
)
from kenshi_agent.models import (
    ActivateVisibleControlAction,
    ApproachDialogueTargetAction,
    ClickAction,
    ControlMode,
    Disposition,
    GameState,
    IdempotencyPolicy,
    NearbyEntity,
    NormalizedPointerBounds,
    Observation,
    PurchaseItemAction,
    SkillAction,
    SkillArgument,
    TelemetrySnapshot,
    UIState,
    VisibleUIControl,
    WorldStateRevision,
)

VENDOR_ID = "entity-barman"
CIVILIAN_ID = "entity-wanderer"

APPROACH_CAPABILITIES = [
    "control.approach_vendor",
    "identity.stable_handles",
    "nearby.characters",
    "nearby.roles",
]


def _bounds(y: float) -> NormalizedPointerBounds:
    return NormalizedPointerBounds(min_x=0.1, max_x=0.4, min_y=y, max_y=y + 0.05)


def observation(
    *,
    entities: list[NearbyEntity] | None = None,
    controls: list[VisibleUIControl] | None = None,
    capabilities: list[str] | None = None,
    stale: bool = False,
    control_mode: ControlMode = ControlMode.NATIVE_ASSISTED,
) -> Observation:
    return Observation(
        run_id="contract-test",
        step_index=1,
        mode="live",
        control_mode=control_mode,
        world_revision=WorldStateRevision(telemetry_sequence=10, capability_epoch=1),
        telemetry=TelemetrySnapshot(
            sequence=10,
            identity_session_id="session-contract-test",
            capabilities=capabilities if capabilities is not None else APPROACH_CAPABILITIES,
            game=GameState(loaded=True, paused=True),
            ui=UIState(visible_controls=controls),
            nearby_entities=entities or [],
        ),
        telemetry_stale=stale,
        telemetry_age_seconds=0.1,
    )


def vendor(distance: float = 30.0) -> NearbyEntity:
    return NearbyEntity(
        id=VENDOR_ID,
        name="Barman",
        is_animal=False,
        has_dialogue=True,
        has_vendor_list=True,
        is_squad_leader=True,
        disposition=Disposition.NEUTRAL,
        distance=distance,
    )


def civilian(distance: float = 12.0) -> NearbyEntity:
    """A talkable person who owns no shop and leads no squad."""

    return NearbyEntity(
        id=CIVILIAN_ID,
        name="Nomad Wanderer",
        is_animal=False,
        has_dialogue=True,
        has_vendor_list=False,
        is_squad_leader=False,
        disposition=Disposition.FRIENDLY,
        distance=distance,
    )


class TestApproachBindsAnyDialogueTarget:
    """The whole point: approach is not a commerce affordance."""

    def test_binds_a_vendor(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
            ApproachDialogueTargetAction(target_id=VENDOR_ID),
            observation(entities=[vendor()]),
        )
        assert binding.bound
        assert binding.target_id == VENDOR_ID

    def test_binds_a_non_vendor_identically(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
            ApproachDialogueTargetAction(target_id=CIVILIAN_ID),
            observation(entities=[civilian()]),
        )
        assert binding.bound
        assert binding.target_id == CIVILIAN_ID

    def test_rejects_a_target_absent_from_current_state(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
            ApproachDialogueTargetAction(target_id="entity-ghost"),
            observation(entities=[vendor(), civilian()]),
        )
        assert not binding.bound
        assert "not a current valid dialogue target" in binding.reason

    def test_rejects_a_hostile_target(self) -> None:
        hostile = NearbyEntity(
            id="entity-bandit",
            name="Dust Bandit",
            is_animal=False,
            has_dialogue=True,
            disposition=Disposition.HOSTILE,
            distance=8.0,
        )
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
            ApproachDialogueTargetAction(target_id="entity-bandit"),
            observation(entities=[hostile]),
        )
        assert not binding.bound

    def test_stale_telemetry_cannot_bind(self) -> None:
        binding = APPROACH_DIALOGUE_TARGET_CONTRACT.bind(
            ApproachDialogueTargetAction(target_id=VENDOR_ID),
            observation(entities=[vendor()], stale=True),
        )
        assert not binding.bound
        assert "stale" in binding.reason


class TestVisibleControlBinding:
    def test_binds_two_unrelated_labels_with_the_same_action(self) -> None:
        controls = [
            VisibleUIControl(label="Show me your goods.", role="button", bounds=_bounds(0.5)),
            VisibleUIControl(label="Goodbye.", role="button", bounds=_bounds(0.6)),
        ]
        state = observation(controls=controls, capabilities=["ui.visible_controls"])

        first = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="Show me your goods.", role="button"),
            state,
        )
        second = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="Goodbye.", role="button"),
            state,
        )

        assert first.bound and second.bound
        assert first.resolved_bounds == _bounds(0.5)
        assert second.resolved_bounds == _bounds(0.6)

    def test_label_whitespace_and_case_normalize(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="Show me  your goods.", role="button", bounds=_bounds(0.5))
            ],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="show me your goods.", role="button"),
            state,
        )
        assert binding.bound
        assert binding.resolved_label == "Show me  your goods."

    def test_duplicate_label_fails_closed(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5)),
                VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.7)),
            ],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="Trade", role="button"),
            state,
        )
        assert not binding.bound
        assert "ambiguous" in binding.reason

    def test_role_mismatch_does_not_bind(self) -> None:
        state = observation(
            controls=[VisibleUIControl(label="Trade", role="text", bounds=_bounds(0.5))],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="Trade", role="button"),
            state,
        )
        assert not binding.bound

    def test_missing_capability_is_unknown_not_absent(self) -> None:
        state = observation(controls=None, capabilities=[])
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="Trade", role="button"),
            state,
        )
        assert not binding.bound
        assert "unavailable" in binding.reason


class TestContractCatalog:
    def test_contracts_are_registered_by_kind(self) -> None:
        assert set(ACTION_CONTRACTS) == {
            "approach_dialogue_target",
            "activate_visible_control",
            "dismiss_screen",
            "purchase_item",
            "use_game_binding",
            "scroll_screen",
            "sell_item",
            "equip_item",
        }
        assert contract_for(ApproachDialogueTargetAction(target_id=VENDOR_ID)) is (
            APPROACH_DIALOGUE_TARGET_CONTRACT
        )
        assert contract_for(ClickAction(x=0.5, y=0.5)) is None

    def test_approach_is_withheld_from_interface_only(self) -> None:
        visible = planner_visible_contracts(
            control_mode=ControlMode.INTERFACE_ONLY,
            capabilities=set(APPROACH_CAPABILITIES) | {"ui.visible_controls"},
        )
        kinds = [contract.kind for contract in visible]
        assert "approach_dialogue_target" not in kinds
        assert "activate_visible_control" in kinds

    def test_missing_capability_withholds_an_action(self) -> None:
        visible = planner_visible_contracts(
            control_mode=ControlMode.NATIVE_ASSISTED,
            capabilities={"ui.visible_controls"},
        )
        kinds = [contract.kind for contract in visible]
        assert "activate_visible_control" in kinds
        # Purchase needs the tooltip capability, which is absent here.
        assert "purchase_item" not in kinds

    def test_legacy_capability_alias_still_satisfies_the_contract(self) -> None:
        """The installed plug-in emits the vendor-named capability."""

        assert not APPROACH_DIALOGUE_TARGET_CONTRACT.missing_capabilities(
            set(APPROACH_CAPABILITIES)
        )

    def test_generic_capability_name_also_satisfies_the_contract(self) -> None:
        capabilities = {
            "control.approach_dialogue_target",
            "identity.stable_handles",
            "nearby.characters",
            "nearby.roles",
        }
        assert not APPROACH_DIALOGUE_TARGET_CONTRACT.missing_capabilities(capabilities)


class TestLegacyCompatibilityAdapter:
    def test_translates_the_vendor_macro_and_counts_it(self) -> None:
        ledger = LegacyCompatibilityLedger()
        action = translate_legacy_skill(
            SkillAction(
                name="approach_confirmed_vendor",
                args=[SkillArgument(name="target_id", value=VENDOR_ID)],
            ),
            ledger=ledger,
        )
        assert isinstance(action, ApproachDialogueTargetAction)
        assert action.target_id == VENDOR_ID
        assert ledger.summary() == {"approach_confirmed_vendor": 1}

    def test_translates_the_calibrated_dialogue_macro(self) -> None:
        ledger = LegacyCompatibilityLedger()
        action = translate_legacy_skill(SkillAction(name="choose_show_goods"), ledger=ledger)
        assert isinstance(action, ActivateVisibleControlAction)
        assert action.exact_label == "Show me your goods."
        assert ledger.total == 1

    def test_untranslatable_macro_is_left_alone(self) -> None:
        ledger = LegacyCompatibilityLedger()
        assert translate_legacy_skill(SkillAction(name="eat_food"), ledger=ledger) is None
        assert ledger.total == 0


class TestSemanticActionsAreAdvertised:
    def test_digest_reports_available_actions_and_argument_sources(self) -> None:
        state = observation(
            entities=[vendor()],
            controls=[VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5))],
            capabilities=[*APPROACH_CAPABILITIES, "ui.visible_controls"],
        )
        digest = state.semantic_action_digest()
        kinds = {entry["kind"] for entry in digest}
        assert {"approach_dialogue_target", "activate_visible_control", "dismiss_screen"} <= kinds
        assert all(entry["argument_source"] for entry in digest)

    def test_visible_control_digest_marks_ambiguity(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5)),
                VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.7)),
                VisibleUIControl(label="Leave", role="button", bounds=_bounds(0.9)),
            ],
            capabilities=["ui.visible_controls"],
        )
        digest = {
            entry["exact_label"]: entry["ambiguous"]
            for entry in state.visible_control_digest()
        }
        assert digest == {"Trade": True, "Leave": False}

    def test_digest_is_empty_without_the_capability(self) -> None:
        state = observation(
            controls=[VisibleUIControl(label="Trade", role="button", bounds=_bounds(0.5))],
            capabilities=[],
        )
        assert state.visible_control_digest() == []


class TestLegacyPlanTranslation:
    def test_a_legacy_macro_plan_enters_as_semantic_actions(self) -> None:
        from kenshi_agent.action_contracts import translate_legacy_plan_actions
        from kenshi_agent.models import (
            Condition,
            ConditionKind,
            ConditionOperator,
            ConditionPath,
            PlanEnvelope,
            PlanStep,
            RiskBudget,
        )

        fresh = Condition(
            kind=ConditionKind.TELEMETRY_FRESH,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=3.0,
        )
        dialogue = Condition(
            kind=ConditionKind.FIELD,
            path=ConditionPath.TELEMETRY_UI_DIALOGUE_OPEN,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=3.0,
        )
        legacy = PlanEnvelope(
            schema_version="1.0",
            plan_id="legacy-food",
            objective="Legacy calibrated chain.",
            control_mode=ControlMode.NATIVE_ASSISTED,
            based_on_revision=WorldStateRevision(telemetry_sequence=5, capability_epoch=1),
            assumptions=[fresh],
            steps=[
                PlanStep(
                    step_id="approach",
                    action=SkillAction(
                        name="approach_confirmed_vendor",
                        args=[SkillArgument(name="target_id", value=VENDOR_ID)],
                    ),
                    preconditions=[fresh],
                    success_conditions=[dialogue],
                    timeout_seconds=30.0,
                    on_success="goods",
                ),
                PlanStep(
                    step_id="goods",
                    action=SkillAction(name="choose_show_goods"),
                    preconditions=[fresh],
                    success_conditions=[dialogue],
                    timeout_seconds=30.0,
                    on_success="unrelated",
                ),
                PlanStep(
                    step_id="unrelated",
                    action=SkillAction(name="eat_food"),
                    preconditions=[fresh],
                    success_conditions=[dialogue],
                    timeout_seconds=30.0,
                ),
            ],
            entry_step_id="approach",
            max_actions=4,
            max_wall_seconds=60.0,
            max_game_seconds=120.0,
            risk_budget=RiskBudget(
                max_pointer_actions=2,
                max_purchase_actions=0,
                max_native_assisted_actions=1,
            ),
        )

        ledger = LegacyCompatibilityLedger()
        translated, counts = translate_legacy_plan_actions(legacy, ledger=ledger)

        kinds = [step.action.kind for step in translated.steps]
        assert kinds == ["approach_dialogue_target", "activate_visible_control", "skill"]
        assert counts == {"approach_confirmed_vendor": 1, "choose_show_goods": 1}
        # An untranslatable macro is left exactly as it was.
        assert translated.steps[2].action == legacy.steps[2].action
        assert ledger.total == 2

    def test_a_plan_with_no_legacy_macros_is_returned_unchanged(self) -> None:
        from kenshi_agent.action_contracts import translate_legacy_plan_actions
        from kenshi_agent.models import (
            Condition,
            ConditionKind,
            ConditionOperator,
            ConditionPath,
            PlanEnvelope,
            PlanStep,
            RiskBudget,
        )

        fresh = Condition(
            kind=ConditionKind.TELEMETRY_FRESH,
            operator=ConditionOperator.EQUALS,
            expected=True,
            max_age_seconds=3.0,
        )
        plan = PlanEnvelope(
            schema_version="1.0",
            plan_id="already-generic",
            objective="Already reusable.",
            control_mode=ControlMode.NATIVE_ASSISTED,
            based_on_revision=WorldStateRevision(telemetry_sequence=5, capability_epoch=1),
            assumptions=[fresh],
            steps=[
                PlanStep(
                    step_id="approach",
                    action=ApproachDialogueTargetAction(target_id=VENDOR_ID),
                    preconditions=[fresh],
                    success_conditions=[
                        Condition(
                            kind=ConditionKind.FIELD,
                            path=ConditionPath.TELEMETRY_UI_DIALOGUE_OPEN,
                            operator=ConditionOperator.EQUALS,
                            expected=True,
                            max_age_seconds=3.0,
                        )
                    ],
                    timeout_seconds=30.0,
                )
            ],
            entry_step_id="approach",
            max_actions=2,
            max_wall_seconds=60.0,
            max_game_seconds=120.0,
            risk_budget=RiskBudget(
                max_pointer_actions=0,
                max_purchase_actions=0,
                max_native_assisted_actions=1,
            ),
        )
        translated, counts = translate_legacy_plan_actions(plan, ledger=LegacyCompatibilityLedger())
        assert translated is plan
        assert counts == {}


class TestItemCellControls:
    """Shop and inventory grid cells bind like any other advertised control."""

    def test_an_item_cell_binds_by_ordinal_and_role(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="item_0", role="item", bounds=_bounds(0.5)),
                VisibleUIControl(label="item_1", role="item", bounds=_bounds(0.6)),
                VisibleUIControl(label="ARRANGE", role="button", bounds=_bounds(0.9)),
            ],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="item_1", role="item"),
            state,
        )
        assert binding.bound
        assert binding.resolved_role == "item"
        assert binding.resolved_bounds == _bounds(0.6)

    def test_an_item_ordinal_does_not_match_a_button(self) -> None:
        state = observation(
            controls=[VisibleUIControl(label="item_0", role="item", bounds=_bounds(0.5))],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="item_0", role="button"),
            state,
        )
        assert not binding.bound

    def test_absent_cell_fails_closed(self) -> None:
        state = observation(
            controls=[VisibleUIControl(label="item_0", role="item", bounds=_bounds(0.5))],
            capabilities=["ui.visible_controls"],
        )
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="item_9", role="item"),
            state,
        )
        assert not binding.bound

    def test_item_cells_appear_in_the_digest_with_ambiguity_marked(self) -> None:
        state = observation(
            controls=[
                VisibleUIControl(label="item_0", role="item", bounds=_bounds(0.5)),
                VisibleUIControl(label="item_1", role="item", bounds=_bounds(0.6)),
            ],
            capabilities=["ui.visible_controls"],
        )
        digest = state.visible_control_digest()
        assert {e["exact_label"] for e in digest} == {"item_0", "item_1"}
        assert all(e["role"] == "item" for e in digest)
        assert not any(e["ambiguous"] for e in digest)


class TestPurchaseSafety:
    """The purchase fence, lifted out of the calibrated food policy.

    The legacy version took model-authored x/y and merely checked they landed
    inside the tooltip's source. Here the cell is the reference and the tooltip
    must belong to *it*, so "buy what I am looking at" is proven rather than
    asserted.
    """

    SELLER = "entity-barman"

    def _state(
        self,
        *,
        tooltip: str | None = "Dried Meat\n[Food]\nValue c.52",
        tooltip_visible: bool = True,
        tooltip_over_cell: bool = True,
        traders: int = 1,
        shop_owner: bool = True,
    ) -> Observation:
        # A real trade screen shows both inventories, and the cell ordinals run
        # across both, so the window is the only thing saying whose item it is.
        cell = VisibleUIControl(
            label="item_3", role="item", window="BARMAN", bounds=_bounds(0.5)
        )
        # Ordinals come from one counter spanning every window, so they are
        # unique across both inventories.
        # Carries its own facts, so binding reaches the ownership check rather
        # than stopping at the tooltip.
        our_cell = VisibleUIControl(
            label="item_7",
            role="item",
            window="HEP",
            bounds=_bounds(0.2),
            item_name="Dried Meat",
            item_value=52,
        )
        # The tooltip's source is the cell itself unless told otherwise.
        source = _bounds(0.5) if tooltip_over_cell else _bounds(0.8)
        seller = NearbyEntity(
            id=self.SELLER,
            name="Barman",
            is_animal=False,
            has_dialogue=True,
            has_vendor_list=True,
            is_squad_leader=True,
            shop_inventory_owner=shop_owner,
            disposition=Disposition.NEUTRAL,
            distance=3.0,
            conscious=True,
        )
        state = observation(
            entities=[seller],
            controls=[cell, our_cell],
            capabilities=["ui.visible_controls", "ui.tooltip", "nearby.shop_owners"],
        )
        telemetry = state.telemetry
        assert telemetry is not None
        return state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "active_shop_trader_count": traders,
                        "ui": telemetry.ui.model_copy(
                            update={
                                "active_screen": "trade",
                                "tooltip_visible": tooltip_visible,
                                "tooltip_text": tooltip,
                                "tooltip_source_bounds": source,
                            }
                        ),
                    }
                )
            },
            deep=True,
        )

    def _action(self, **overrides: object) -> PurchaseItemAction:
        fields: dict[str, object] = {
            "cell_label": "item_3",
            "item_name": "Dried Meat",
            "expected_price": 52,
            "window": "BARMAN",
            "seller_id": self.SELLER,
        }
        fields.update(overrides)
        return PurchaseItemAction(**fields)  # type: ignore[arg-type]

    def test_a_purchase_matching_its_own_tooltip_binds(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(), self._state())
        assert binding.bound, binding.reason
        assert binding.target_id == self.SELLER
        assert binding.resolved_bounds == _bounds(0.5)

    def test_a_tooltip_describing_another_widget_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(), self._state(tooltip_over_cell=False)
        )
        assert not binding.bound
        assert "does not belong to cell" in binding.reason

    def test_a_wrong_item_name_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(item_name="Ancient Katana"), self._state()
        )
        assert not binding.bound
        assert "does not name" in binding.reason

    def test_a_wrong_price_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(expected_price=5), self._state())
        assert not binding.bound
        assert "does not show price" in binding.reason

    def test_a_price_that_is_only_a_substring_is_refused(self) -> None:
        """c.52 must not satisfy a claim of c.5."""

        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(expected_price=5),
            self._state(tooltip="Dried Meat\n[Food]\nValue c.52"),
        )
        assert not binding.bound

    def test_no_visible_tooltip_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(), self._state(tooltip_visible=False)
        )
        assert not binding.bound
        assert "hover the cell first" in binding.reason

    def test_a_seller_who_is_not_the_active_shop_owner_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(), self._state(shop_owner=False))
        assert not binding.bound
        assert "verified non-hostile shop owner" in binding.reason

    def test_a_cell_outside_the_sellers_window_is_refused(self) -> None:
        """Ownership is the cell's window, not a count of traders in the world.

        `active_shop_trader_count` is a registry of shop traders loaded in the
        world - it reads 5 in a bar with nothing open - so gating on it being
        exactly 1 made this action unbindable everywhere, which is why the agent
        could open a shop and never buy. What proves the item is the shop's is
        that the cell sits in the shop's own inventory window.
        """

        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(cell_label="item_7", window="HEP"), self._state()
        )
        assert not binding.bound
        assert "not the seller's own inventory" in binding.reason

    def test_many_traders_in_the_world_do_not_block_a_purchase(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(), self._state(traders=5))
        assert binding.bound, binding.reason

    def test_the_window_caption_matches_the_seller_case_insensitively(self) -> None:
        """Kenshi captions the window "BARMAN" while the character is "Barman"."""

        binding = PURCHASE_ITEM_CONTRACT.bind(self._action(), self._state())
        assert binding.bound, binding.reason

    def test_an_absent_cell_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(cell_label="item_99"), self._state()
        )
        assert not binding.bound
        assert "No current item cell" in binding.reason

    def test_purchase_is_at_most_once_and_costs_a_purchase_budget(self) -> None:
        assert PURCHASE_ITEM_CONTRACT.idempotency is IdempotencyPolicy.AT_MOST_ONCE
        assert PURCHASE_ITEM_CONTRACT.risk.purchase_actions == 1

    def test_purchase_says_nothing_about_what_kind_of_item_is_worth_buying(self) -> None:
        """Task intent lives in config, not in the purchase contract."""

        binding = PURCHASE_ITEM_CONTRACT.bind(
            self._action(item_name="Ancient Katana", expected_price=865),
            self._state(tooltip="Ancient Katana\n[Weapon]\nValue c.865"),
        )
        assert binding.bound, binding.reason


class TestWindowAttribution:
    """Several open windows share labels; the owning window disambiguates."""

    def _two_windows(self) -> Observation:
        return observation(
            controls=[
                VisibleUIControl(label="ARRANGE", role="button", bounds=_bounds(0.5), window="HEP"),
                VisibleUIControl(
                    label="ARRANGE", role="button", bounds=_bounds(0.7), window="BARMAN"
                ),
            ],
            capabilities=["ui.visible_controls"],
        )

    def test_a_label_shared_by_two_windows_is_ambiguous(self) -> None:
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(exact_label="ARRANGE", role="button"),
            self._two_windows(),
        )
        assert not binding.bound
        assert "Name the window" in binding.reason

    def test_naming_the_window_resolves_it(self) -> None:
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(
                exact_label="ARRANGE", role="button", window="BARMAN"
            ),
            self._two_windows(),
        )
        assert binding.bound
        assert binding.resolved_bounds == _bounds(0.7)

    def test_naming_a_window_that_does_not_have_it_fails_closed(self) -> None:
        binding = ACTIVATE_VISIBLE_CONTROL_CONTRACT.bind(
            ActivateVisibleControlAction(
                exact_label="ARRANGE", role="button", window="NOBODY"
            ),
            self._two_windows(),
        )
        assert not binding.bound

    def test_open_window_captions_are_reported_in_order(self) -> None:
        assert self._two_windows().open_window_captions() == ["HEP", "BARMAN"]

    def test_the_digest_reports_the_owning_window(self) -> None:
        digest = self._two_windows().visible_control_digest()
        assert {entry["window"] for entry in digest} == {"HEP", "BARMAN"}
        # Same label, different windows: neither is ambiguous within its own.
        assert not any(entry["ambiguous"] for entry in digest)


class TestPurchaseUsesExportedCellFacts:
    """Buying something visible must be one step, not hover-then-replan.

    Requiring a tooltip made sense while cells were opaque ordinals. Once the
    cell carries the game's own name and price, demanding a tooltip forced a
    hover, a replan and a second model call before every purchase — the agent
    spent whole runs inspecting instead of buying.
    """

    def _trade_state(self) -> Observation:
        cell = VisibleUIControl(
            label="Bread",
            role="item",
            window="BARMAN",
            bounds=_bounds(0.5),
            item_name="Bread",
            item_value=52,
            item_quantity=3,
        )
        seller = NearbyEntity(
            id="entity-barman",
            name="Barman",
            is_animal=False,
            has_dialogue=True,
            shop_inventory_owner=True,
            disposition=Disposition.NEUTRAL,
            distance=3.0,
            conscious=True,
        )
        state = observation(
            entities=[seller],
            controls=[cell],
            capabilities=["ui.visible_controls", "ui.tooltip", "nearby.shop_owners"],
        )
        telemetry = state.telemetry
        assert telemetry is not None
        return state.model_copy(
            update={
                "telemetry": telemetry.model_copy(
                    update={
                        "active_shop_trader_count": 1,
                        "ui": telemetry.ui.model_copy(update={"active_screen": "trade"}),
                    }
                )
            },
            deep=True,
        )

    def test_a_named_cell_needs_no_tooltip(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            PurchaseItemAction(
                cell_label="Bread",
                item_name="Bread",
                expected_price=52,
                window="BARMAN",
            seller_id="entity-barman",
            ),
            self._trade_state(),
        )
        assert binding.bound, binding.reason

    def test_a_price_that_disagrees_with_the_cell_does_not_refuse(self) -> None:
        """`item_value` is the item's worth, not what this shop charges.

        A trader applies its own multiplier and the asking price is never
        exported, so an `expected_price` that disagrees means the planner was
        wrong about something it was never given - not that the wrong item is
        about to be bought. Live, this refused every attempt at a c.38 Dried
        Meat and reported that the cell had vanished.
        """

        binding = PURCHASE_ITEM_CONTRACT.bind(
            PurchaseItemAction(
                cell_label="Bread",
                item_name="Bread",
                expected_price=5,
                window="BARMAN",
                seller_id="entity-barman",
            ),
            self._trade_state(),
        )
        assert binding.bound, binding.reason
        assert binding.resolved_label == "Bread"

    def test_a_name_that_disagrees_with_the_cell_is_refused(self) -> None:
        binding = PURCHASE_ITEM_CONTRACT.bind(
            PurchaseItemAction(
                cell_label="Bread",
                item_name="Ancient Katana",
                expected_price=52,
                window="BARMAN",
            seller_id="entity-barman",
            ),
            self._trade_state(),
        )
        assert not binding.bound
        assert "holds 'Bread'" in binding.reason
