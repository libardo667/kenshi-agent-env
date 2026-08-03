"""Make one pydantic schema acceptable to every structured-output provider.

Providers disagree about which JSON Schema keywords they accept, and they
disagree by rejecting the whole request:

- Google rejects ``const``, so a pydantic discriminator like
  ``{"const": "purchase_item"}`` reads to it as an unspecified property.
- Anthropic (and its Bedrock and Azure mirrors) reject ``minimum`` and
  ``maximum`` on integers.

Both refusals are about how a constraint is *spelled*, not about what it means,
so the fix is to spell the schema in the subset everyone accepts. Constraints
that no longer fit in the schema are moved into the field description, so the
model is still told the bound even though the provider no longer enforces it.
Nothing is lost on our side: the response is validated against the real pydantic
model afterwards either way.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import BaseModel

# Keywords a provider may reject outright. Each maps to a phrasing used to carry
# the constraint over into the description, so dropping it costs the model
# guidance but never silently loosens what we accept back.
_BOUND_PHRASING: dict[str, str] = {
    "minimum": "at least {}",
    "maximum": "at most {}",
    "exclusiveMinimum": "greater than {}",
    "exclusiveMaximum": "less than {}",
    "multipleOf": "a multiple of {}",
    "minLength": "at least {} characters",
    "maxLength": "at most {} characters",
    "minItems": "at least {} items",
    "maxItems": "at most {} items",
    "pattern": "matching the pattern {}",
}

# Dropped without a note: they constrain nothing the model needs to be told.
# `default` is meaningless under strict mode, where every property is required
# anyway, and `title` is not part of Google's schema type at all - it carries no
# meaning beyond the field name, which the model already sees.
_DROPPED_SILENTLY = frozenset(
    {
        "default",
        "examples",
        "format",
        "uniqueItems",
        "title",
        "$comment",
        "readOnly",
        "writeOnly",
    }
)


def _sanitize(node: Any) -> Any:
    if isinstance(node, list):
        return [_sanitize(item) for item in node]
    if not isinstance(node, dict):
        return node

    result: dict[str, Any] = {}
    notes: list[str] = []
    for key, value in node.items():
        if key in _DROPPED_SILENTLY:
            continue
        if key in _BOUND_PHRASING:
            notes.append(_BOUND_PHRASING[key].format(value))
            continue
        if key == "const":
            # The one spelling of a fixed value that every provider accepts.
            value = [value]
            key = "enum"
        if key == "enum" and any(not isinstance(item, str) for item in value):
            # Google only allows `enum` on strings, and an enum it cannot read
            # invalidates the whole object around it - which is how a bad
            # integer enum turns into a complaint about a sibling property.
            notes.append("one of: " + ", ".join(str(item) for item in value))
            continue
        result[key] = _sanitize(value)

    if notes:
        description = result.get("description")
        carried = "Must be " + ", ".join(notes) + "."
        result["description"] = f"{description} {carried}".strip() if description else carried
    return result


@lru_cache(maxsize=8)
def portable_response_format(model: type[BaseModel]) -> dict[str, Any]:
    """Build a ``response_format`` for ``model`` that any provider will accept."""
    try:
        from openai.lib._pydantic import to_strict_json_schema
    except ImportError as exc:  # pragma: no cover - depends on the installed openai
        raise RuntimeError(
            "The installed openai package no longer exposes "
            "openai.lib._pydantic.to_strict_json_schema, which the planner uses to "
            "build the same strict schema the SDK would send."
        ) from exc

    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "strict": True,
            "schema": _sanitize(to_strict_json_schema(model)),
        },
    }


projected_response_format = portable_response_format
