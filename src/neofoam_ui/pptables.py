"""NeoFOAM post-processing tables, seen from the stepper: their rows and their pipelines.

A case declares *tables* (``system/postProcess.py`` / ``.yaml``); NeoFOAM's
``post_process`` op evaluates the due ones each step through a ``PostProcessor``
(``neofoam.postprocess.model``) and hands every result to the table's writer.
:func:`observe` hooks that processor from the outside — nothing in NeoFOAM is
changed — and turns what passes into two worker messages:

* ``pptables`` once, when the processor is built: every table's pipeline as a
  diagram (:func:`describe_table`, read off the node objects' pydantic fields);
* ``pptable`` per evaluated table and step: the newest rows, already reduced over
  the MPI ranks by NeoFOAM's aggregators (only rank 0's messages reach the UI)::

      {"type": "pptable", "name": "p_mean", "time": 0.001, "ms": 0.4,
       "columns": ["p_mean"], "group_columns": [], "values": [[-1.2]], "groups": [[]]}

Duck-typed throughout (no neofoam import), so a fake processor tests it.
"""

from __future__ import annotations

import time
import traceback
from typing import Any, Callable, Optional

__all__ = ["describe_table", "observe", "rows_message"]

Emit = Callable[[dict[str, Any]], None]

#: Settings rows shown per diagram node; the rest is summarised as "…".
MAX_SETTINGS = 6


def observe(processor: Any, emit: Emit) -> bool:
    """Tee the rows ``processor`` writes into ``emit``; False if it is not a PostProcessor.

    The processor opens a private ``(write_control, writer)`` per table on its first
    write (``_open``); the copies are wrapped there, so every table — whoever declared
    it — is seen, and the CSV files are written exactly as before. The time between the
    write control saying "due" and the writer receiving the result is the table's
    compute time.
    """
    tables = getattr(processor, "_tables", None)
    opener = getattr(processor, "_open", None)
    if tables is None or opener is None:
        return False
    try:
        emit({"type": "pptables", "tables": [describe_table(t) for t in tables]})
    except Exception:  # noqa: BLE001 - no diagram rather than no solver
        emit({"type": "stdout", "text": "post-processing tables: " + traceback.format_exc()})
    started: dict[str, float] = {}

    def wrap(runtime: dict[str, Any]) -> dict[str, Any]:
        return {name: rt._replace(write_control=_Due(rt.write_control, name, started),
                                  writer=_Tee(rt.writer, name, started, emit))
                for name, rt in runtime.items()}

    def opened(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return wrap(opener(*args, **kwargs))

    processor._open = opened
    if getattr(processor, "_runtime", None):  # already opened (observed late)
        processor._runtime = wrap(processor._runtime)
    return True


class _Due:
    """A table's write control; notes when it fires, so the compute can be timed."""

    def __init__(self, inner: Any, name: str, started: dict[str, float]) -> None:
        self._inner, self._name, self._started = inner, name, started

    def should_write(self, *args: Any, **kwargs: Any) -> Any:
        due = self._inner.should_write(*args, **kwargs)
        if due:
            self._started[self._name] = time.perf_counter()
        return due

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._inner, attr)


class _Tee:
    """A table's writer: writes as before, then emits the rows it was given."""

    def __init__(self, inner: Any, name: str, started: dict[str, float], emit: Emit) -> None:
        self._inner, self._name, self._started, self._emit = inner, name, started, emit
        self._failed = False

    def write(self, time_value: float, result: Any) -> Any:
        t0 = self._started.pop(self._name, None)
        ms = (time.perf_counter() - t0) * 1e3 if t0 is not None else None
        out = self._inner.write(time_value, result)
        if not self._failed:
            try:
                message = rows_message(self._name, time_value, result, ms)
                if message is not None:
                    self._emit(message)
            except Exception:  # noqa: BLE001 - reported once; the CSV is still written
                self._failed = True
                self._emit({"type": "stdout", "text": f"post-processing table {self._name!r}: "
                                                      + traceback.format_exc()})
        return out

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._inner, attr)


def rows_message(name: str, time_value: float, result: Any, ms: Optional[float] = None
                 ) -> Optional[dict[str, Any]]:
    """An ``AggregatedDataSet`` as a ``pptable`` message; ``None`` for one without rows.

    Its ``headers`` / ``grouped_values`` put the value columns first and the group labels
    (a bin index, a residual's field and solver, …) after them; the message keeps the two
    apart, so the UI can draw one line per value column and group.
    """
    rows = list(getattr(result, "values", None) or ())
    if not rows:
        return None
    headers = list(result.headers)
    labels = list(getattr(rows[0], "group_name", None) or ())
    n_values = len(headers) - len(labels)
    values, groups = [], []
    for row in result.grouped_values:
        values.append([_number(v) for v in row[:n_values]])
        groups.append([_label(v) for v in row[n_values:]])
    message: dict[str, Any] = {
        "type": "pptable", "name": name, "time": float(time_value),
        "columns": headers[:n_values], "group_columns": headers[n_values:],
        "values": values, "groups": groups,
    }
    if ms is not None:
        message["ms"] = round(ms, 3)
    return message


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None  # NaN/inf: no JSON


def _label(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value if isinstance(value, (int, float, str)) else str(value)


# -- the pipeline as a diagram --------------------------------------------------------------


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
    if len(settings) <= MAX_SETTINGS:
        return settings
    return settings[:MAX_SETTINGS - 1] + [["…", f"{len(settings) - MAX_SETTINGS + 1} more"]]
