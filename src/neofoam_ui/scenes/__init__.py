"""Scenes: in-situ visualisations as node graphs, saved in the case as ``system/scenes/<name>.json``.

A scene is a graph of nodes (``grid`` → filters → ``Display`` → ``View``); the UI picks one, the
solver's pipeline (``pipelines/view.py``) builds it in ParaView and keeps it while others show.
This module is plain data: the built-in ``Field`` scene and the graph helpers. The node models
and the checks of a graph are in :mod:`neofoam_ui.scenes.models` (pydantic).

A graph as the solver gets it: ``{"version": 1, "nodes": [{"id", "type", "props"}, ...],
"edges": [[source, target], ...]}`` with every property given (``models.Scene.graph()``)::

    graph = scenes.field_graph()
    for node in scenes.ordered(graph):
        print(node["id"], scenes.inputs(graph)[node["id"]])
"""

from __future__ import annotations

from typing import Any, Sequence

from neofoam_ui.scenes._camera import _camera_fit
from neofoam_ui.scenes._graph import _field_graph, _inputs, _ordered

__all__ = ["FIELD", "SCENES_DIR", "camera_fit", "field_graph", "inputs", "ordered"]

#: The built-in scene's name: today's field view (surface coloured by the pane's field).
FIELD = "Field"
#: Where a case keeps its scenes (relative to the case directory).
SCENES_DIR = "system/scenes"


def field_graph() -> dict[str, Any]:
    """The built-in ``Field`` scene's graph (a fresh copy): the surface, coloured by the pane's
    field and component, seen from the default side."""
    return _field_graph()


def inputs(graph: dict[str, Any]) -> dict[str, list[str]]:
    """Each node's inputs (node ids), in edge order."""
    return _inputs(graph)


def camera_fit(camera: str, zoom: float, bounds: Sequence[float], size: Sequence[float],
               bar_px: float = 0.0) -> dict[str, Any]:
    """A View node's camera (``camera`` preset, ``zoom``) fitted to the domain ``bounds`` in an
    image of ``size`` pixels, ``bar_px`` of it kept free at the bottom for colour bars.

    A parallel projection: ``focal``, ``position`` and ``up`` vectors and the parallel ``scale``.
    The solver's image and the UI's data view use it alike, so both modes frame a scene the same::

        fit = scenes.camera_fit("+z", 1.0, bounds, (800, 400))
    """
    return _camera_fit(camera, zoom, bounds, size, bar_px)


def ordered(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """The nodes, each after its inputs; ``ValueError`` naming a node of a cycle."""
    return _ordered(graph)
