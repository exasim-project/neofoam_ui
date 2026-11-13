"""One frame of four unit hexahedra along x (``T`` = -1, 0, 1, 2) through the stepper's
pipeline with the display options given as JSON (first argument after the prefix); prints
what the image shows (JSON): its size, which colour ends above the colour bar, whether the mesh reaches the
top-left corner, and the pipeline's report."""

import json
import sys

import numpy as np

from neofoam_ui import catalyst

N = 4
QUADS = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
CUBE = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], float)
GEOM = {"kind": "polyhedral", "points": np.concatenate([CUBE + (i, 0, 0) for i in range(N)]),
        "offsets": np.arange(0, 24 * N + 1, 4), "conn": np.array([8 * i + v for i in range(N) for q in QUADS for v in q]),
        "cell_offsets": np.arange(0, 6 * N + 1, 6), "cell_faces": np.arange(6 * N)}


def _seen(png):
    # VTK only once Catalyst loaded ParaView: earlier, ParaView's own Python setup fails
    from vtkmodules.util.numpy_support import vtk_to_numpy  # noqa: PLC0415
    from vtkmodules.vtkIOImage import vtkPNGReader  # noqa: PLC0415

    reader = vtkPNGReader()
    reader.SetMemoryBuffer(png)
    reader.SetMemoryBufferLength(len(png))
    reader.Update()
    width, height, _ = reader.GetOutput().GetDimensions()
    rgb = vtk_to_numpy(reader.GetOutput().GetPointData().GetScalars())[:, :3].astype(int).reshape(height, width, 3)
    flat = rgb[int(0.4 * height):].reshape(-1, 3)  # above the colour bar
    r, g, b = flat[:, 0], flat[:, 1], flat[:, 2]
    background = rgb[0, -1]  # VTK images start at the bottom: the bottom-right corner is free
    corner = rgb[int(0.95 * height), int(0.05 * width)]  # near the top left
    return {"size": [width, height],
            "purple": bool(((r < 100) & (g < 40) & (b > 40)).any()),
            "yellow": bool(((r > 150) & (g > 150) & (b < 100)).any()),
            "red": bool(((r > 100) & (g < 40) & (b < 40)).any()),
            "corner_is_mesh": bool(np.abs(corner - background).sum() > 30)}


insitu = catalyst.InSitu(sys.argv[1])
options = json.loads(sys.argv[2])
image = insitu.execute(GEOM, {"T": np.arange(N, dtype=float) - 1}, 0.0,
                       {"field": "T", "component": "mag", "mode": "image", **options})
print(json.dumps({**_seen(image), "notes": insitu.report["notes"], "bounds": insitu.report["bounds"],
                  "axis": insitu.report["axis"]}), flush=True)
insitu.finalize()
