"""The node models of a scene and the checks of its graph (pydantic).

One model per node type, its properties a model of their own (the editor's forms come from
``model_json_schema()``); :class:`Scene` holds them as a union discriminated by ``type``.
A scene is checked when it is read (:func:`parse`, :func:`read_scenes`) and when the solver
gets it: a bad one raises :class:`SceneError` naming the node and the reason.

Ports are typed: ``grid`` and the filters give *data*, a ``Display`` takes data and gives a
*display*, the ``View`` takes displays; each port has a name the editor shows (``takes_name``,
``gives_name``). The View has a ``layout`` of cells, each an input port of its own named by the
layout (:func:`neofoam_ui.scenes.cells`): an edge to it names the cell as a third element
(``[display, view, "left"]``; one to a one-cell View may leave it out). A filter and a display
take exactly one input, a display shows in one cell; a scene has one ``View`` and at most one ``grid``. Field properties are a field name or ``@field``
(the pane's pick); components are ``mag``, ``0``–``2`` or ``@component``. Positions are 0–1 of
the domain's extent along the axis (``auto``: the longest in-plane axis of a 2D case, else z).

Use it in the UI process and, for a scene of the case, in the solver's pipeline; the built-in
``Field`` scene needs none of it (:mod:`neofoam_ui.scenes`)::

    scene = models.parse(path.read_text())
    graph = scene.graph()   # what the solver builds
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from neofoam_ui.scenes import SCENES_DIR, cells, ordered

__all__ = ["CalculatorNode", "CellCamera", "ClipNode", "ContourNode", "DisplayNode", "GlyphNode", "GridNode", "Scene",
           "SceneError", "SceneFile", "SliceNode", "StreamTracerNode", "SurfaceNode", "ThresholdNode",
           "ViewNode", "parse", "read_scenes", "write_scene"]

FieldName = Annotated[str, StringConstraints(min_length=1)]
Component = Literal["@component", "mag", "0", "1", "2"]
Axis = Literal["auto", "x", "y", "z"]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
Colormap = Literal["viridis", "coolwarm", "inferno", "jet", "grayscale"]
Camera = Literal["reset", "+x", "-x", "+y", "-y", "+z", "-z"]
Zoom = Annotated[float, Field(ge=0.25, le=50.0)]
Layout = Literal["1", "1x2", "2x1", "2x2"]
#: The ports: what a node takes and gives.
Port = Literal["data", "display"]


class SceneError(ValueError):
    """A scene that cannot be built: the message names the node (``node``, if one) and the reason."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        named = re.match(r"node '([^']+)'", message)
        self.node: Optional[str] = named.group(1) if named else None


