"""Catalyst 2 pipeline of the stepper's field view (runs inside the solver, all ranks).

Channel ``grid``: the solver's cells with every cell field. Parameters per execute
(``catalyst/state/parameters``, all optional but ``field``):

* ``field=<name>``, ``component=mag|0|1|2``, ``mode=image|data``;
* ``show=surface|slice|clip|volume``, ``axis=x|y|z`` (default: the longest in-plane axis of a
  one-cell-thick case, else z), ``pos=<0..1 along the axis>``, ``flip=1`` (clip keeps the
  upper side);
* image size ``width``/``height`` and ``scale`` (device pixels per CSS pixel: fonts grow);
  ``camera=reset|+x|-x|+y|-y|+z|-z`` (look from there, fitted) and ``zoom``; a new size
  refits only the ``reset`` camera at zoom 1;
* ``range=lo:hi`` (else the global data range), ``cmap=<name>`` (``_CMAPS``), ``log=1``.

With a directory as the script's first argument it writes ``view_<timestep>.png`` of the
whole domain there; the caller may capture the view as well. ``report`` tells the caller
of the last execute: the domain ``bounds``, the default ``axis`` and ``notes``.
``NEOFOAM_UI_CATALYST_LIVE=host:port`` lets a ParaView attach (Catalyst ▸ Connect) at every
execute; ``NEOFOAM_UI_CATALYST_DATA=host:port`` (the stepper UI's data mode) publishes to
Live only in ``mode=data``, where the shown source is updated but nothing is drawn.
"""

import os

from paraview import catalyst
from paraview.simple import (Clip, ColorBy, CreateExtractor, CreateView, ExtractSurface,
                             GetColorTransferFunction, GetScalarBar, Hide, Show, Slice, TrivialProducer)
from vtkmodules.vtkCommonCore import vtkDoubleArray
from vtkmodules.vtkParallelCore import vtkCommunicator, vtkMultiProcessController

_args = catalyst.get_args()

options = catalyst.Options()
_live = os.environ.get("NEOFOAM_UI_CATALYST_LIVE", "off")
_live = "" if _live == "off" else _live
_data = os.environ.get("NEOFOAM_UI_CATALYST_DATA", "")
if _live or _data:
    options.CatalystLiveURL = _live or _data
    options.EnableCatalystLive = int(bool(_live))

grid = TrivialProducer(registrationName="grid")
surface = ExtractSurface(registrationName="surface", Input=grid)
cut = Slice(registrationName="slice", Input=grid)
cut.SliceType = "Plane"
clip = Clip(registrationName="clip", Input=grid)
clip.ClipType = "Plane"
#: What ``show`` draws (and what data mode updates for Live to extract).
_SOURCES = {"surface": surface, "slice": cut, "clip": clip, "volume": grid}
view = CreateView("RenderView")
view.ViewSize = [1280, 640]
view.OrientationAxesVisibility = 0
_displays = {"surface": Show(surface, view)}
png = None
if _args:
    options.ExtractsOutputDirectory = _args[0]
    png = CreateExtractor("PNG", view, registrationName="png")
    png.Writer.FileName = "view_{timestep:06d}.png"
    png.Writer.ImageResolution = [1280, 640]

_COMPONENT = {"mag": "Magnitude", "0": "X", "1": "Y", "2": "Z"}
_CMAPS = {"viridis": "Viridis (matplotlib)", "coolwarm": "Cool to Warm", "inferno": "Inferno (matplotlib)",
          "jet": "Jet", "grayscale": "Grayscale"}
_AXES = "xyz"
#: What the caller reads after an execute (see the module docstring).
report = {}
_state = {"bounds": None, "camera": None, "shown": None, "bar": None}


def _local():
    """This rank's part of the grid (the TrivialProducer's output on this process)."""
    data = grid.GetClientSideObject().GetOutputDataObject(0)
    if data.IsA("vtkPartitionedDataSet"):  # a conduit mesh arrives as one partition per rank
        data = data.GetPartition(0) if data.GetNumberOfPartitions() else None
    return data


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
    big = 1e300
    lo, hi = _global([big if empty else b[i] for i in (0, 2, 4)],
                     [-big if empty else b[i] for i in (1, 3, 5)])
    return [lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]]


def _range(field, index):
    local = _local()
    data = local.GetCellData().GetArray(field) if local is not None else None
    big = 1e300
    lo, hi = big, -big
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


def _place_cut(params, b):
    """Put the slice and clip planes where ``axis`` and ``pos`` say; the name of the shown source."""
    show = params.get("show", "surface")
    if show not in _SOURCES:
        show = "surface"
    axis = _AXES.index(params.get("axis") if params.get("axis") in ("x", "y", "z") else _default_axis(b))
    pos = min(max(float(params.get("pos", 0.5)), 0.0), 1.0)
    origin = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2]
    origin[axis] = b[2 * axis] + pos * (b[2 * axis + 1] - b[2 * axis])
    normal = [0.0, 0.0, 0.0]
    normal[axis] = 1.0
    cut.SliceType.Origin, cut.SliceType.Normal = origin, normal
    clip.ClipType.Origin, clip.ClipType.Normal = origin, normal
    clip.Invert = 0 if params.get("flip") == "1" else 1  # inverted: the lower side stays
    return show


def _display(show):
    """Show only the source of ``show`` (volume in an image: its surface)."""
    name = "surface" if show == "volume" else show
    for other in _displays:
        if other != name:
            Hide(_SOURCES[other], view)
    _displays[name] = Show(_SOURCES[name], view)
    return _displays[name]


