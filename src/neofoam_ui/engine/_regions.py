"""Mesh regions as the field view sees them: today one, the solver's own mesh and fields.

A multi-region solver (CHT, FSI) implements :class:`~neofoam_ui.engine.RegionProvider`;
any other solver is adapted to one region named :data:`DEFAULT_REGION` (OpenFOAM's
``region0``) from its optional ``mesh`` / ``view_fields`` and its ``fields``.
The view renders the first region (one Catalyst channel ``grid``); its geometry cache and
field identities are keyed by region already.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from neofoam_ui.engine._protocols import MeshProvider, Region, RegionProvider, ViewFieldProvider

__all__ = ()

#: Name of the one region of a solver that does not describe its regions.
DEFAULT_REGION = "region0"


class _SolverRegion:
    """A solver's own mesh and fields as its only region."""

    name = DEFAULT_REGION

    def __init__(self, solver: Any) -> None:
        self._solver = solver

    def mesh(self, ctx: Any) -> Optional[dict[str, Any]]:
        if not isinstance(self._solver, MeshProvider):
            return None
        return self._solver.mesh(ctx)

    def fields(self, ctx: Any) -> dict[str, np.ndarray]:
        if isinstance(self._solver, ViewFieldProvider):
            return self._solver.view_fields(ctx)
        return self._solver.fields(ctx)


def _regions_of(solver: Any) -> Any:
    """``regions(ctx) -> list[Region]`` of ``solver`` (decided once, not per frame)."""
    if isinstance(solver, RegionProvider):
        return solver.regions
    only: list[Region] = [_SolverRegion(solver)]
    return lambda ctx: only
