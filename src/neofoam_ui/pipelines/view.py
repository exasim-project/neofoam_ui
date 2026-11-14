"""Catalyst 2 pipeline of the stepper's field view: it builds scenes (runs inside the solver, all ranks).

Channel ``grid``: the solver's cells with every cell field. Parameters per execute
(``catalyst/state/parameters``, all optional):

* ``field=<name>``, ``component=mag|0|1|2`` (what ``@field`` / ``@component`` bind to), ``mode=image|data``;
* ``scene=<name>`` (default ``Field``, the built-in scene) and, for any other scene, ``graph=<JSON>``
  (a scene graph, :mod:`neofoam_ui.scenes`; checked with :mod:`neofoam_ui.scenes.models`);
* image size ``width``/``height`` and ``scale`` (device pixels per CSS pixel: fonts grow);
* ``export=<path>``: also write the shown scene there as a Catalyst script.

Every scene shown once stays built: a ParaView proxy per node and a render view per cell of
its View's layout (more than one cell: arranged in a ParaView layout, :data:`shown_layout`).
A new graph for a scene is applied as a diff (unchanged nodes keep their proxies, properties
are set on them, inputs relinked; a new layout keeps the filters, a Display that changes cells
is drawn anew in its cell's view); only the shown scene executes. Camera and zoom come from
the scene's View node: one for all cells (``linked``) or each cell's own (``per_cell``); the
camera is fitted again for a new size. A scene that
cannot be built is dropped with its error (``report["error"]`` names the node), until another
graph comes for it; the other scenes are not touched.

Script arguments (``key=value``, each optional): with ``out=<dir>`` it writes
``view_<timestep>.png`` of the whole domain there (a layout: all cells in one picture); the caller may capture :data:`shown_view` (or
:data:`shown_layout`) as well. ``report`` tells the
caller of the last execute: the domain ``bounds``, the default ``axis``, ``notes``, the shown
``scene``, its ``error`` (and ``error_node``), each node's proxies (``proxies``, for timings; the
View's: one render view per cell) and each cell's view (``cells``), in
data mode the ``extracts`` (each Display's input, the source Catalyst Live sends, with the
Display's settings), and ``exported`` after ``export=<path>`` (a Catalyst script of the scene).
``live=host:port`` lets a ParaView attach (Catalyst ▸ Connect) at every
execute; ``data=host:port`` (the stepper UI's data mode) publishes to
Live only in ``mode=data``, where the input of the scene's first Display is updated but
nothing is drawn.
"""

import inspect
import os
from typing import Any

from paraview import catalyst, servermanager
from paraview.simple import (Calculator, CellDatatoPointData, Clip, ColorBy, Contour, CreateExtractor, CreateLayout,
                             CreateView, Delete, ExtractSurface, GetScalarBar, Glyph, Slice,
                             StreamTracer, Threshold, TrivialProducer)
from vtkmodules.vtkCommonCore import vtkDoubleArray
from vtkmodules.vtkCommonDataModel import vtkDataObjectTreeIterator
from vtkmodules.vtkParallelCore import vtkCommunicator, vtkMultiProcessController
from vtkmodules.vtkRenderingFreeType import vtkMathTextUtilities

from neofoam_ui.scenes import FIELD, camera_fit, cell_of, cells, field_graph, inputs, ordered, shape
from neofoam_ui.scenes.models import parse

_args = dict(arg.split("=", 1) for arg in catalyst.get_args())

options = catalyst.Options()
_live = _args.get("live", "")
_data = _args.get("data", "")
if _live or _data:
    options.CatalystLiveURL = _live or _data
    options.EnableCatalystLive = int(bool(_live))

grid = TrivialProducer(registrationName="grid")
#: The PNG extractor of ``out=<dir>`` while the shown scene has one cell (ParaView's image
#: extractors take a view, not a layout: a layout is written by :func:`_save_layout_png`).
png = None
if "out" in _args:
    options.ExtractsOutputDirectory = _args["out"]

_COMPONENT = {"mag": "Magnitude", "0": "X", "1": "Y", "2": "Z"}
_CMAPS = {"viridis": "Viridis (matplotlib)", "coolwarm": "Cool to Warm", "inferno": "Inferno (matplotlib)",
          "jet": "Jet", "grayscale": "Grayscale"}
_AXES = "xyz"
_BIG = 1e300
#: What the caller reads after an execute (see the module docstring).
report: dict[str, Any] = {}
#: The render view of the shown scene (its first cell's): what the caller captures.
shown_view = None
#: The shown scene's layout when its View has more than one cell (else ``None``): what the caller
#: captures then, all cells in one picture.
shown_layout = None
_state = {"bounds": None}
#: The scenes built so far, by name, and those that failed (name: (graph text, error)).
_scenes: dict[str, Any] = {}
_failed: dict[str, tuple[str, str, Any]] = {}


def _local(proxy=grid):
    """This rank's output of ``proxy`` (a partitioned or multiblock output: its first leaf)."""
    data = proxy.GetClientSideObject().GetOutputDataObject(0)
    if data is None or not data.IsA("vtkDataObjectTree"):
        return data
    leaves = vtkDataObjectTreeIterator()
    leaves.SetDataSet(data)
    leaves.InitTraversal()
    return None if leaves.IsDoneWithTraversal() else leaves.GetCurrentDataObject()


