"""Frames rendered in turn on two long-lived threads (as a multi-region solver's region threads
are): prints, per frame, the thread and which colour ends its captured image shows (JSON)."""

import json
import queue
import sys
import threading

import numpy as np

from neofoam_ui import catalyst

N = 4
QUADS = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
CUBE = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], float)
GEOM = {"kind": "polyhedral", "points": np.concatenate([CUBE + (i, 0, 0) for i in range(N)]),
        "offsets": np.arange(0, 24 * N + 1, 4), "conn": np.array([8 * i + v for i in range(N) for q in QUADS for v in q]),
        "cell_offsets": np.arange(0, 6 * N + 1, 6), "cell_faces": np.arange(6 * N)}
session = {}


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


def frame(number):
    if "insitu" not in session:
        session["insitu"] = catalyst.InSitu(sys.argv[1])
    image = session["insitu"].execute(GEOM, {"T": np.arange(N, dtype=float) + number}, float(number),
                                      {"field": "T", "component": "mag", "mode": "image"})
    purple, yellow = _colours(image)
    print(json.dumps({"frame": number, "thread": threading.current_thread().name,
                      "purple": purple, "yellow": yellow}), flush=True)


def serve(jobs):
    for job in iter(jobs.get, None):
        job()


lanes = [queue.Queue(), queue.Queue()]
threads = [threading.Thread(target=serve, args=(q,), name=f"region{i}") for i, q in enumerate(lanes)]
for thread in threads:
    thread.start()
for number in range(4):
    done = threading.Event()
    lanes[number % 2].put(lambda number=number: (frame(number), done.set()))
    done.wait()
lanes[0].put(lambda: session["insitu"].finalize())
for lane in lanes:
    lane.put(None)
for thread in threads:
    thread.join()
