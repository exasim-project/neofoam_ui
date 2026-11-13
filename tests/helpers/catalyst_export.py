"""Exports a scene, or runs an exported one, in a fresh Catalyst session.

``export <prefix> <scene file> <script>``: one frame of the polyMesh case (``T`` per cell,
``U`` = (T, 0, 0)) through the stepper's pipeline, writing the scene as a Catalyst script;
prints the report's ``exported`` and ``error``. ``replay <prefix> <script> -``: the same frame
through that script alone (its extracts go where its options say, under the working directory)."""

import json
import sys
from pathlib import Path

import numpy as np

from neofoam_ui import catalyst
from neofoam_ui.geometry import polyhedral, read_polymesh

CASE = Path(__file__).resolve().parents[1] / "scenes" / "cases" / "builder"
mode, prefix, source, target = sys.argv[1:5]
geom = polyhedral(read_polymesh(str(CASE / "constant" / "polyMesh")), 2)
T = np.array([1.0, 2.0])
fields = {"T": T, "U": np.stack([T, np.zeros(2), np.zeros(2)], axis=1)}
params = {"field": "T", "component": "mag", "mode": "image"}
if mode == "export":
    insitu = catalyst.InSitu(prefix)
    params.update(scene=Path(source).stem, graph=Path(source).read_text(), export=target)
else:
    insitu = catalyst.InSitu(prefix, script=source, capture=False)
insitu.execute(geom, fields, 0.0, params)
print(json.dumps({"exported": insitu.report.get("exported"), "error": insitu.report.get("error")}), flush=True)
insitu.finalize()