def _global(lows, highs):
    """Element-wise min of ``lows`` / max of ``highs`` over all ranks.

    Image size, camera and colour range must be identical on every rank, or
    the parallel compositing (IceT) gathers images of different sizes.
    """
    controller = vtkMultiProcessController.GetGlobalController()
    if controller is None or controller.GetNumberOfProcesses() < 2:
        return list(lows), list(highs)
    out = []
    for values, op in ((lows, vtkCommunicator.MIN_OP), (highs, vtkCommunicator.MAX_OP)):
        send, recv = vtkDoubleArray(), vtkDoubleArray()
        for v in values:
            send.InsertNextValue(v)
        controller.AllReduce(send, recv, op)
        out.append([recv.GetValue(i) for i in range(recv.GetNumberOfTuples())])
    return out


def _bounds():
    local = _local()
    b = local.GetBounds() if local is not None else (1, 0, 1, 0, 1, 0)
    empty = b[0] > b[1]
    lo, hi = _global([_BIG if empty else b[i] for i in (0, 2, 4)],
                     [-_BIG if empty else b[i] for i in (1, 3, 5)])
    return [lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]]


def _arrays(proxy, association):
    local = _local(proxy)
    if local is None:
        return None
    return local.GetCellData() if association == "CELLS" else local.GetPointData()


def _range(proxy, association, field, index):
    """The global range of ``field`` (component ``index``, -1: magnitude) in ``proxy``'s output."""
    arrays = _arrays(proxy, association)
    data = arrays.GetArray(field) if arrays is not None else None
    lo, hi = _BIG, -_BIG
    if data is not None and data.GetNumberOfTuples():
        lo, hi = data.GetRange(index)
    (lo,), (hi,) = _global([lo], [hi])
    return lo, hi


def _params(info):
    """``catalyst/state/parameters`` as a dict (ParaView 6 hands over the whole execute node)."""
    node = info.catalyst_params
    if node.has_path("catalyst/state/parameters"):
        node = node.fetch_existing("catalyst/state/parameters")
    out = {}
    for i in range(node.number_of_children()):
        key, _, value = str(node.child(i).value()).partition("=")
        out[key] = value
    return out


def _domain():
    """The global bounds of the grid, once (the mesh does not move)."""
    if _state["bounds"] is None:
        _state["bounds"] = _bounds()
    return _state["bounds"]


def _extents(b):
    return [b[1] - b[0], b[3] - b[2], b[5] - b[4]]


def _default_axis(b):
    """The longest in-plane axis of a one-cell-thick (2D) case, else z."""
    extent = _extents(b)
    thin = extent.index(min(extent))
    if extent[thin] > 0.02 * max(extent):  # thicker than a 2D case's one cell
        return "z"
    return _AXES[max((a for a in range(3) if a != thin), key=lambda a: extent[a])]


def _at(b, fractions):
    """The point at ``fractions`` (0–1) of the domain's extent along each axis."""
    return [b[2 * a] + f * (b[2 * a + 1] - b[2 * a]) for a, f in enumerate(fractions)]


def _plane(props, b):
    """Origin and normal of a cut: normal to ``axis`` at ``position`` of the domain."""
    axis = _AXES.index(_default_axis(b) if props["axis"] == "auto" else props["axis"])
    fractions = [0.5, 0.5, 0.5]
    fractions[axis] = props["position"]
    normal = [0.0, 0.0, 0.0]
    normal[axis] = 1.0
    return _at(b, fractions), normal


# -- binding to the pane's pick ---------------------------------------------------------------


class _Context:
    """What one execute's properties depend on: the pane's field and component, the domain."""

    def __init__(self, params, bounds, time):
        self.time = time
        self.field = params.get("field") or None
        self.component = params.get("component", "mag")
        self.bounds = bounds
        self.scale = float(params.get("scale", 1))
        self.size = _image_size(params, bounds)
        self.height = self.size[1]
        #: Where the fields are in the input of the node being set (``CELLS`` or ``POINTS``).
        self.association = "CELLS"

    def field_of(self, value):
        return self.field if value == "@field" else value

    def component_of(self, value):
        return self.component if value == "@component" else value


def _ncomp(field):
    arrays = _arrays(grid, "CELLS")
    data = arrays.GetArray(field) if arrays is not None and field else None
    return data.GetNumberOfComponents() if data is not None else 1


def _expression(field, component):
    """A calculator expression of ``field``'s value: itself, its magnitude or one component."""
    if _ncomp(field) == 1:
        return f'"{field}"'
    if component == "mag":
        return f'mag("{field}")'
    return f'"{field}_{"XYZ"[int(component)]}"'


# -- nodes: create, set properties, relink, delete --------------------------------------------


def _name(scene, node):
    return f"{scene.name}:{node['id']}"


