"""A NeoFOAM mesh as cells for the field view (read from the polyMesh files)."""

from __future__ import annotations

import os
from typing import Any, Optional

import numpy as np

from neofoam_ui.geometry import cell_centres_geometry, polyhedral, read_polymesh
from neofoam_ui.parallel import rank_info

__all__ = ()


def _polymesh_dir(rank: int, size: int) -> str:
    """``[processor<rank>/]constant/polyMesh`` (OpenFOAM's layout, one region)."""
    return os.path.join(*([f"processor{rank}"] if size > 1 else []), "constant", "polyMesh")


def _region_dir(region: Any) -> Optional[str]:
    """``<region>/constant/polyMesh`` of a multiRun region (its own case directory), if there."""
    if not isinstance(region, str) or not region:
        return None
    directory = os.path.join(region, "constant", "polyMesh")
    return directory if os.path.isdir(directory) else None


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
        directory = _region_dir(getattr(ctx, "region", None)) or _polymesh_dir(*rank_info())
        try:
            return polyhedral(read_polymesh(directory), n_cells)
        except (OSError, ValueError, IndexError):
            centres = np.asarray(mesh.C().internalField())
            return cell_centres_geometry(centres, note="cell centres (no polyMesh files)")
