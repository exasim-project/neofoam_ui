"""In-situ post-processing tables for pitzDaily — the script front door.

Registers a ``pressureForce`` node (pressure times face area vector, ``p S_f``) and
sums it over each wall: the pressure force on the wall per unit density, one
column per component. Run before ``system/postProcess.yaml`` is resolved, so the
node could be used from there too.
"""

from typing import Literal

import numpy as np

from neofoam.postprocess import Node, PatchDataSet, Sum, TableSet, patch


@Node.register
class PressureForce(Node):
    """``p`` on a patch → the vector ``p S_f`` per face (sum it for the force)."""

    type: Literal["pressureForce"] = "pressureForce"

    def compute(self, dataset: PatchDataSet) -> PatchDataSet:
        areas = np.asarray(dataset.geometry.face_areas())
        values = np.asarray(dataset.field).reshape(-1, 1)
        return dataset.with_field(values * areas)


postProcess = TableSet()  # the single module-level TableSet the loader picks up


@postProcess.table("pressure_force_lowerWall.csv")
def pressure_force_lowerWall():
    return patch("p", "lowerWall") | PressureForce() | Sum(name="F_lowerWall")


@postProcess.table("pressure_force_upperWall.csv")
def pressure_force_upperWall():
    return patch("p", "upperWall") | PressureForce() | Sum(name="F_upperWall")
