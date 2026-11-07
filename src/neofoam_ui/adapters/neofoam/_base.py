"""What both NeoFOAM adapters share: fields, the mesh for the view, the post-processing hook."""

from __future__ import annotations

from neofoam_ui.adapters.neofoam._fields import _FieldAccess
from neofoam_ui.adapters.neofoam._mesh import _PolyMeshGeometry
from neofoam_ui.adapters.neofoam._postprocess import _PostProcessHook

__all__ = ()


class _NeoFoamBase(_FieldAccess, _PolyMeshGeometry, _PostProcessHook):
    """A NeoFOAM solver as a :class:`~neofoam_ui.engine.FieldSource` with a mesh and an observer."""
