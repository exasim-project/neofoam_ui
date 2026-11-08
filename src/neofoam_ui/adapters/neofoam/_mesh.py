"""A NeoFOAM mesh as cells for the field view (read from the polyMesh files)."""

from __future__ import annotations

import os
from typing import Any, Optional

import numpy as np

from neofoam_ui.engine import DEFAULT_REGION
from neofoam_ui.geometry import cell_centres_geometry, polyhedral, read_polymesh
from neofoam_ui.parallel import rank_info

__all__ = ()


def _polymesh_dir(region: str, rank: int, size: int) -> str:
    """``[processor<rank>/]constant/[<region>/]polyMesh`` (OpenFOAM's layout)."""
    parts = [f"processor{rank}"] if size > 1 else []
    parts.append("constant")
    if region != DEFAULT_REGION:
        parts.append(region)
    return os.path.join(*parts, "polyMesh")


class _PolyMeshGeometry:
    """``mesh(ctx)``: this rank's polyMesh as polyhedra (static meshes)."""

    def mesh(self, ctx: Any) -> Optional[dict[str, Any]]:
        """This rank's cells for the in-situ view: the polyMesh as polyhedra.

        pybFoam's ``fvMesh`` exposes no points/faces, so they are read from the
        polyMesh files (``constant/polyMesh``, or ``processor<rank>/constant/polyMesh``
        in an MPI run); without them only the cell centres ``mesh.C()`` are shown.
        Static meshes only.
        """
        mesh = getattr(ctx, "mesh", None)
        if mesh is None:
            return None
        n_cells = int(mesh.nCells())
        directory = _polymesh_dir(DEFAULT_REGION, *rank_info())
        try:
            return polyhedral(read_polymesh(directory), n_cells)
        except (OSError, ValueError, IndexError):
            centres = np.asarray(mesh.C().internalField())
            return cell_centres_geometry(centres, note="cell centres (no polyMesh files)")