def _create_filter(scene, node, source):
    """The node's proxy and the helpers in front of it (each fed by the one before)."""
    kind, name = node["type"], _name(scene, node)
    if kind == "Surface":
        return ExtractSurface(registrationName=name, Input=source), []
    if kind == "Slice":
        return Slice(registrationName=name, Input=source, SliceType="Plane"), []
    if kind == "Clip":
        return Clip(registrationName=name, Input=source, ClipType="Plane"), []
    if kind == "Threshold":
        return Threshold(registrationName=name, Input=source), []
    if kind == "Calculator":
        return Calculator(registrationName=name, Input=source, AttributeType="Cell Data"), []
    if kind == "Glyph":
        return Glyph(registrationName=name, Input=source, GlyphType="Arrow"), []
    return _create_with_points(name, kind, source)


def _create_with_points(name, kind, source):
    """Contour and StreamTracer work on point data: a cell-to-point helper in front."""
    points = CellDatatoPointData(registrationName=f"{name}/points", Input=source, PassCellData=1)
    if kind == "StreamTracer":
        return StreamTracer(registrationName=name, Input=points, SeedType="Line"), [points]
    value = Calculator(registrationName=f"{name}/value", Input=points, AttributeType="Point Data",
                       ResultArrayName="_value")
    return Contour(registrationName=name, Input=value), [points, value]


def _create(scene, node, sources):
    kind = scene.kinds[node["id"]] = node["type"]
    if kind == "grid":
        scene.proxies[node["id"]] = grid
    elif kind == "View":
        scene.proxies[node["id"]] = scene.view
    elif kind == "Display":
        rep = servermanager.CreateRepresentation(sources[0], scene.views[_cell_index(scene.graph)[node["id"]]])
        scene.proxies[node["id"]] = rep
    else:
        scene.proxies[node["id"]], scene.helpers[node["id"]] = _create_filter(scene, node, sources[0])


def _relink(scene, node, sources):
    if node["type"] in ("grid", "View"):
        return
    head = (scene.helpers.get(node["id"]) or [scene.proxies[node["id"]]])[0]
    if head.Input != sources[0]:
        head.Input = sources[0]


def _delete(scene, node, proxy, helpers):
    if node["type"] in ("grid", "View"):
        return
    if node["type"] == "Display":
        next(v for v in scene.views if proxy in v.Representations).Representations.remove(proxy)
    Delete(proxy)
    for helper in reversed(helpers):
        Delete(helper)


def _set_slice(proxy, props, ctx):
    proxy.SliceType.Origin, proxy.SliceType.Normal = _plane(props, ctx.bounds)
    proxy.Triangulatetheslice = int(props["triangulate"])


def _set_clip(proxy, props, ctx):
    proxy.ClipType.Origin, proxy.ClipType.Normal = _plane(props, ctx.bounds)
    proxy.Invert = 0 if props["flip"] else 1  # inverted: the lower side stays


def _set_threshold(proxy, props, ctx):
    field = ctx.field_of(props["field"])
    component, ncomp = ctx.component_of(props["component"]), _ncomp(field)
    proxy.Scalars = [ctx.association, field]
    proxy.ComponentMode = "Selected"
    proxy.SelectedComponent = ncomp if component == "mag" or ncomp == 1 else int(component)  # ncomp: magnitude
    lower, upper = props["lower"], props["upper"]
    proxy.LowerThreshold = -_BIG if lower is None else lower
    proxy.UpperThreshold = _BIG if upper is None else upper
    proxy.ThresholdMethod = "Between"


def _set_calculator(proxy, props, ctx):
    proxy.Function = props["expression"]
    proxy.ResultArrayName = props["result"]


def _set_glyph(proxy, props, ctx):
    proxy.OrientationArray = [ctx.association, ctx.field_of(props["vectors"])]
    proxy.ScaleArray = ["POINTS", "No scale array"]
    proxy.MaximumNumberOfSamplePoints = props["max_glyphs"]
    if props["scale"] is not None:
        proxy.ScaleFactor = props["scale"]


def _set_contour(proxy, helpers, props, ctx):
    field = ctx.field_of(props["field"])
    helpers[1].Function = _expression(field, ctx.component_of(props["component"]))
    proxy.ContourBy = ["POINTS", "_value"]
    proxy.Isosurfaces = props["values"]


def _set_stream_tracer(proxy, helpers, props, ctx):
    b = ctx.bounds
    proxy.Vectors = ["POINTS", ctx.field_of(props["vectors"])]
    proxy.SeedType.Point1, proxy.SeedType.Point2 = _at(b, props["start"]), _at(b, props["end"])
    proxy.SeedType.Resolution = props["seeds"]
    proxy.MaximumStreamlineLength = max(_extents(b))


_SETTERS = {"Slice": _set_slice, "Clip": _set_clip, "Threshold": _set_threshold, "Calculator": _set_calculator,
            "Glyph": _set_glyph}
_HELPED_SETTERS = {"Contour": _set_contour, "StreamTracer": _set_stream_tracer}


# -- displays: colours, colour bars -----------------------------------------------------------


def _colour_range(display, props, field, index):
    """A fixed ``range``, else the field's global range in the grid (or in the display's input)."""
    if props["range"] != "auto":
        return tuple(props["range"])
    in_grid = display.association == "CELLS" and _arrays(grid, "CELLS") is not None \
        and _arrays(grid, "CELLS").GetArray(field) is not None
    if in_grid:
        return _range(grid, "CELLS", field, index)
    display.rep.Input.UpdatePipeline(display.time)  # before the render: its output is still the last one
    return _range(display.rep.Input, display.association, field, index)


