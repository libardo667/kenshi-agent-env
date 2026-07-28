"""Which save's memories these are.

A config profile name is not a campaign identity and neither is a character's
display name. Both were the scope before this module existed, which meant two
unrelated saves opened under `live.longform.yaml` shared one memory, and a
fixture run could inherit a real playthrough's beliefs.

Scope is therefore explicit or explicitly ephemeral, never implicitly global.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .config import MemoryConfig
from .models import ScenarioIdentity

CAMPAIGN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,79}$"
LEGACY_CAMPAIGN_PREFIX = "legacy:"


class CampaignScopeError(ValueError):
    """Durable continuity was asked for without saying whose it is."""


class CampaignScopeOrigin(StrEnum):
    CONFIGURED = "configured"
    EPHEMERAL = "ephemeral"
    SCENARIO = "scenario"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True)
class CampaignScope:
    campaign_id: str
    origin: CampaignScopeOrigin


def resolve_campaign_scope(
    config: MemoryConfig,
    *,
    mode: str,
    run_id: str,
    scenario: ScenarioIdentity | None,
) -> CampaignScope:
    """Answer whose memories a run may read and write, or refuse to guess.

    Ordering matters. An explicit campaign wins everywhere, because that is the
    operator saying so. An explicit ephemeral request is the other way of
    saying so. An attested scenario is a deterministic identity derived from the
    exact save, so repeat runs of one fixture accumulate while a different save
    stays separate. Everything left over is run-scoped: a mock or replay run
    that never declared a campaign gets its own throwaway one, and a live run
    that never declared a campaign is refused outright — the only case where
    guessing would silently mix a real playthrough with something else.
    """

    if config.campaign_id is not None and config.ephemeral:
        raise CampaignScopeError(  # mutation: reason
            "memory.campaign_id and memory.ephemeral are "  # mutation: reason
            "mutually exclusive: an explicit campaign cannot also "  # mutation: reason
            "be thrown away at the end of the run."  # mutation: reason
        )
    if config.campaign_id is not None:
        return CampaignScope(
            campaign_id=config.campaign_id,
            origin=CampaignScopeOrigin.CONFIGURED,
        )
    if config.ephemeral:
        return _ephemeral(run_id)
    if scenario is not None:
        return CampaignScope(
            campaign_id=f"scenario:{scenario.scenario_id}:{scenario.save_id}",
            origin=CampaignScopeOrigin.SCENARIO,
        )
    if mode == "live":
        raise CampaignScopeError(  # mutation: reason
            "Durable memory is enabled for a live run with no "  # mutation: reason
            "campaign identity. Set memory.campaign_id to the save "  # mutation: reason
            "lineage this run belongs to, or set memory.ephemeral: "  # mutation: reason
            "true to keep this run's memories to itself."  # mutation: reason
        )
    return _ephemeral(run_id)


def _ephemeral(run_id: str) -> CampaignScope:
    return CampaignScope(
        campaign_id=f"run:{run_id}",
        origin=CampaignScopeOrigin.EPHEMERAL,
    )


def legacy_campaign_id(namespace: str) -> str:
    """The campaign a pre-campaign namespace's rows keep.

    Deliberately not whichever campaign happens to open the file first: those
    rows were written under a profile name, and the honest thing to say is which
    profile, not which save.
    """

    return f"{LEGACY_CAMPAIGN_PREFIX}{namespace}"
