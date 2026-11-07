"""A table's pipeline as a diagram, read generically off its nodes (or by a registered describer)."""

from __future__ import annotations

from typing import Any, Optional, Protocol

__all__ = ()

#: Settings rows shown per diagram node; the rest is summarised as "…".
_MAX_SETTINGS = 6


class NodeDescriber(Protocol):
    """Describes a post-processing node the generic reading does not: ``None`` = not mine.

    Otherwise a dict with any of ``name``, ``type``, ``kind`` (its colour in the diagram) and
    ``settings`` (``[[key, value], ...]``); the rest comes from the generic reading. The
    node's nested plugins are then not drawn (the describer's settings stand for them)."""

    def __call__(self, obj: Any) -> Optional[dict[str, Any]]: ...


_DESCRIBERS: list[NodeDescriber] = []


def register_describer(describer: NodeDescriber) -> None:
    """Use ``describer`` (first) for every table described from now on; once, at start-up."""
    _DESCRIBERS.insert(0, describer)


def _custom(obj: Any) -> Optional[dict[str, Any]]:
    for describer in _DESCRIBERS:
        found = describer(obj)
        if found is not None:
            return found
    return None


def describe_table(table: Any) -> dict[str, Any]:
    """One table's pipeline in the diagram format of :meth:`neofoam_ui.catalyst.InSitu.describe`.

    ``{"name", "nodes": [{"id", "kind", "name", "type", "settings"}], "edges": [{"source",
    "target", "label"?}]}``: the source, each step, then the writer with its write control.
    Nodes are read generically: ``type`` is the plugin discriminator, the settings are the
    other pydantic fields; a field holding a plugin itself (a selector inside ``not`` / ``binary``)
    becomes a node of its own with an edge into its holder, labelled with the field name.
    """
    name = str(getattr(table, "name", "table"))
    pipeline = getattr(table, "pipeline", None)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    prefix = f"nf:{name}:"
    chain: list[str] = []
    source = getattr(pipeline, "source", None)
    if source is not None:
        chain.append(_plugin(source, prefix + "source", "source", nodes, edges))
    for i, step in enumerate(getattr(pipeline, "steps", None) or ()):
        chain.append(_plugin(step, f"{prefix}{i}", _kind(step), nodes, edges))
    writer = getattr(table, "writer", None)
    control = getattr(table, "write_control", None)
    if writer is not None or control is not None:
        settings = _settings(writer) if writer is not None else []
        if control is not None:
            settings = [["when", _type_of(control)], *_settings(control)] + settings
        nodes.append({"id": prefix + "writer", "kind": "writer", "name": name,
                      "type": _type_of(writer) if writer is not None else "—",
                      "settings": _cap(settings)})
        chain.append(prefix + "writer")
    edges += [{"source": a, "target": b} for a, b in zip(chain, chain[1:])]
    return {"name": name, "nodes": nodes, "edges": edges}


def _plugin(obj: Any, nid: str, kind: str, nodes: list[dict[str, Any]],
            edges: list[dict[str, Any]]) -> str:
    """Add ``obj`` (and the plugins it holds) as diagram nodes; return its id."""
    label = getattr(obj, "name", None)
    custom = _custom(obj) if _DESCRIBERS else None
    if custom is not None:
        nodes.append({"id": nid, "kind": str(custom.get("kind", kind)),
                      "name": str(custom.get("name", label if isinstance(label, str) and label else _type_of(obj))),
                      "type": str(custom.get("type", _type_of(obj))),
                      "settings": _cap([[str(k), str(v)] for k, v in custom.get("settings", ())])})
        return nid
    node = {"id": nid, "kind": kind, "name": label if isinstance(label, str) and label else _type_of(obj),
            "type": _type_of(obj), "settings": []}
    nodes.append(node)
    settings: list[list[str]] = []
    for key, value in _fields(obj):
        if key == "name":
            continue
        held = [value] if _is_plugin(value) else (
            list(value) if isinstance(value, (list, tuple)) and value and all(map(_is_plugin, value)) else [])
        if held:
            for j, inner in enumerate(held):
                child = _plugin(inner, f"{nid}.{key}" + (f"{j}" if len(held) > 1 else ""),
                                _kind(inner), nodes, edges)
                edges.append({"source": child, "target": nid, "label": key})
            continue
        settings.append([key, _fmt(value)])
    node["settings"] = _cap(settings)
    return nid


def _settings(obj: Any) -> list[list[str]]:
    """A plugin's fields as ``[key, value]`` rows (nested plugins by their type)."""
    return [[key, _fmt(value)] for key, value in _fields(obj)]


def _fields(obj: Any) -> list[tuple[str, Any]]:
    """A pydantic model's fields (not the discriminator), unset optional ones left out."""
    names = getattr(type(obj), "model_fields", None)
    if names is None:
        names = {k: None for k in vars(obj) if not k.startswith("_")} if hasattr(obj, "__dict__") else {}
    out = []
    discriminator = _discriminator(obj)
    for key in names:
        if key == discriminator:
            continue
        value = getattr(obj, key, None)
        if value is None:
            continue
        out.append((key, value))
    return out


def _is_plugin(value: Any) -> bool:
    """A node/source held as a field: a pydantic model with a ``type`` discriminator."""
    return hasattr(type(value), "model_fields") and isinstance(getattr(value, "type", None), str)


def _discriminator(obj: Any) -> Optional[str]:
    """The field naming the plugin: ``type``, else the first ``*_type`` (``write_control_type``)."""
    if isinstance(getattr(obj, "type", None), str):
        return "type"
    for key in getattr(type(obj), "model_fields", None) or ():
        if key.endswith("_type") and isinstance(getattr(obj, key, None), str):
            return key
    return None


def _type_of(obj: Any) -> str:
    """The plugin discriminator's value, else the class name."""
    key = _discriminator(obj)
    return str(getattr(obj, key)) if key else type(obj).__name__


def _kind(obj: Any) -> str:
    """What a step is, from the module NeoFOAM files it in (``…nodes.selectors`` → selector).

    A node from elsewhere (a case script's own) is ``custom``.
    """
    module = type(obj).__module__ or ""
    if ".postprocess." not in f".{module}.":
        return "custom"
    tail = module.rsplit(".", 1)[-1].strip("_").replace("_", " ")
    return tail[:-1] if tail.endswith("s") and not tail.endswith("ss") else tail


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_fmt(v) for v in value[:8]) + (", …" if len(value) > 8 else "")
        return f"[{inner}]" if isinstance(value, list) else f"({inner})"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_fmt(v)}" for k, v in list(value.items())[:4]) + "}"
    if hasattr(value, "model_dump"):
        return _type_of(value)
    return str(value)


def _cap(settings: list[list[str]]) -> list[list[str]]:
    if len(settings) <= _MAX_SETTINGS:
        return settings
    return settings[:_MAX_SETTINGS - 1] + [["…", f"{len(settings) - _MAX_SETTINGS + 1} more"]]