def _lut_of(display, props, ctx):
    """Colour ``display`` by its field (or solid); the colour map and its bar's title, or ``None``."""
    rep = display.rep
    rep.Opacity = props["opacity"]
    rep.LineWidth = props["line_width"]
    rep.AmbientColor = rep.DiffuseColor = props["color"]
    field = ctx.field_of(props["field"])
    if field is None:
        rep.ColorArrayName = ["POINTS", ""]
        return None
    component, ncomp = ctx.component_of(props["component"]), _ncomp(field)
    comp = None if ncomp == 1 else component
    ColorBy(rep, (display.association, field) if comp is None else
            (display.association, field, _COMPONENT.get(comp, "Magnitude")), separate=True)
    lut = rep.LookupTable  # the display's own (separate) colour map
    lut.ApplyPreset(_CMAPS[props["colormap"]], True)
    lut.NanColor = [0.75, 0.75, 0.75]  # cells of a region without this field (merged regions)
    lo, hi = _colour_range(display, props, field, -1 if comp in (None, "mag") else int(comp))
    hi = hi if hi > lo else lo + 1e-30
    log = props["log"] and lo > 0
    if props["log"] and not log:
        report["notes"].append("log scale off: the colour range reaches 0 or below")
    lut.UseLogScale = 0  # a log scale would clamp a range reaching 0 or below
    lut.RescaleTransferFunction(lo, hi)
    lut.UseLogScale = int(log)
    return lut, _title(field, comp)


def _title(field, comp):
    """A colour bar's title: ``p``, ``|U|`` or ``U.x``."""
    if comp is None:
        return field
    if comp != "mag":
        return f"{field}.{'xyz'[int(comp)]}"
    # VTK's matplotlib text takes "|" for a table cell separator: draw it as math there
    math = vtkMathTextUtilities.GetInstance()
    return f"$\\vert${field}$\\vert$" if math is not None and math.IsAvailable() else f"|{field}|"


#: A colour bar's height (title, bar, labels) in pixels at scale 1: the camera keeps room for each.
_BAR_PX = 110
#: The gap between the cells of a layout in pixels at scale 1.
_SEPARATOR_PX = 3


def _colour_bar(lut, title, ctx, view, row):
    """The colour bar of ``lut`` in ``view``, in ``row`` (0: the lowest) of the bars stacked at the bottom."""
    scale = ctx.scale
    height = view.ViewSize[1]
    bar = GetScalarBar(lut, view)
    bar.Title = title
    bar.ComponentTitle = ""
    bar.Orientation = "Horizontal"
    bar.WindowLocation = "Any Location"
    bar.Position = [0.25, 0.02 + row * _BAR_PX * scale / height]
    bar.ScalarBarLength = 0.5
    bar.TitleFontSize = round(22 * scale)
    bar.LabelFontSize = round(20 * scale)
    bar.ScalarBarThickness = round(16 * scale)
    bar.Visibility = 1


class _Display:
    """A Display node's representation, the association of the arrays it colours by, the time."""

    def __init__(self, rep, association, time):
        self.rep = rep
        self.association = association
        self.time = time


def _show_displays(scene, ctx):
    """Colour the displays; those linked to the View show, each coloured one with its bar (``bar``)
    in its cell. How many bars each cell has."""
    graph, sources = scene.graph, inputs(scene.graph)
    linked, index = cell_of(graph), _cell_index(graph)
    bars = [[] for _ in scene.views]
    for node in ordered(graph):
        if node["type"] != "Display":
            continue
        scene.failing = node
        rep = scene.proxies[node["id"]]
        rep.Visibility = int(node["id"] in linked)
        display = _Display(rep, _association(scene, sources[node["id"]][0]), ctx.time)
        coloured = _lut_of(display, node["props"], ctx)
        if coloured is not None and rep.Visibility and node["props"]["bar"]:
            bars[index[node["id"]]].append(coloured)
    shown = {(lut, i) for i, cell in enumerate(bars) for lut, _ in cell}
    for lut, i in scene.bars - shown:
        GetScalarBar(lut, scene.views[i]).Visibility = 0
    for i, cell in enumerate(bars):
        for row, (lut, title) in enumerate(cell):
            _colour_bar(lut, title, ctx, scene.views[i], row)
    scene.bars |= shown
    return [len(cell) for cell in bars]


def _cell_index(graph):
    """Each Display's cell, as an index into the View's layout (one not linked: the first)."""
    view = next((n for n in graph["nodes"] if n["type"] == "View"), None)
    if view is None:
        return {}
    names, linked = cells(view["props"].get("layout", "1")), cell_of(graph)
    return {n["id"]: names.index(linked.get(n["id"], names[0])) for n in graph["nodes"] if n["type"] == "Display"}


def _association(scene, node_id):
    """Where a node's output holds the fields: cells, or points after a Glyph or a StreamTracer."""
    sources = inputs(scene.graph)
    while True:
        kind = next(n["type"] for n in scene.graph["nodes"] if n["id"] == node_id)
        if kind in ("Glyph", "StreamTracer"):
            return "POINTS"
        if not sources[node_id]:
            return "CELLS"
        node_id = sources[node_id][0]


# -- image size and camera ---------------------------------------------------------------------