def _field_of(params, local):
    names = [local.GetArrayName(i) for i in range(local.GetNumberOfArrays())] if local is not None else []
    field = params.get("field")
    if field not in names:
        field = next((n for n in ("U", "p", "T") if n in names), names[0] if names else None)
    return field


def _colour_range(params, field, index):
    """``range=lo:hi``, else the global data range."""
    lo, _, hi = params.get("range", "").partition(":")
    try:
        return float(lo), float(hi)
    except ValueError:
        return _range(field, index)


def _colour(display, params, field, ncomp):
    """Colour ``display`` by ``field``: component, map, range, log scale, one colour bar."""
    comp = params.get("component", "mag") if ncomp > 1 else None
    if comp is None:
        ColorBy(display, ("CELLS", field))
    else:
        ColorBy(display, ("CELLS", field, _COMPONENT.get(comp, "Magnitude")))
    lut = GetColorTransferFunction(field)
    if _state["shown"] is not None and _state["shown"] != field:  # one colour bar: the previous field's goes
        GetScalarBar(GetColorTransferFunction(_state["shown"]), view).Visibility = 0
    _state["shown"] = field
    lut.ApplyPreset(_CMAPS.get(params.get("cmap"), _CMAPS["viridis"]), True)
    lut.NanColor = [0.75, 0.75, 0.75]  # cells of a region without this field (merged regions)
    lo, hi = _colour_range(params, field, -1 if comp in (None, "mag") else int(comp))
    hi = hi if hi > lo else lo + 1e-30
    log = params.get("log") == "1" and lo > 0
    if params.get("log") == "1" and not log:
        report["notes"].append("log scale off: the colour range reaches 0 or below")
    lut.UseLogScale = 0  # a log scale would clamp a range reaching 0 or below
    lut.RescaleTransferFunction(lo, hi)
    lut.UseLogScale = int(log)
    _colour_bar(lut, field, comp, float(params.get("scale", 1)))


def _colour_bar(lut, field, comp, scale):
    bar = GetScalarBar(lut, view)
    bar.Title = field if comp is None else (f"|{field}|" if comp == "mag" else f"{field}.{'xyz'[int(comp)]}")
    bar.ComponentTitle = ""
    bar.Orientation = "Horizontal"
    bar.WindowLocation = "Lower Center"
    bar.ScalarBarLength = 0.5
    bar.TitleFontSize = round(22 * scale)
    bar.LabelFontSize = round(20 * scale)
    bar.ScalarBarThickness = round(16 * scale)
    bar.Visibility = 1


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


def _look(camera, b):
    """The axis looked along and the side looked from for a ``camera`` preset."""
    if camera in ("+x", "-x", "+y", "-y", "+z", "-z"):
        return _AXES.index(camera[1]), 1.0 if camera[0] == "+" else -1.0
    extent = _extents(b)
    return extent.index(min(extent)), 1.0


def _fit_camera(params, b, size, scale):
    """Look from the preset's side, the domain filling the image above the colour bar, zoomed."""
    thin, sign = _look(params.get("camera", "reset"), b)
    extent = _extents(b)
    centre = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2]
    up_axis = 1 if thin != 1 else 2
    side = next(a for a in range(3) if a not in (thin, up_axis))
    width, height = size
    bar_px = 110 * scale
    fit = 1.04 * max(extent[up_axis] / 2 * height / max(height - bar_px, 1), extent[side] / 2 * height / width)
    zoom = max(float(params.get("zoom", 1)), 1e-3)
    shift = bar_px / 2 * 2 * fit / height / zoom  # centre the mesh above the colour bar
    focal = list(centre)
    focal[up_axis] -= shift
    position = list(focal)
    position[thin] += sign * 10 * max(extent)
    up = [0.0, 0.0, 0.0]
    up[up_axis] = 1.0
    view.CameraFocalPoint = focal
    view.CameraPosition = position
    view.CameraViewUp = up
    view.CameraParallelProjection = 1
    view.CameraParallelScale = fit / zoom


def _place_camera(params, b):
    """Image size and camera; a new size alone keeps a picked or zoomed camera (only resized)."""
    size = _image_size(params, b)
    scale = float(params.get("scale", 1))
    pick = (params.get("camera", "reset"), params.get("zoom", "1"))
    previous = _state["camera"]
    _state["camera"] = (pick, size)
    view.ViewSize = size
    if png is not None:
        png.Writer.ImageResolution = size
    if previous is None or previous[0] != pick or (previous[1] != size and pick == ("reset", "1")):
        _fit_camera(params, b, size, scale)


def catalyst_execute(info):
    params = _params(info)
    # Bring the producer to this execute's data first: until then its output still holds the
    # previous step's arrays, whose memory the solver may have freed (nut is reallocated).
    grid.UpdatePipeline(info.time)
    data = params.get("mode") == "data"
    if _data and not _live:
        options.EnableCatalystLive = int(data)
    b = _domain()
    report.clear()
    report.update(bounds=list(b), axis=_default_axis(b), notes=[])
    show = _place_cut(params, b)
    if data:  # Live sends the extracted source as it is (no update of its own); the UI draws it
        _SOURCES[show].UpdatePipeline(info.time)
        return
    local = _local()
    local = local.GetCellData() if local is not None else None
    field = _field_of(params, local)
    if field is None:
        return
    _colour(_display(show), params, field, local.GetArray(field).GetNumberOfComponents())
    _place_camera(params, b)
