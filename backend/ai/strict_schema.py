"""Convert a Pydantic model into a schema accepted by Groq strict mode.

Groq's `strict: true` (constrained decoding) requires that every object lists
ALL its properties in `required` and sets `additionalProperties: false`;
optional values must be expressed as a union with null. Pydantic's default
output does neither, so we post-process it.

We also drop keywords outside the documented subset (types, enum, anyOf,
$defs/$ref). Value constraints (ge/le/max_length...) are therefore NOT enforced
by the decoder; they are still enforced by Pydantic when we validate the
response, which is the check BizLens actually relies on.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_DROP_KEYS = {
    "title",
    "default",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "uniqueItems",
}
# Keys whose values are {name: subschema} maps; the *names* there are user
# field names (a field called "title" must survive).
_NAMED_SCHEMA_MAPS = {"properties", "$defs"}


class StrictSchemaError(ValueError):
    """The model cannot be expressed as a strict-mode schema."""


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    return _walk(model.model_json_schema(), path=model.__name__)


def _walk(node: Any, path: str) -> Any:
    if isinstance(node, list):
        return [_walk(item, path) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP_KEYS:
            continue
        if key in _NAMED_SCHEMA_MAPS:
            out[key] = {name: _walk(sub, f"{path}.{name}") for name, sub in value.items()}
        else:
            out[key] = _walk(value, path)

    if "const" in out:  # Literal["x"] -> const; strict mode documents `enum`, so use that
        out["enum"] = [out.pop("const")]

    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties")
        if not props:
            raise StrictSchemaError(
                f"{path}: open-ended objects (e.g. dict[str, Any]) are not allowed in "
                "strict mode. Model it as a list of {key, value} items or a named model."
            )
        out["required"] = list(props.keys())
        out["additionalProperties"] = False
    return out