def _image_size(params, b):
    """``width``/``height``, else 1000 wide and the domain's aspect (room for the colour bar)."""
    if params.get("width") and params.get("height"):
        return [max(64, min(int(params["width"]), 4096)), max(64, min(int(params["height"]), 4096))]
    extent = _extents(b)
    thin = extent.index(min(extent))
    up_axis = 1 if thin != 1 else 2
    side = next(a for a in range(3) if a not in (thin, up_axis))
    aspect = extent[up_axis] / max(extent[side], 1e-30)
    return [1000, int(min(max(1000 * aspect * 1.15 + 130, 280), 1000))]


def _fit_camera(view, pick, b, size, bar_px):
    """Look from the preset's side, the domain filling the image above the colour bars, zoomed."""
    fit = camera_fit(pick[0], pick[1], b, size, bar_px)
    view.CameraFocalPoint = fit["focal"]
    view.CameraPosition = fit["position"]
    view.CameraViewUp = fit["up"]
    view.CameraParallelProjection = 1
    view.CameraParallelScale = fit["scale"]


def _size_views(scene, ctx):
    """The image size: one view's, or the layout's split into its cells; each view's size."""
    if len(scene.views) == 1:
        scene.view.ViewSize = ctx.size
        return [list(ctx.size)]
    scene.layout.SeparatorWidth = max(1, round(_SEPARATOR_PX * ctx.scale))
    scene.layout.SetSize(*ctx.size)
    return [list(view.ViewSize) for view in scene.views]


def _pick(props, cell):
    """The camera preset and zoom of a cell: its own (per cell, if it has one), else the View's."""
    own = props["cell_cameras"].get(cell) if props["cameras"] == "per_cell" else None
    return (own["camera"], float(own["zoom"])) if own else (props["camera"], float(props["zoom"]))


def _place_camera(scene, ctx, sizes, bars):
    """Each cell's camera from the View node, fitted again when it, the cell's size or its bars change.

    The View node defines the camera entirely: a camera fitted for another size (the pane
    resized, the scene shown again) would crop the domain. Linked cells share one camera,
    fitted to the first cell with room for the most bars any cell has."""
    props = next(n["props"] for n in scene.graph["nodes"] if n["type"] == "View")
    names, linked = cells(props["layout"]), props["cameras"] == "linked"
    for i, view in enumerate(scene.views):
        pick, size = _pick(props, names[i]), sizes[0] if linked else sizes[i]
        rows = max(bars) if linked else bars[i]
        camera = (pick, size, rows)
        if scene.cameras.get(i) != camera:
            scene.cameras[i] = camera
            _fit_camera(view, pick, ctx.bounds, size, _BAR_PX * ctx.scale * max(rows, 1))


# -- scenes ------------------------------------------------------------------------------------


def _new_view(name, index):
    view = CreateView("RenderView", registrationName=f"{name}:view" + (f"{index}" if index else ""))
    view.OrientationAxesVisibility = 0
    return view


def _locations(layout, name):
    """Split an empty ParaView layout into the cells of the View ``layout`` ``name``: their locations, row by row."""
    rows, columns = shape(name)
    if rows == columns == 1:
        return [0]
    if rows == 1 or columns == 1:
        first = layout.SplitHorizontal(0, 0.5) if rows == 1 else layout.SplitVertical(0, 0.5)
        return [first, first + 1]
    top = layout.SplitVertical(0, 0.5)
    upper, lower = layout.SplitHorizontal(top, 0.5), layout.SplitHorizontal(top + 1, 0.5)
    return [upper, upper + 1, lower, lower + 1]


