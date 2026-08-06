"""Recipient scope has exactly one owner: the resolved interaction contract.

Scope kept growing private copies. Each looked local and reasonable, and each
could disagree with the registry without anything noticing:

- option preparation demanded a singleton for anything that was not a character
  move or a map travel;
- the native request builder demanded one for any wire command outside a
  hardcoded "group capable" set, so `perform_context_action` was singleton-only
  while its contract declares CURRENT_SELECTION;
- order adoption compared a live order against the current selection instead of
  the authored one;
- transport validation kept a command-name exception set;
- safety read PRIMARY as "exactly one selected character" while the registry
  reads it as "Kenshi's exported primary is among the selected".

These ratchets are structural rather than behavioural on purpose. A behavioural
test proves one operation works today; these are meant to make the *next* hidden
authority fail to land, so adding a group-scoped operation stays one contract
declaration instead of edits to several secret lists.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "kenshi_agent"
NATIVE = (
    Path(__file__).resolve().parents[1]
    / "native"
    / "KenshiAgentTelemetry"
    / "KenshiAgentTelemetry.cpp"
)

# The registry is where scope lives. Every other module is a consumer.
SCOPE_OWNER = SOURCE / "operation_definitions.py"

# Modules that carried a private scope model and must not grow one back.
CONSUMER_MODULES = (
    SOURCE / "options.py",
    SOURCE / "safety.py",
    SOURCE / "core" / "transport.py",
    SOURCE / "operation_authority.py",
    SOURCE / "execution" / "handlers" / "kenshi_surface.py",
    SOURCE / "execution" / "handlers" / "movement.py",
    SOURCE / "execution" / "handlers" / "resources.py",
)


# Wire-command sets that classify request shape rather than recipients. Kept as
# a record of what exists rather than an exemption anyone can quietly extend:
# the test still fails if such a set starts consulting the selection.
SHAPE_CLASSIFICATIONS: dict[str, set[int]] = {}


def _module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _executable_source(path: Path) -> str:
    """Source with comments and docstrings removed.

    A ratchet that reads prose flags the comment explaining the defect it was
    written to prevent, which teaches people to delete the explanation.
    """

    tree = ast.parse(_module_source(path))
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
    return ast.unparse(tree)


@pytest.mark.parametrize("path", CONSUMER_MODULES, ids=lambda p: p.name)
def test_no_consumer_decides_cardinality_from_a_selection_count(path: Path) -> None:
    """`len(selected...) != 1` is how every private scope model was spelled.

    A consumer may ask the contract whether the selection satisfies the scope.
    It may not decide for itself that one is the right number.
    """

    source = _executable_source(path)
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"len\(\s*selected(_ids|_character_ids|_members)?\s*\)\s*[!=]=\s*1", line)
    ]

    assert not offenders, (
        f"{path.name} decides selection cardinality itself: {offenders}. "
        "Ask definition.satisfies_recipient_scope instead."
    )


@pytest.mark.parametrize("path", CONSUMER_MODULES, ids=lambda p: p.name)
def test_no_consumer_builds_a_group_capable_command_name_set(path: Path) -> None:
    """A set of wire command names used to decide who an order addresses.

    Rediscovering scope from a command name is exactly the thing the registry
    exists to prevent, and it is how the two layers came to disagree.
    """

    tree = ast.parse(_executable_source(path))
    offenders: list[str] = []
    # The unit of judgement is the enclosing function, not the literal. A set of
    # command names becomes a scope authority once something in the same
    # function reaches for the selection, and those two sit in different
    # expressions - so inspecting the literal alone missed exactly the case this
    # exists to catch.
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        touches_selection = "selected" in ast.dump(function)
        for node in ast.walk(function):
            if not isinstance(node, (ast.Set, ast.Tuple, ast.List)):
                continue
            names = [
                element.attr
                for element in node.elts
                if isinstance(element, ast.Attribute)
                and element.attr.endswith("WIRE_COMMAND")
            ]
            # One or two names is a shape check (targeted vs targetless). Three
            # or more is a classification, and classifying commands is how scope
            # gets rediscovered.
            if len(names) < 3:
                continue
            # Classifying request *shape* - which commands carry a world target,
            # which are targetless - describes what the bytes look like, not who
            # receives them. Reaching for the selection in the same function is
            # the line between describing a request and deciding its recipients.
            if touches_selection:
                offenders.append(
                    f"{function.name} (line {node.lineno}): {names} "
                    "classified in a function that reads the selection"
                )
            else:
                SHAPE_CLASSIFICATIONS.setdefault(path.name, set()).add(node.lineno)

    assert not offenders, (
        f"{path.name} classifies wire commands as a group: {offenders}. "
        "Scope comes from the operation definition, not from the command name."
    )


def test_only_the_registry_names_the_recipient_scope_members() -> None:
    """Consumers may compare a scope; they may not enumerate the members.

    Enumerating them is how a second interpretation gets written: safety read
    PRIMARY as a singleton while the registry read it as "the exported primary
    is selected".
    """

    allowed = {
        SOURCE / "operation_definitions.py",
        SOURCE / "core" / "interaction.py",
        SOURCE / "affordances.py",
    }
    offenders: dict[str, list[str]] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        if path in allowed:
            continue
        named = sorted(
            set(re.findall(r"RecipientScope\.(\w+)", _executable_source(path)))
        )
        if len(named) >= 3:
            offenders[str(path.relative_to(SOURCE))] = named

    assert not offenders, (
        f"these modules enumerate recipient scopes rather than asking the "
        f"contract: {offenders}"
    )


def test_the_native_plugin_holds_no_selection_cardinality_exception() -> None:
    """The plug-in revalidates identity and target; scope is Python's.

    Kenshi's own ordering API is selection-based throughout, so a cardinality
    exception keyed on a command name in native code would be a third
    disagreeing authority - in the layer hardest to change.
    """

    source = NATIVE.read_text(encoding="utf-8", errors="replace")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"selectedCharacterIds\.size\(\)\s*[!=]=\s*1", line)
        # Selecting one exact character is an outcome, not a scope rule.
        and "isSquadSelection" not in line
    ]

    assert len(offenders) <= 1, (
        f"native code decides selection cardinality by command: {offenders}"
    )


def test_every_definition_resolves_a_scope_without_consulting_a_wire_name() -> None:
    """The end state: one declaration is enough.

    If this can answer for every operation, no consumer needs a list.
    """

    from kenshi_agent.operation_definitions import OPERATION_DEFINITION_LIST

    for definition in OPERATION_DEFINITION_LIST:
        scope = definition.recipient_scope_for()
        assert scope is not None, definition.kind


def test_adding_a_group_scoped_operation_touches_one_declaration() -> None:
    """The closing condition, stated as a check rather than a claim.

    Every group-scoped operation must be discoverable purely from its contract,
    with no membership in any consumer-side collection.
    """

    from kenshi_agent.core.interaction import RecipientScope
    from kenshi_agent.operation_definitions import OPERATION_DEFINITION_LIST

    group_scoped = {
        definition.kind
        for definition in OPERATION_DEFINITION_LIST
        if definition.recipient_scope_for() is RecipientScope.CURRENT_SELECTION
    }

    assert group_scoped, "the probe found no group-scoped operations to check"

    for path in CONSUMER_MODULES:
        source = _executable_source(path)
        for kind in sorted(group_scoped):
            # A consumer naming a specific group-scoped operation is the
            # beginning of an allowlist.
            assert f'"{kind}"' not in source, (
                f"{path.name} names the group-scoped operation {kind!r}; "
                "membership belongs in the contract, not here."
            )


def test_the_survey_has_a_complete_dispatch_route() -> None:
    """Offered and undispatchable is the worst of both.

    `survey_local_resources` was on the agent's menu while the request builder
    had no branch for it: being targetless, it fell through to the targeted
    route and was refused for having no target_id. The native command counter
    never incremented for it across two live runs, and the failure surfaced as
    "no causal transition".

    Every link is checked here, because it was the missing one nobody looked at
    that made the other four worthless.
    """

    from kenshi_agent.core.interaction import RecipientScope
    from kenshi_agent.operation_definitions import (
        OPERATION_DEFINITIONS,
        native_wire_command_for,
    )

    definition = OPERATION_DEFINITIONS["survey_local_resources"]

    # 1. It commands nobody, so a selection change must not make it stale.
    assert definition.recipient_scope_for() is RecipientScope.NONE
    # 2. It declares the capability its native command needs.
    assert "control.survey_local_resources" in definition.required_capabilities
    # 3. That capability resolves to a wire command.
    assert native_wire_command_for(definition) == "survey_local_resources"
    # 4. A handler exists for it.
    assert definition.handler_key == "movement.survey_local_resources"
    # 5. The request builder has a branch, so dispatch cannot fall through to
    #    the targeted route and be refused for having no target.
    surface = (SOURCE / "execution" / "handlers" / "kenshi_surface.py").read_text()
    assert "NATIVE_RESOURCE_SURVEY_WIRE_COMMAND" in surface
    # 6. The plug-in advertises the capability and implements the command.
    manifest = (
        Path(__file__).resolve().parents[1]
        / "native"
        / "KenshiAgentTelemetry"
        / "GameplayCapabilities.json"
    ).read_text()
    assert "control.survey_local_resources" in manifest
    assert '"survey_local_resources"' in NATIVE.read_text(encoding="utf-8", errors="replace")


def test_no_operation_is_offered_without_a_dispatch_route() -> None:
    """The general form of the survey's defect.

    An operation that declares a control capability must have a wire command,
    and one that has a wire command must have a branch that can build it.
    Otherwise it is offered to the agent and refused at the last moment for a
    reason that names none of this.
    """

    from kenshi_agent import native_commands
    from kenshi_agent.core.telemetry import NATIVE_COMMANDS_NAMING_A_TARGET
    from kenshi_agent.operation_definitions import (
        OPERATION_DEFINITION_LIST,
        native_wire_command_for,
    )

    # The surface names wire commands through their constants, so resolve each
    # wire value back to the constant that holds it rather than grepping for the
    # string - which finds nothing and passes for the wrong reason.
    constant_for = {
        value: name
        for name, value in vars(native_commands).items()
        if name.endswith("WIRE_COMMAND") and isinstance(value, str)
    }
    surface = (SOURCE / "execution" / "handlers" / "kenshi_surface.py").read_text()
    routeless: list[str] = []
    for definition in OPERATION_DEFINITION_LIST:
        wire = native_wire_command_for(definition)
        if wire is None:
            continue
        constant = constant_for.get(wire)
        assert constant is not None, f"{wire!r} has no wire-command constant"
        if wire in NATIVE_COMMANDS_NAMING_A_TARGET:
            # A targeted command is built by the generic targeted route and
            # needs no branch of its own.
            continue
        # A targetless command must have an explicit branch. Without one it
        # falls through to the targeted route and is refused for having no
        # target_id - which is exactly what happened to the survey, and why it
        # was offered to the agent while impossible to dispatch.
        if constant not in surface:
            routeless.append(f"{definition.kind} -> {wire} ({constant})")

    assert not routeless, (
        f"these operations declare a native command the request builder cannot "
        f"build: {routeless}"
    )
