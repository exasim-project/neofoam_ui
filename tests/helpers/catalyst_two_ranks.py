"""Under ``mpirun -np 2``: each rank publishes four unit hexahedra of its own value to the
stepper's pipeline and prints which colour ends its captured image shows (JSON, one line)."""

import json
import os
import sys

import numpy as np

PREFIX = sys.argv[1]
sys.path.append(os.path.join(PREFIX, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}",
                             "site-packages"))
from mpi4py import MPI  # noqa: E402 - Catalyst's own, on the path just added; initialises MPI

from neofoam_ui import catalyst  # noqa: E402

N = 4
QUADS = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
CUBE = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], float)


def _colours(png):
    """Whether the image has pixels of Viridis' purple and of its yellow end."""
    # VTK only once Catalyst loaded ParaView: earlier, ParaView's own Python setup fails
    from vtkmodules.util.numpy_support import vtk_to_numpy  # noqa: PLC0415
    from vtkmodules.vtkIOImage import vtkPNGReader  # noqa: PLC0415

    reader = vtkPNGReader()
    reader.SetMemoryBuffer(png)
    reader.SetMemoryBufferLength(len(png))
    reader.Update()
    rgb = vtk_to_numpy(reader.GetOutput().GetPointData().GetScalars())[:, :3].astype(int)
    purple = (rgb[:, 0] < 100) & (rgb[:, 1] < 40) & (rgb[:, 2] > 40)
    yellow = (rgb[:, 0] > 150) & (rgb[:, 1] > 150) & (rgb[:, 2] < 100)
    return bool(purple.any()), bool(yellow.any())


rank = MPI.COMM_WORLD.rank
points = np.concatenate([CUBE + (rank * N + i, 0, 0) for i in range(N)])
geom = {"kind": "polyhedral", "points": points, "offsets": np.arange(0, 24 * N + 1, 4),
        "conn": np.array([8 * i + v for i in range(N) for q in QUADS for v in q]),
        "cell_offsets": np.arange(0, 6 * N + 1, 6), "cell_faces": np.arange(6 * N)}
insitu = catalyst.InSitu(PREFIX)
image = insitu.execute(geom, {"T": np.full(N, float(rank))}, 0.0, {"field": "T", "component": "mag", "mode": "image"})
purple, yellow = _colours(image) if image else (False, False)
print(json.dumps({"rank": rank, "png": image is not None, "purple": purple, "yellow": yellow}), flush=True)
insitu.finalize()