class _Scene:
    """One scene as ParaView proxies: its graph, a proxy per node (and helpers), a render view per cell."""

    def __init__(self, name):
        self.name = name
        self.graph = {"nodes": [], "edges": []}
        #: Node id → its proxy: grid → the producer, a filter → its filter, Display → its
        #: representation, View → the first cell's render view (:attr:`views`: all).
        self.proxies = {}
        self.helpers = {}
        self.kinds = {}
        #: The graph text it was built from (``""``: the built-in Field).
        self.text = None
        #: A render view per cell, row by row; with more than one, arranged in :attr:`layout`.
        self.views = [_new_view(name, 0)]
        self.layout = None
        self.arranged = "1"
        #: Each view's last camera fit (by cell index) and the colour bars shown so far (lut, cell index).
        self.cameras = {}
        self.bars = set()
        #: The node being worked on (named by an error).
        self.failing = None

    @property
    def view(self):
        """The first cell's render view."""
        return self.views[0]

    def apply(self, graph, ctx):
        """Build ``graph`` from what is built: the cells' views, new nodes, relinked inputs, removed
        nodes (last), the views of cells gone.

        A filter's properties are set as it is made: a display updates its input when created. A
        Display in another cell than before is made again in that cell's view."""
        new = {n["id"]: n["type"] for n in graph["nodes"]}
        old_cells, new_cells = _cell_index(self.graph), _cell_index(graph)
        moved = {k for k, i in old_cells.items() if new_cells.get(k, i) != i}
        gone = [(n, self.proxies.pop(n["id"]), self.helpers.pop(n["id"], []), self.kinds.pop(n["id"]))
                for n in reversed(ordered(self.graph)) if new.get(n["id"]) != n["type"] or n["id"] in moved]
        self.graph = graph
        self._arrange(graph)
        sources = inputs(graph)
        for node in ordered(graph):
            self.failing = node
            upstream = [self.proxies[s] for s in sources[node["id"]]]
            if node["id"] in self.proxies:
                _relink(self, node, upstream)
            else:
                _create(self, node, upstream)
            self._set(node, sources, ctx)
        for node, proxy, helpers, _ in gone:
            self.failing = node
            _delete(self, node, proxy, helpers)
        self.failing = None
        self._drop_views()

    def _arrange(self, graph):
        """A view for each cell of the View's layout, the views in a ParaView layout (more than one cell)."""
        name = next(n["props"].get("layout", "1") for n in graph["nodes"] if n["type"] == "View")
        count = len(cells(name))
        while len(self.views) < count:
            self.views.append(_new_view(self.name, len(self.views)))
        if name == self.arranged:
            return
        self.arranged = name
        if self.layout is None:
            self.layout = CreateLayout(f"{self.name}:layout")  # traced by an export; captured as one picture
        self.layout.SMProxy.Reset()
        for view, location in zip(self.views, _locations(self.layout, name)):
            self.layout.AssignView(location, view)

    def _drop_views(self):
        """Delete the views of cells the layout no longer has (their displays went already)."""
        count = len(cells(self.arranged))
        for i in range(len(self.views) - 1, count - 1, -1):
            Delete(self.views.pop(i))
            self.cameras.pop(i, None)
        self.bars = {(lut, i) for lut, i in self.bars if i < count}

    def set_properties(self, ctx):
        """Every filter's properties (those of the pane's field may change at any execute)."""
        sources = inputs(self.graph)
        for node in ordered(self.graph):
            self.failing = node
            self._set(node, sources, ctx)
        self.failing = None

    def _set(self, node, sources, ctx):
        kind, proxy = node["type"], self.proxies[node["id"]]
        _check_fields(self, node, ctx)
        if sources[node["id"]]:
            ctx.association = _association(self, sources[node["id"]][0])
        if kind in _SETTERS:
            _SETTERS[kind](proxy, node["props"], ctx)
        elif kind in _HELPED_SETTERS:
            _HELPED_SETTERS[kind](proxy, self.helpers[node["id"]], node["props"], ctx)

    def displays(self):
        """The Display nodes linked to the View, each with the node it draws."""
        sources = inputs(self.graph)
        view = next(n["id"] for n in self.graph["nodes"] if n["type"] == "View")
        return [(n, sources[n["id"]][0]) for n in ordered(self.graph)
                if n["type"] == "Display" and n["id"] in sources[view]]

    def source_name(self, node_id):
        """The registration name of a node's proxy (what Catalyst Live extracts by)."""
        return "grid" if self.proxies[node_id] is grid else f"{self.name}:{node_id}"

    def proxy_ids(self):
        """Each node's proxies as the pipeline diagram names them (``pv<global id>``): its timings
        (the View's: every cell's view)."""
        out = {}
        for node_id, proxy in self.proxies.items():
            proxies = self.views if self.kinds[node_id] == "View" else [proxy, *self.helpers.get(node_id, [])]
            out[node_id] = ["pv" + p.GetGlobalIDAsString() for p in proxies]
        return out

    def cell_ids(self):
        """Each cell's render view as the pipeline diagram names it: the cell's timing."""
        return {cell: "pv" + view.GetGlobalIDAsString() for cell, view in zip(cells(self.arranged), self.views)}

    def close(self):
        """Delete every proxy built (consumers first), the views and the layout."""
        for node_id, proxy in reversed(list(self.proxies.items())):
            _delete(self, {"type": self.kinds[node_id]}, proxy, self.helpers.get(node_id, []))
        for view in self.views:
            Delete(view)
        if self.layout is not None:
            pxm = servermanager.ProxyManager()
            pxm.UnRegisterProxy("layouts", pxm.GetProxyName("layouts", self.layout), self.layout)


def _graph_of(params):
    """The shown scene's name and graph: the built-in ``Field``, or the one in ``graph``."""
    name = params.get("scene") or FIELD
    if not params.get("graph"):
        return name, field_graph()
    return name, parse(params["graph"]).graph()


def _failure(scene_name, node, exc):
    if node:
        report["error_node"] = node["id"]
    where = f"node {node['id']!r} ({node['type']}): " if node else ""
    return f"scene {scene_name!r}: {where}{exc}"


def _check_fields(scene, node, ctx):
    """A field a node reads by name must be in the grid or made upstream (a Calculator's result)."""
    props = node["props"]
    named = [props.get(k) for k in ("field", "vectors") if k in props]
    names = [ctx.field_of(v) for v in named if v is not None and v != "@field"]
    if not names:
        return
    arrays = _arrays(grid, "CELLS")
    known = {arrays.GetArrayName(i) for i in range(arrays.GetNumberOfArrays())} if arrays is not None else set()
    known |= _results_upstream(scene, node["id"])
    missing = [n for n in names if n not in known]
    if missing and arrays is not None:
        raise ValueError(f"no field {missing[0]!r} (fields: {', '.join(sorted(known))})")


