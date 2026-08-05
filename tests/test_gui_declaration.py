"""Kenshi's shipped GUI declaration, and what the agent perceives through it."""

from __future__ import annotations

from kenshi_agent.core.gui_declaration import load_gui_vocabulary
from kenshi_agent.core.gui_resolution import (
    declared_siblings,
    expectation_for,
    resolve_control,
)
from kenshi_agent.tooling.gui_coverage import assess_gui_coverage, render_gui_coverage


def test_the_declaration_carries_structure_not_just_names() -> None:
    """The flat capture could not answer "what sits beside this".

    That gap is why locating the prospecting reading was done by collecting
    every caption under a line and guessing, when the layout named the widget.
    """

    vocabulary = load_gui_vocabulary()

    assert len(vocabulary.layouts) == 52
    assert vocabulary.named_widget_count > 600

    line = vocabulary.layout("Kenshi_ProspectingWindowResourceLine.layout")
    assert line is not None
    siblings = line.siblings_of("CheckboxButton")
    assert [widget.name for widget in siblings] == ["ValueText"]
    assert siblings[0].widget_type == "TextBox"


def test_declared_siblings_answers_the_question_that_was_guessed() -> None:
    siblings = declared_siblings(
        "Kenshi_ProspectingWindowResourceLine.layout", "CheckboxButton"
    )

    assert [widget.name for widget in siblings] == ["ValueText"]


def test_a_name_in_many_layouts_resolves_ambiguously_without_inventing_detail() -> None:
    """`Root` is declared in nearly every layout.

    Taking the first match reported the Prospecting window's caption as
    'Fog Volumes'. Ambiguity is reported and the details withheld rather than
    an arbitrary layout's values being presented as this widget's.
    """

    resolved = resolve_control("Root")

    assert resolved.resolved
    assert resolved.ambiguous
    assert resolved.provenance == "ambiguous"
    assert resolved.declared_caption == ""
    assert len(resolved.layouts) > 10


def test_a_uniquely_declared_widget_resolves_completely() -> None:
    resolved = resolve_control("ShortcutInventoryButton")

    assert resolved.provenance == "declared"
    assert not resolved.ambiguous
    assert resolved.layouts == ("Kenshi_MainPanel.layout",)
    assert resolved.widget_type == "Button"


def test_an_undeclared_widget_is_named_as_such_rather_than_forced() -> None:
    """Mod windows and code-built widgets exist in no layout.

    Observed live: RE_Kenshi contributes DebugWindow, GameSpeedTutorialWindow,
    and BugReportWindow. Reporting them as undeclared is the correct answer,
    not a failure to match.
    """

    resolved = resolve_control("BugReportWindow")

    assert not resolved.resolved
    assert resolved.provenance == "undeclared"
    assert resolved.layouts == ()


def test_expectation_separates_absent_from_unknown() -> None:
    """A window's declaration says what should be there to look for."""

    expectation = expectation_for(
        "Kenshi_ProspectingWindow.layout",
        observed_names=("Map", "ResourcesList", "lbOverview"),
    )

    assert "lbResources" in expectation.missing
    assert "Map" not in expectation.missing
    assert 0.0 < expectation.coverage < 1.0


def test_expectation_for_an_unknown_layout_claims_nothing() -> None:
    expectation = expectation_for("NotALayout.layout", observed_names=("Anything",))

    assert expectation.declared == ()
    assert expectation.missing == ()
    assert expectation.coverage == 0.0


def test_coverage_reports_the_declared_interface_as_the_denominator() -> None:
    coverage = assess_gui_coverage()

    assert coverage.total_layouts == 52
    assert coverage.total_actionable > 0
    # Every layout is accounted for, modeled or not.
    assert len(coverage.layouts) == coverage.total_layouts

    body = "\n".join(render_gui_coverage(coverage))
    assert "DECLARED INTERFACE" in body
    assert "Kenshi_ProspectingWindow.layout" in body
