"""Catalyst 2 pipeline of the stepper's field view (runs inside the solver, all ranks).

Channel ``grid``: the solver's cells with every cell field. Parameters per
execute (``catalyst/state/parameters``): ``field=<name>`` and
``component=mag|0|1|2``. Writes ``view_<timestep>.png`` of the whole domain
to the directory given as the script's first argument. Set
``NEOFOAM_UI_CATALYST_LIVE=host:port`` to let ParaView attach (Catalyst ▸ Connect).
"""

import os

from paraview import catalyst
from paraview.simple import (ColorBy, CreateExtractor, CreateView, ExtractSurface,
                             GetColorTransferFunction, GetScalarBar, Show, TrivialProducer)

_args = catalyst.get_args()

options = catalyst.Options()
options.ExtractsOutputDirectory = _args[0] if _args else "."
_live = os.environ.get("NEOFOAM_UI_CATALYST_LIVE", "")
if _live and _live != "off":
    options.EnableCatalystLive = 1
    options.CatalystLiveURL = _live

grid = TrivialProducer(registrationName="grid")
surface = ExtractSurface(registrationName="surface", Input=grid)
view = CreateView("RenderView")
view.ViewSize = [1280, 640]
view.OrientationAxesVisibility = 0
display = Show(surface, view)
png = CreateExtractor("PNG", view, registrationName="png")
png.Writer.FileName = "view_{timestep:06d}.png"
png.Writer.ImageResolution = [1280, 640]

_COMPONENT = {"mag": "Magnitude", "0": "X", "1": "Y", "2": "Z"}


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
    from vtkmodules.vtkCommonCore import vtkDoubleArray
    from vtkmodules.vtkParallelCore import vtkCommunicator, vtkMultiProcessController

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
_camera_set = [False]


def _params(info):
    out = {}
    for item in info.catalyst_params:
        key, _, value = str(item).partition("=")
        out[key] = value
    return out


def _fit_camera():
    """Look along the thinnest axis (a 2D case is one cell thick); image aspect = domain aspect."""
    b = _bounds()
    extent = [b[1] - b[0], b[3] - b[2], b[5] - b[4]]
    thin = extent.index(min(extent))
    centre = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2]
    across = [a for a in range(3) if a != thin]
    up_axis = 1 if thin != 1 else 2
    side = next(a for a in across if a != up_axis)
    aspect = extent[up_axis] / max(extent[side], 1e-30)
    width = 1000
    height = int(min(max(width * aspect * 1.15 + 130, 280), 1000))  # room for the colour bar
    view.ViewSize = [width, height]
    png.Writer.ImageResolution = [width, height]
    position = list(centre)
    position[thin] += 10 * max(extent)
    up = [0.0, 0.0, 0.0]
    up[up_axis] = 1.0
    view.CameraFocalPoint = centre
    view.CameraPosition = position
    view.CameraViewUp = up
    view.CameraParallelProjection = 1
    bar_px = 110
    scale = 1.04 * max(extent[up_axis] / 2 * height / (height - bar_px), extent[side] / 2 * height / width)
    view.CameraParallelScale = scale
    shift = bar_px / 2 * 2 * scale / height  # centre the mesh above the colour bar
    focal = list(centre)
    focal[up_axis] -= shift
    position[up_axis] -= shift
    view.CameraFocalPoint = focal
    view.CameraPosition = position


def catalyst_execute(info):
    params = _params(info)
    local = _local()
    local = local.GetCellData() if local is not None else None
    names = [local.GetArrayName(i) for i in range(local.GetNumberOfArrays())] if local is not None else []
    field = params.get("field")
    if field not in names:
        field = next((n for n in ("U", "p", "T") if n in names), names[0] if names else None)
    if field is None:
        return
    ncomp = local.GetArray(field).GetNumberOfComponents()
    comp = params.get("component", "mag") if ncomp > 1 else None
    if comp is None:
        ColorBy(display, ("CELLS", field))
    else:
        ColorBy(display, ("CELLS", field, _COMPONENT.get(comp, "Magnitude")))
    lut = GetColorTransferFunction(field)
    lut.ApplyPreset("Viridis (matplotlib)", True)
    index = -1 if comp in (None, "mag") else int(comp)
    lo, hi = _range(field, index)
    lut.RescaleTransferFunction(lo, hi if hi > lo else lo + 1e-30)
    bar = GetScalarBar(lut, view)
    bar.Title = field if comp is None else (f"|{field}|" if comp == "mag" else f"{field}.{'xyz'[int(comp)]}")
    bar.ComponentTitle = ""
    bar.Orientation = "Horizontal"
    bar.WindowLocation = "Lower Center"
    bar.ScalarBarLength = 0.5
    bar.TitleFontSize = 22
    bar.LabelFontSize = 20
    bar.Visibility = 1
    if not _camera_set[0]:
        _fit_camera()
        _camera_set[0] = True