def _results_upstream(scene, node_id):
    """The fields Calculators upstream of a node add."""
    sources, by_id, out = inputs(scene.graph), {n["id"]: n for n in scene.graph["nodes"]}, set()
    todo = list(sources[node_id])
    while todo:
        up = by_id[todo.pop()]
        if up["type"] == "Calculator":
            out.add(up["props"]["result"])
        todo += sources[up["id"]]
    return out


def _scene(params, ctx):
    """The shown scene, built or brought to its graph; ``None`` and ``report["error"]`` if it cannot be.

    A graph that is not a scene is refused and the scene stays as it was built; one that
    fails to build drops the scene."""
    name, text = params.get("scene") or FIELD, params.get("graph", "")
    if name in _failed and _failed[name][0] == text:
        report["error"], node = _failed[name][1:]
        if node:
            report["error_node"] = node
        return None
    scene = _scenes.get(name)
    if scene is not None and scene.text == text:
        _failed.pop(name, None)
        return scene
    try:
        graph = _graph_of(params)[1]
    except (ValueError, ImportError) as exc:  # not a scene (the message names the node), or no pydantic
        if getattr(exc, "node", None):
            report["error_node"] = exc.node
        _failed[name] = (text, report.setdefault("error", f"scene {name!r}: {exc}"), report.get("error_node"))
        return None
    try:
        if scene is None:
            scene = _scenes[name] = _Scene(name)
        scene.apply(graph, ctx)
        scene.text = text
        _failed.pop(name, None)
        return scene
    except Exception as exc:  # noqa: BLE001 - a broken scene leaves the others working
        _drop(name, scene, text, exc)
        return None


def _drop(name, scene, text, exc):
    """Forget a scene that failed (its proxies go); remember why, for as long as its graph stays."""
    error = _failure(name, scene.failing if scene is not None else None, exc)
    _failed[name] = (text, error, report.get("error_node"))
    report["error"] = error
    if _scenes.pop(name, None) is not None:
        scene.close()


def _show(scene, params, ctx):
    """Draw the scene in its views (image mode); a failure drops it."""
    try:
        scene.set_properties(ctx)
        sizes = _size_views(scene, ctx)
        _place_camera(scene, ctx, sizes, _show_displays(scene, ctx))
        return True
    except Exception as exc:  # noqa: BLE001 - a broken scene leaves the others working
        _drop(scene.name, scene, params.get("graph", ""), exc)
        return False


def _save_layout_png(layout, size, path):
    """A ParaView layout as one PNG of ``size`` at ``path``: every rank renders each view (collective;
    IceT composites each on rank 0), rank 0 merges the cells and writes the file.

    Self-contained: an exported scene's script carries its source."""
    import os  # noqa: PLC0415 - the function travels into exported scripts as source
    from paraview.modules.vtkRemotingViews import vtkSMSaveScreenshotProxy  # noqa: PLC0415
    from vtkmodules.vtkCommonDataModel import vtkVector2i  # noqa: PLC0415
    from vtkmodules.vtkIOImage import vtkPNGWriter  # noqa: PLC0415
    from vtkmodules.vtkParallelCore import vtkMultiProcessController  # noqa: PLC0415

    layout.SetSize(*size)
    image = vtkSMSaveScreenshotProxy.CaptureImage(layout.SMProxy, vtkVector2i(*size))
    controller = vtkMultiProcessController.GetGlobalController()
    if controller is not None and controller.GetLocalProcessId() > 0:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    writer = vtkPNGWriter()
    writer.SetFileName(path)
    writer.SetInputData(image)
    writer.Write()


def _write_out(scene, ctx, timestep):
    """``out=<dir>``: ``view_<timestep>.png`` of the shown scene, all its cells in one picture.

    One cell: a PNG extractor of its view (Catalyst writes it after the execute); a layout: the
    picture written now, the extractor off (one of a layout writes nothing; deleting one that
    Catalyst knows aborts its next execute)."""
    global png
    if "out" not in _args:
        return
    layout = len(scene.views) > 1
    if png is None and not layout:
        png = CreateExtractor("PNG", scene.view, registrationName="png")
        png.Writer.FileName = "view_{timestep:06d}.png"
    if png is not None:
        png.Enable = int(not layout)
        png.Producer = scene.view
        png.Writer.ImageResolution = list(ctx.size)
    if layout:
        _save_layout_png(scene.layout, list(ctx.size), os.path.join(_args["out"], f"view_{timestep:06d}.png"))


def catalyst_execute(info):
    global shown_view, shown_layout
    params = _params(info)
    # Bring the producer to this execute's data first: until then its output still holds the
    # previous step's arrays, whose memory the solver may have freed (nut is reallocated).
    grid.UpdatePipeline(info.time)
    data = params.get("mode") == "data"
    if _data and not _live:
        options.EnableCatalystLive = int(data)
    b = _domain()
    report.clear()
    report.update(bounds=list(b), axis=_default_axis(b), notes=[], scene=params.get("scene") or FIELD)
    shown_view = shown_layout = None
    ctx = _Context(params, b, info.time)
    scene = _scene(params, ctx)
    if scene is None:
        return
    if data:  # Live sends the extracted source as it is (no update of its own); the UI draws it
        _extract(scene, params, ctx, info.time)
        return
    if _show(scene, params, ctx):
        shown_view = scene.view
        shown_layout = scene.layout if len(scene.views) > 1 else None
        _write_out(scene, ctx, info.timestep)
        report["proxies"], report["cells"] = scene.proxy_ids(), scene.cell_ids()
        if params.get("export"):
            _export(scene, params["export"], ctx)