class _Props(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The input port (``None``: none) and the output port (``None``: none).
    takes: ClassVar[Optional[Port]] = "data"
    gives: ClassVar[Optional[Port]] = "data"
    #: Takes any number of inputs (else exactly one).
    many: ClassVar[bool] = False
    #: The ports' names, as the editor shows them (``None`` where there is no port).
    takes_name: ClassVar[Optional[str]] = "input"
    gives_name: ClassVar[Optional[str]] = "output"

    id: FieldName

    @classmethod
    def input_ports(cls, props: dict[str, Any]) -> tuple[str, ...]:
        """The names of the input ports a node of these properties has (one, or none)."""
        return (cls.takes_name,) if cls.takes_name else ()


class GridProps(_Props):
    pass


class GridNode(_Node):
    """The solver's cells with every cell field (one, shared by all scenes)."""

    takes: ClassVar[Optional[Port]] = None
    takes_name: ClassVar[Optional[str]] = None
    gives_name: ClassVar[Optional[str]] = "mesh"
    type: Literal["grid"]
    props: GridProps = GridProps()


class SurfaceProps(_Props):
    pass


class SurfaceNode(_Node):
    """The outer surface of its input."""

    type: Literal["Surface"]
    props: SurfaceProps = SurfaceProps()


class SliceProps(_Props):
    axis: Axis = "auto"
    position: Fraction = 0.5
    triangulate: bool = True


class SliceNode(_Node):
    """A plane through the input, normal to an axis."""

    type: Literal["Slice"]
    props: SliceProps = SliceProps()


class ClipProps(_Props):
    axis: Axis = "auto"
    position: Fraction = 0.5
    #: Keep the upper side (else the lower one).
    flip: bool = False


class ClipNode(_Node):
    """The input cut by a plane normal to an axis, one side kept."""

    type: Literal["Clip"]
    props: ClipProps = ClipProps()


class ContourProps(_Props):
    field: FieldName = "@field"
    component: Component = "@component"
    values: list[float] = Field(min_length=1)


class ContourNode(_Node):
    """Iso-surfaces (iso-lines in a slice) of a field's values."""

    type: Literal["Contour"]
    props: ContourProps


class ThresholdProps(_Props):
    field: FieldName = "@field"
    component: Component = "@component"
    #: Bounds of the kept cells' values (``None``: open).
    lower: Optional[float] = None
    upper: Optional[float] = None


class ThresholdNode(_Node):
    """The cells whose value lies between the bounds."""

    type: Literal["Threshold"]
    props: ThresholdProps = ThresholdProps()


class CalculatorProps(_Props):
    expression: FieldName
    result: FieldName = "Result"


class CalculatorNode(_Node):
    """A new cell field from an expression of the others (ParaView's calculator)."""

    type: Literal["Calculator"]
    props: CalculatorProps


class GlyphProps(_Props):
    vectors: FieldName = "@field"
    #: Arrow length (``None``: ParaView's, from the input's size).
    scale: Optional[Annotated[float, Field(gt=0.0)]] = None
    max_glyphs: Annotated[int, Field(ge=1)] = 5000


class GlyphNode(_Node):
    """Arrows along a vector field."""

    type: Literal["Glyph"]
    props: GlyphProps = GlyphProps()


class StreamTracerProps(_Props):
    vectors: FieldName = "@field"
    #: The seed line's ends, as fractions of the domain's extent (default: its diagonal).
    start: tuple[Fraction, Fraction, Fraction] = (0.0, 0.0, 0.0)
    end: tuple[Fraction, Fraction, Fraction] = (1.0, 1.0, 1.0)
    seeds: Annotated[int, Field(ge=1)] = 100


class StreamTracerNode(_Node):
    """Streamlines of a vector field from seeds along a line."""

    type: Literal["StreamTracer"]
    props: StreamTracerProps = StreamTracerProps()


class DisplayProps(_Props):
    #: Colour by this field (``None``: the solid ``color``).
    field: Optional[FieldName] = "@field"
    component: Component = "@component"
    colormap: Colormap = "viridis"
    range: Union[Literal["auto"], tuple[float, float]] = "auto"
    log: bool = False
    color: tuple[Fraction, Fraction, Fraction] = (1.0, 1.0, 1.0)
    line_width: Annotated[float, Field(gt=0.0)] = 1.0
    opacity: Fraction = 1.0
    #: Show its colour bar (a coloured display; several bars stack upwards).
    bar: bool = True

    @model_validator(mode="after")
    def _range_rises(self) -> DisplayProps:
        if self.range != "auto" and not self.range[1] > self.range[0]:
            raise ValueError(f"range {list(self.range)}: the upper end must be above the lower one")
        return self


class DisplayNode(_Node):
    """How its input is drawn: coloured by a field (map, range, log scale) or solid."""

    gives: ClassVar[Optional[Port]] = "display"
    gives_name: ClassVar[Optional[str]] = "to view"
    type: Literal["Display"]
    props: DisplayProps = DisplayProps()


class CellCamera(_Props):
    camera: Camera = "reset"
    zoom: Zoom = 1.0


class ViewProps(_Props):
    #: The camera of every cell, while they are linked (per cell: of a cell without its own).
    camera: Camera = "reset"
    zoom: Zoom = 1.0
    #: The cells (each an input port): ``1``, ``1x2``, ``2x1`` or ``2x2``.
    layout: Layout = "1"
    #: ``linked``: one camera for all cells; ``per_cell``: each cell's own (``cell_cameras``).
    cameras: Literal["linked", "per_cell"] = "linked"
    cell_cameras: dict[str, CellCamera] = {}

    @model_validator(mode="after")
    def _cameras_of_cells(self) -> ViewProps:
        names = cells(self.layout)
        for cell in self.cell_cameras:
            if cell not in names:
                raise ValueError(f"cell_cameras: no cell {cell!r} in the {self.layout} layout ({', '.join(names)})")
        return self


class ViewNode(_Node):
    """The picture: its displays, seen from the camera (the pane shows it)."""

    takes: ClassVar[Optional[Port]] = "display"
    gives: ClassVar[Optional[Port]] = None
    gives_name: ClassVar[Optional[str]] = None
    many: ClassVar[bool] = True
    takes_name: ClassVar[Optional[str]] = "displays"
    type: Literal["View"]
    props: ViewProps = ViewProps()

    @classmethod
    def input_ports(cls, props: dict[str, Any]) -> tuple[str, ...]:
        """One port per cell of the layout, named by it."""
        return cells(props.get("layout", "1"))


AnyNode = Annotated[Union[GridNode, SurfaceNode, SliceNode, ClipNode, ContourNode, ThresholdNode, CalculatorNode,
                          GlyphNode, StreamTracerNode, DisplayNode, ViewNode], Field(discriminator="type")]


def _label(node: _Node) -> str:
    return f"node {node.id!r} ({node.type})"  # type: ignore[attr-defined]


Edge = Union[tuple[FieldName, FieldName], tuple[FieldName, FieldName, FieldName]]


class Scene(BaseModel):
    """A scene: its nodes, the edges between them (``[source, target]`` or ``[source, target,
    port]``), the editor's layout."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    #: What the panel calls it (else the file's stem, its name).
    title: Optional[str] = None
    nodes: list[AnyNode]
    edges: list[Edge] = []
    #: Node positions in the editor (the solver ignores them).
    layout: dict[str, tuple[float, float]] = {}

    @model_validator(mode="after")
    def _graph_holds(self) -> Scene:
        by_id = self._unique_ids()
        self._one_view_one_grid()
        self._edges_fit(by_id)
        self._inputs_complete(by_id)
        self._one_cell_each(by_id)
        ordered(self.graph())
        return self

    def _unique_ids(self) -> dict[str, _Node]:
        by_id: dict[str, _Node] = {}
        for node in self.nodes:
            if node.id in by_id:
                raise ValueError(f"{_label(node)}: the id is used twice")
            by_id[node.id] = node
        return by_id

    def _one_view_one_grid(self) -> None:
        views = [n for n in self.nodes if n.type == "View"]
        if len(views) != 1:
            raise ValueError(f"a scene has one View node, not {len(views)}")
        grids = [n for n in self.nodes if n.type == "grid"]
        if len(grids) > 1:
            raise ValueError(f"{_label(grids[1])}: a scene has at most one grid")

    def _edges_fit(self, by_id: dict[str, _Node]) -> None:
        seen = set()
        for edge in self.edges:
            source, target = edge[:2]
            for end in (source, target):
                if end not in by_id:
                    raise ValueError(f"edge {source!r} → {target!r}: no node {end!r}")
            src, dst = by_id[source], by_id[target]
            if dst.takes is None or src.gives != dst.takes:
                takes = f"takes {dst.takes}" if dst.takes else "takes no input"
                raise ValueError(f"{_label(dst)} {takes}, not the {src.gives or 'nothing'} of {_label(src)}")
            key = (source, target, _port(edge, dst))
            if key in seen:
                raise ValueError(f"edge {source!r} → {target!r} is there twice")
            seen.add(key)

    def _one_cell_each(self, by_id: dict[str, _Node]) -> None:
        count = {node_id: 0 for node_id in by_id}
        for source, *_ in self.edges:
            count[source] += 1
        for node in self.nodes:
            if node.gives == "display" and count[node.id] > 1:
                raise ValueError(f"{_label(node)} shows in one cell of the View, not {count[node.id]}")

    def _inputs_complete(self, by_id: dict[str, _Node]) -> None:
        count = {node_id: 0 for node_id in by_id}
        for _, target, *_ in self.edges:
            count[target] += 1
        for node in self.nodes:
            if node.takes is not None and not node.many and count[node.id] != 1:
                raise ValueError(f"{_label(node)} needs one input, has {count[node.id]}")

    def graph(self) -> dict[str, Any]:
        """What the solver builds: version, nodes with every property, edges (no title, no layout)."""
        return self.model_dump(mode="json", exclude={"title", "layout"})


def _port(edge: tuple[str, ...], target: _Node) -> str:
    """The input port an edge goes to: the one it names, else the target's only port."""
    ports = target.input_ports(target.props.model_dump())  # type: ignore[attr-defined]
    if len(edge) > 2:
        if edge[2] not in ports:
            raise ValueError(f"edge {edge[0]!r} → {edge[1]!r}: {_label(target)} has no port {edge[2]!r}"
                             f" (ports: {', '.join(ports)})")
        return edge[2]
    if len(ports) > 1:
        layout = target.props.layout  # type: ignore[attr-defined]
        raise ValueError(f"edge {edge[0]!r} → {edge[1]!r}: name the cell of the {layout} layout ({', '.join(ports)})")
    return ports[0]


def _where(raw: Any, loc: tuple[Any, ...]) -> str:
    """The node a pydantic error location points into (``nodes``, index, …), and the rest."""
    if len(loc) < 2 or loc[0] != "nodes" or not isinstance(loc[1], int):
        return ".".join(map(str, loc))
    nodes = raw.get("nodes") if isinstance(raw, dict) else None
    node = nodes[loc[1]] if isinstance(nodes, list) and loc[1] < len(nodes) else None
    node = node if isinstance(node, dict) else {}
    head = f"node {node.get('id', loc[1])!r} ({node.get('type', '?')})"
    rest = ".".join(map(str, loc[3:] if len(loc) > 2 and loc[2] == node.get("type") else loc[2:]))
    return f"{head}: {rest}" if rest else head


def _message(raw: Any, exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = _where(raw, tuple(first["loc"]))
    msg = first["msg"].removeprefix("Value error, ")
    return f"{where}: {msg}" if where else msg


def parse(data: Union[str, bytes, dict[str, Any]]) -> Scene:
    """A scene from JSON text or a dict; :class:`SceneError` (node and reason) if it is not one."""
    try:
        raw = json.loads(data) if isinstance(data, (str, bytes)) else data
    except ValueError as exc:
        raise SceneError(f"not JSON: {exc}") from exc
    try:
        return Scene.model_validate(raw)
    except ValidationError as exc:
        raise SceneError(_message(raw, exc)) from None


@dataclass(frozen=True)
class SceneFile:
    """A scene file of a case: its name (the file's stem), path, and the scene or why it is not one."""

    name: str
    path: Path
    scene: Optional[Scene]
    error: Optional[str]


def read_scenes(case: Union[str, Path]) -> list[SceneFile]:
    """Every ``system/scenes/*.json`` of ``case``, by name; a bad one with its error, not its scene."""
    out = []
    for path in sorted((Path(case) / SCENES_DIR).glob("*.json")):
        try:
            out.append(SceneFile(path.stem, path, parse(path.read_text()), None))
        except (SceneError, OSError, UnicodeDecodeError) as exc:
            out.append(SceneFile(path.stem, path, None, str(exc)))
    return out


def write_scene(case: Union[str, Path], name: str, scene: Scene) -> Path:
    """Save ``scene`` as ``system/scenes/<name>.json`` of ``case`` (the directory is made)."""
    path = Path(case) / SCENES_DIR / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scene.model_dump_json(indent=2) + "\n")
    return path
