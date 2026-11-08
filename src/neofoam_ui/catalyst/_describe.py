"""The live ParaView pipeline as a graph, for the UI's post-processing diagram (settings only)."""

from __future__ import annotations

from typing import Any

__all__ = ()


_SKIP = {"Input", "Producer", "Trigger", "Writer", "View", "Enable"}
_MAX_SETTINGS = 4


def _short(value: Any) -> str:
    """A property value as a short string (numbers to 4 digits, proxies by their label)."""
    if hasattr(value, "SMProperty"):  # a property wrapper (e.g. an array selection): its values
        value = list(value)
    if isinstance(value, (list, tuple)):
        text = "[" + ", ".join(_short(v) for v in value[:4]) + (", …" if len(value) > 4 else "") + "]"
    elif isinstance(value, float):
        text = f"{value:.4g}"
    elif hasattr(value, "SMProxy"):
        text = value.GetXMLLabel()
    else:
        text = str(value)
    return text if len(text) <= 48 else text[:47] + "…"


def _enum(value: Any) -> str:
    return str(value).strip("'")  # an enumeration property's value prints quoted


def _settings(proxy: Any) -> list[list[str]]:
    """Up to ``_MAX_SETTINGS`` properties that differ from their defaults (no inputs, no info)."""
    rows: list[list[str]] = []
    for name in proxy.ListProperties():
        if name in _SKIP:
            continue
        prop = proxy.GetProperty(name).SMProperty
        if prop.GetInformationOnly() or prop.IsA("vtkSMInputProperty") or prop.IsValueDefault():
            continue
        rows.append([name, _short(proxy.GetPropertyValue(name))])
        if len(rows) == _MAX_SETTINGS:
            break
    return rows


def _port_proxy(value: Any) -> Any:
    """The proxy behind an input value (a proxy, or an output port of one)."""
    if isinstance(value, (list, tuple)):
        return [_port_proxy(v) for v in value]
    return getattr(value, "Proxy", value) if not hasattr(value, "GetGlobalIDAsString") else value


def _colouring(rep: Any) -> list[list[str]]:
    """What a display shows: representation, coloured array and component, colour range."""
    rows = [["as", _enum(rep.Representation)]] if rep.GetProperty("Representation") is not None else []
    array = list(rep.ColorArrayName) if rep.GetProperty("ColorArrayName") is not None else []
    lut = rep.LookupTable if rep.GetProperty("LookupTable") is not None else None
    if len(array) < 2 or not array[1] or lut is None:
        return rows + [["colour", "solid"]]
    name = array[1]
    mode = _enum(lut.VectorMode)
    comp = (f"|{name}|" if mode == "Magnitude" else f"{name}[{int(lut.VectorComponent)}]") \
        if _ncomp(rep, array) > 1 else name
    points = list(lut.RGBPoints)
    rows.append(["colour", f"{comp} ({str(array[0]).lower()})"])
    if len(points) >= 4:
        rows.append(["range", f"{points[0]:.4g} … {points[-4]:.4g}"])
    return rows


def _ncomp(rep: Any, array: list[Any]) -> int:
    data = rep.Input.PointData if str(array[0]) == "POINTS" else rep.Input.CellData
    arr = data.GetArray(array[1])
    return arr.GetNumberOfComponents() if arr is not None else 1


def _describe_pipeline(observe: Any = None) -> dict[str, Any]:
    """Nodes and edges of the ParaView pipeline in this process (``paraview.simple``).

    Node: ``{"id", "kind": source|filter|display|view|extractor, "name", "type",
    "settings": [[key, value], ...]}``; edge: ``{"source", "target"}`` (data flows
    from source to target). ``observe(id, vtk_object)`` is called for what can be timed.
    """
    from paraview import servermanager, simple  # noqa: PLC0415 - on sys.path only once Catalyst is loaded

    pxm = servermanager.ProxyManager()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    def gid(proxy: Any) -> str:
        return "pv" + proxy.GetGlobalIDAsString()

    for (name, _), proxy in simple.GetSources().items():
        group = proxy.GetXMLGroup()
        nodes.append({"id": gid(proxy), "kind": "source" if group == "sources" else "filter",
                      "name": name, "type": proxy.GetXMLLabel(), "settings": _settings(proxy)})
        for prop in proxy.ListProperties():
            if proxy.GetProperty(prop).SMProperty.IsA("vtkSMInputProperty"):
                inputs = _port_proxy(proxy.GetPropertyValue(prop))
                for up in inputs if isinstance(inputs, list) else [inputs]:
                    if up is not None:
                        edges.append({"source": gid(up), "target": gid(proxy)})
        if observe is not None:
            observe(gid(proxy), proxy.GetClientSideObject())
    for view in simple.GetViews():
        bars = []
        for rep in view.Representations:
            if rep.GetProperty("Input") is None:  # a colour bar, not a display
                if rep.GetProperty("Title") is not None and rep.Visibility:
                    bars.append(str(rep.Title))
                continue
            if not rep.Visibility or rep.Input is None:
                continue
            nodes.append({"id": gid(rep), "kind": "display",
                          "name": pxm.GetProxyName("representations", rep) or "display",
                          "type": rep.GetXMLLabel(), "settings": _colouring(rep)})
            edges.append({"source": gid(_port_proxy(rep.Input)), "target": gid(rep)})
            edges.append({"source": gid(rep), "target": gid(view)})
            if observe is not None:
                observe(gid(rep), rep.GetClientSideObject())
        settings = [["size", "×".join(str(v) for v in view.ViewSize)]] if view.GetProperty("ViewSize") else []
        settings += [["colour bar", t] for t in bars]
        nodes.append({"id": gid(view), "kind": "view", "name": pxm.GetProxyName("views", view) or "view",
                      "type": view.GetXMLLabel(), "settings": settings})
        if observe is not None and hasattr(view, "GetRenderWindow"):
            observe(gid(view), view.GetRenderWindow())
    for ext in simple.GetExtractors():
        writer = ext.Writer
        settings = [["file", str(writer.FileName)]] if writer.GetProperty("FileName") is not None else []
        if writer.GetProperty("ImageResolution") is not None and any(writer.ImageResolution):
            settings.append(["resolution", "×".join(str(v) for v in writer.ImageResolution)])
        trigger = ext.Trigger
        if trigger is not None and trigger.GetProperty("Frequency") is not None:
            settings.append(["every", f"{int(trigger.Frequency)} step(s)"])
        if not ext.Enable:
            settings.append(["enabled", "no"])
        nodes.append({"id": gid(ext), "kind": "extractor",
                      "name": pxm.GetProxyName("extractors", ext) or "extractor",
                      "type": writer.GetXMLLabel(), "settings": settings})
        if ext.Producer is not None:
            edges.append({"source": gid(_port_proxy(ext.Producer)), "target": gid(ext)})
    known = {n["id"] for n in nodes}
    return {"nodes": nodes, "edges": [e for e in edges if e["source"] in known and e["target"] in known]}