def _extract(scene, params, ctx, time):
    """Data mode: bring each Display's input up to date (Live sends those the UI asks for); the
    report names them with the displays' settings, the pane's field bound (``extracts``)."""
    try:
        scene.set_properties(ctx)
    except Exception as exc:  # noqa: BLE001 - a broken scene leaves the others working
        _drop(scene.name, scene, params.get("graph", ""), exc)
        return
    report["extracts"], updated = [], set()
    for display, source in scene.displays():
        props = display["props"]
        report["extracts"].append({**props, "id": display["id"], "source": scene.source_name(source),
                                   "field": ctx.field_of(props["field"]),
                                   "component": ctx.component_of(props["component"])})
        if source not in updated:
            updated.add(source)
            scene.proxies[source].UpdatePipeline(time)


def _catalyst_state(export, views):
    """ParaView's Catalyst export of the extractors (``catalyst_export``), with the views and their
    layout: its exporter leaves layouts out, and an extractor of a layout reaches no view."""
    from paraview import simple, smstate  # noqa: PLC0415 - only for an export
    from paraview.detail.catalyst_export import _get_catalyst_postamble, _get_catalyst_preamble  # noqa: PLC0415

    options = servermanager._getPyProxy(servermanager.ProxyManager().NewProxy("pythontracing", "PythonStateOptions"))
    options.PropertiesToTraceOnCreate = smstate.RECORD_ACTIVE_MODIFIED_PROPERTIES
    options.SkipHiddenDisplayProperties = True
    options.SkipRenderingComponents = False
    options.SkipActiveComponents = True
    options.SkipLayoutComponents = False
    options.ExtractsOutputDirectory = export.ExtractsOutputDirectory
    return smstate.get_state(options=options, source_set=[*simple.GetExtractors(), *views],
                             preamble=_get_catalyst_preamble(export), postamble=_get_catalyst_postamble(export))


def _export_png(name, view, size):
    """A PNG extractor of a view, ``<scene>_<timestep>.png``."""
    # not through CreateExtractor: in situ it also tags the extractor for Catalyst, which would
    # still write it after it is deleted (and fail on its writer)
    extractor = servermanager._getPyProxy(servermanager.vtkSMExtractsController().CreateExtractor(
        view.SMProxy, "PNG", f"{name}:png"))
    extractor.Writer.FileName = name + "_{timestep:06d}.png"
    extractor.Writer.ImageResolution = list(size)
    return extractor


def _layout_hook(scene, size):
    """What an exported layout adds to its script: the layout written as ``<scene>_<timestep>.png``,
    one picture per step, by :func:`_save_layout_png` (its source), in situ at each execute and
    once in pvbatch."""
    pxm = servermanager.ProxyManager()
    layout, out = pxm.GetProxyName("layouts", scene.layout), f"{scene.name}_{{:06d}}.png"
    path = f"os.path.join(options.ExtractsOutputDirectory, {out!r}.format(%s))"
    return "\n".join([
        "# " + "-" * 78,
        "# The layout as one picture per step (ParaView's image extractors take a single view)",
        "import os", "", "",
        inspect.getsource(_save_layout_png).rstrip(), "", "",
        "def catalyst_execute(info):",
        f"    _save_layout_png(GetLayoutByName({layout!r}), {list(size)!r}, {path % 'info.timestep'})", "", "",
        "if __name__ == '__main__':",
        f"    _save_layout_png(GetLayoutByName({layout!r}), {list(size)!r}, {path % '0'})", ""])


def _export(scene, path, ctx):
    """Write the scene as a Catalyst script (ParaView's exporter), with a PNG extractor of its view;
    more than one cell: the script builds the views in their layout too and writes the layout as
    one picture per step (ParaView's image extractors take a view, not a layout).

    Every rank builds the state (it reads the proxies only); rank 0 writes the file. The
    extractor exists only while the state is saved, so nothing is written now."""
    from paraview.detail.catalyst_export import _get_catalyst_state  # noqa: PLC0415 - only for an export

    layout = len(scene.views) > 1
    extractors = [] if layout else [_export_png(scene.name, scene.view, ctx.size)]
    export = catalyst.Options()
    export.ExtractsOutputDirectory = f"postProcessing/{scene.name}"
    for lut, _ in scene.bars:  # a range the builder set each frame: the script rescales on its own
        lut.AutomaticRescaleRangeMode = "Grow and update every timestep"
    try:
        options = servermanager._getPyProxy(export)
        state = _catalyst_state(options, scene.views) if layout else _get_catalyst_state(options)
    finally:
        for extractor in extractors:
            Delete(extractor)
        Delete(export)
    if layout:
        state += "\n\n" + _layout_hook(scene, ctx.size)
    controller = vtkMultiProcessController.GetGlobalController()
    if controller is None or controller.GetLocalProcessId() == 0:
        with open(path, "w") as out:
            out.write(state + "\n")
    report["exported"] = path
