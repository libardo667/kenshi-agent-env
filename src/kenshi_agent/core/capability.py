"""Small immutable descriptors exported by semantic capability owners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CapabilityKind = Literal[
    "sensing", "representation", "memory", "action", "verification", "recovery"
]


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """The semantic identity of one projected capability.

    Owners construct this value beside the code that owns the semantics.  The
    projection may serialize it, but cannot mutate an owner's descriptor or
    accidentally turn it into a second registry.
    """

    name: str
    purpose: str
    kind: CapabilityKind
    owner_component: str
    implementation_ref: str
    semantic_effects: tuple[str, ...] = ()
    proof_key: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "name", "purpose", "owner_component", "implementation_ref"
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"capability descriptor {field_name} must be nonempty")
        if self.proof_key is not None and (
            not isinstance(self.proof_key, str) or not self.proof_key.strip()
        ):
            raise ValueError("capability descriptor proof_key must be nonempty when present")
        if not self.semantic_effects or any(
            not isinstance(effect, str) or not effect.strip()
            for effect in self.semantic_effects
        ):
            raise ValueError(
                "capability descriptor semantic_effects must contain at least one "
                "nonblank effect"
            )
        if self.kind not in {
            "sensing", "representation", "memory", "action", "verification", "recovery"
        }:
            raise ValueError(f"unknown capability descriptor kind: {self.kind!r}")

    def as_projection(self) -> dict[str, Any]:
        values = {
            "name": self.name,
            "purpose": self.purpose,
            "kind": self.kind,
            "owner_component": self.owner_component,
            "implementation_ref": self.implementation_ref,
            "semantic_effects": list(self.semantic_effects),
        }
        if self.proof_key is not None:
            values["proof_key"] = self.proof_key
        return values
