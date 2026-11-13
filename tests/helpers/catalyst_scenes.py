"""Frames of four unit hexahedra along x (``T`` = -1, 0, 1, 2; ``U`` = (T + 2, 0, 0)) through the
stepper's pipeline, one per step (JSON list, second argument after the prefix: ``Field``,
a scene of the directory given third, or ``<scene>=<file>``: a scene with another file's graph;
``@<width>x<height>`` after it: the image size, else 400×300). Prints one JSON line per step: the
scene's node proxies (global ids), the scenes built, the report's error, the image's colour
ends and where the mesh is in it, the titles of the colour bars shown, the points of each Display's input and how often each filter has run so far. Rank 0
reads the pipeline back after each frame, as the stepper does.
Under ``mpirun`` every rank publishes its own four cells (``T`` counting on) and prints its own lines."""

import json
import os
import sys
from pathlib import Path

import numpy as np

PREFIX, STEPS, SCENES = sys.argv[1], json.loads(sys.argv[2]), Path(sys.argv[3])
RANK = int(os.environ.get("OMPI_COMM_WORLD_RANK", 0))
if "OMPI_COMM_WORLD_SIZE" in os.environ:
    sys.path.append(os.path.join(PREFIX, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}",
                                 "site-packages"))
    from mpi4py import MPI  # noqa: E402, F401 - Catalyst's own, on the path just added; initialises MPI

from neofoam_ui import catalyst  # noqa: E402
N = 4
QUADS = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
CUBE = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], float)


def _colours(png):
    # VTK only once Catalyst loaded ParaView: earlier, ParaView's own Python setup fails
    from vtkmodules.util.numpy_support import vtk_to_numpy  # noqa: PLC0415
    from vtkmodules.vtkIOImage import vtkPNGReader  # noqa: PLC0415

    if png is None:
        return None
    reader = vtkPNGReader()
    reader.SetMemoryBuffer(png)
    reader.SetMemoryBufferLength(len(png))
    reader.Update()
    width, height, _ = reader.GetOutput().GetDimensions()
    rgb = vtk_to_numpy(reader.GetOutput().GetPointData().GetScalars())[:, :3].astype(int).reshape(height, width, 3)
    flat = rgb[int(0.4 * height):].reshape(-1, 3)  # above the colour bar
    r, g, b = flat[:, 0], flat[:, 1], flat[:, 2]
    background = rgb[0, -1]  # VTK images start at the bottom: the bottom-right corner is free
    rows, cols = np.nonzero(np.abs(rgb[int(0.45 * height):] - background).sum(axis=2) > 30)
    box = [int(cols.min()), int(cols.max()), int(rows.min()), int(rows.max())] if len(rows) else None
    return {"box": box,  # the mesh's pixels above the colour bars: columns, rows (from 45 % up)
            "purple": bool(((r < 100) & (g < 40) & (b > 40)).any()),
            "yellow": bool(((r > 150) & (g > 150) & (b < 100)).any()),
            "red": bool(((r > 150) & (g < 40) & (b < 40)).any())}


RUNS = {}


def _count(name, proxy):
    """Count the runs of a filter (its algorithm's end events) from now on."""
    if name not in RUNS:
        RUNS[name] = 0
        proxy.GetClientSideObject().AddObserver("EndEvent", lambda *_: RUNS.__setitem__(name, RUNS[name] + 1))


def _points(proxy):
    data = proxy.GetClientSideObject().GetOutputDataObject(0)
    return data.GetNumberOfPoints() if data is not None else 0


# each rank its own four cells, one after the other along x, sharing their points (a contour
# of the point data crosses cells)
points = np.array([(RANK * N + x, y, z) for x in range(N + 1) for y in (0, 1) for z in (0, 1)], float)
corner = {tuple(c): 4 * int(c[0]) + 2 * int(c[1]) + int(c[2]) for c in CUBE}
geom = {"kind": "polyhedral", "points": points, "offsets": np.arange(0, 24 * N + 1, 4),
        "conn": np.array([4 * i + corner[tuple(CUBE[v])] for i in range(N) for q in QUADS for v in q]),
        "cell_offsets": np.arange(0, 6 * N + 1, 6), "cell_faces": np.arange(6 * N)}
T = np.arange(N, dtype=float) - 1 + RANK * N
fields = {"T": T, "U": np.stack([T + 2, np.zeros(N), np.zeros(N)], axis=1)}
insitu = catalyst.InSitu(PREFIX)
for step in STEPS:
    step, _, size = step.partition("@")  # an image size of its own: <scene>@<width>x<height>
    name, _, file = step.partition("=")  # a scene, or a scene with the graph of another file
    width, _, height = (size or "400x300").partition("x")
    params = {"field": "T", "component": "mag", "mode": "image", "width": width, "height": height}
    if name != "Field":
        params.update(scene=name, graph=(SCENES / f"{file or name}.json").read_text())
    image = insitu.execute(geom, fields, 0.0, params)
    if RANK == 0:  # as the stepper does: rank 0 alone reads the pipeline back
        insitu.describe()
    if os.environ.get("DUMP") and image:
        Path(os.environ["DUMP"], f"{step}.png").write_bytes(image)
    module = insitu._script_module()
    scene = module._scenes.get(params.get("scene", "Field"))
    ids, points = {}, {}
    if scene is not None:
        ids = {k: v.GetGlobalIDAsString() for k, v in scene.proxies.items()}
        for node in scene.graph["nodes"]:
            proxy = scene.proxies[node["id"]]
            if node["type"] == "Display":
                points[node["id"]] = _points(proxy.Input)
            elif node["type"] not in ("grid", "View"):
                _count(f"{scene.name}:{node['id']}", proxy)
    colours = _colours(image)
    box = colours.pop("box") if colours else None
    bars = sorted(str(r.Title) for r in (scene.view.Representations if scene is not None else [])
                  if r.GetProperty("Title") is not None and r.Visibility)
    print(json.dumps({"rank": RANK, "bars": bars, "scene": name, "ids": ids, "built": sorted(module._scenes),
                      "error": insitu.report.get("error"), "colours": colours, "box": box, "points": points,
                      "runs": dict(RUNS)}), flush=True)
insitu.finalize()
