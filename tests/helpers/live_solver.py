"""A solver side for the data-mode test: four unit hexahedra published in data mode, frame
after frame (``T`` = the frame's number), a second apart so the listener can pick another
source in between: the first frame shows the built-in ``Field`` scene, the others the scene of
the file given as second argument. Prints the sources each frame's report names (``extracts``).
The third argument names the listener (``host:port``, as the worker's ``--data``)."""

import json
import sys
import time
from pathlib import Path

import numpy as np

from neofoam_ui import catalyst
from neofoam_ui.config import CatalystConfig

N = 4
QUADS = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
CUBE = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], float)

geom = {"kind": "polyhedral", "points": np.concatenate([CUBE + (i, 0, 0) for i in range(N)]),
        "offsets": np.arange(0, 24 * N + 1, 4), "conn": np.array([8 * i + v for i in range(N) for q in QUADS for v in q]),
        "cell_offsets": np.arange(0, 6 * N + 1, 6), "cell_faces": np.arange(6 * N)}
catalyst.use(CatalystConfig(prefix=sys.argv[1]), data_url=sys.argv[3])
insitu = catalyst.InSitu()
scene = Path(sys.argv[2])
then = {"scene": scene.stem, "graph": scene.read_text()}
for frame in range(4):
    image = insitu.execute(geom, {"T": np.full(N, float(frame))}, float(frame),
                           {"field": "T", "component": "mag", "mode": "data", **(then if frame else {})})
    assert image is None
    print(json.dumps({"extracts": [e["source"] for e in insitu.report.get("extracts", [])]}), flush=True)
    time.sleep(1.0)
insitu.finalize()
