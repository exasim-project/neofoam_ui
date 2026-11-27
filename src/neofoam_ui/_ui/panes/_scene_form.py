"""A node's property form, generated from its pydantic model's JSON schema.

Each property becomes one field of a kind the form draws: ``choice`` (an enum: buttons, or a
select for many), ``check`` (a bool), ``slider`` (a number with both bounds), ``number``,
``field`` (a field name: ``@field``, the solver's fields; a nullable one also ``solid``),
``string`` or ``text`` (lists, tuples, optional numbers and unions, typed as ``a, b, c`` or
``auto``).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from neofoam_ui.scenes.models import AnyNode

__all__ = ()

#: Properties that name a field of the solver.
_FIELD_PROPS = ("field", "vectors")
#: Properties set elsewhere than in the form: each cell's camera (the pane's camera buttons).
_NOT_IN_FORM = ("cell_cameras",)
#: An enum with at most this many values shows as buttons, else as a select.
_BUTTONS = 5
_SOLID = "(solid)"


def _props_model(kind: str) -> Any:
    """The props model of node type ``kind``."""
    for node in AnyNode.__origin__.__args__:  # type: ignore[attr-defined]
        if node.model_fields["type"].annotation.__args__[0] == kind:
            return node.model_fields["props"].annotation
    raise KeyError(kind)


def _variants(prop: dict[str, Any]) -> list[dict[str, Any]]:
    return list(prop.get("anyOf") or [prop])


def _enum(prop: dict[str, Any]) -> Optional[list[Any]]:
    variants = _variants(prop)
    values = [v for var in variants for v in var.get("enum", [var["const"]] if "const" in var else [])]
    return values if values and all("enum" in v or "const" in v for v in variants) else None


def _field(name: str, prop: dict[str, Any], required: bool) -> dict[str, Any]:
    """One form field of a property (its JSON schema)."""
    out = {"name": name, "label": name.replace("_", " "), "required": required}
    nullable = any(v.get("type") == "null" for v in _variants(prop))
    types = {v.get("type") for v in _variants(prop)} - {"null"}
    values = _enum(prop)
    if name in _FIELD_PROPS:
        return {**out, "kind": "field", "solid": nullable}
    if values is not None:
        return {**out, "kind": "choice", "items": values, "buttons": len(values) <= _BUTTONS}
    if types == {"boolean"}:
        return {**out, "kind": "check"}
    if types == {"string"}:
        return {**out, "kind": "string"}
    if types <= {"number", "integer"} and not nullable:
        if "minimum" in prop and "maximum" in prop:
            return {**out, "kind": "slider", "min": prop["minimum"], "max": prop["maximum"],
                    "step": 1 if types == {"integer"} else (prop["maximum"] - prop["minimum"]) / 200}
        return {**out, "kind": "number", "integer": types == {"integer"}}
    return {**out, "kind": "text"}


def form(kind: str) -> list[dict[str, Any]]:
    """The form fields of node type ``kind``, in the model's order."""
    schema = _props_model(kind).model_json_schema()
    required = set(schema.get("required", []))
    return [_field(name, prop, name in required) for name, prop in schema.get("properties", {}).items()
            if name not in _NOT_IN_FORM]


def shown(fields: list[dict[str, Any]], props: dict[str, Any]) -> dict[str, Any]:
    """The values the form shows for ``props`` (text fields as text)."""
    out: dict[str, Any] = {}
    for f in fields:
        value = props.get(f["name"])
        if f["kind"] == "text":
            out[f["name"]] = _as_text(value)
        elif f["kind"] == "field":
            out[f["name"]] = _SOLID if value is None and f["solid"] else value
        else:
            out[f["name"]] = value
    return out


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_as_text(v) for v in value)
    return str(value)


def _number(text: str) -> Any:
    value = float(text)
    return int(value) if value.is_integer() and "." not in text and "e" not in text.lower() else value


def _from_text(text: str) -> Any:
    """What a text field means: empty → ``None``; ``a, b`` → a list; a number; else the text."""
    text = str(text).strip()
    if not text:
        return None
    if "," in text:
        return [_from_text(part) for part in text.split(",")]
    try:
        return _number(text)
    except ValueError:
        return text


def parsed(field: dict[str, Any], value: Any) -> Any:
    """A form value as the property's value (unchecked: Apply checks the scene)."""
    kind = field["kind"]
    if kind == "text":
        parsed_value = _from_text(value)
        if field["name"] == "values" and not isinstance(parsed_value, list) and parsed_value is not None:
            return [parsed_value]  # one contour value
        return parsed_value
    if kind == "field":
        return None if value == _SOLID else value
    if kind in ("number", "slider") and value not in (None, ""):
        return int(float(value)) if field.get("integer") else float(value)
    return value


def defaults(kind: str) -> dict[str, Any]:
    """The properties a new node starts with: those with defaults (JSON-ready)."""
    model = _props_model(kind)
    return {name: json.loads(json.dumps(f.default))
            for name, f in model.model_fields.items() if not f.is_required()}


SOLID = _SOLID
