"""The ``catalyst`` in-situ type of NeoFOAM's ``postProcess`` (see :func:`register_insitu`)."""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

import numpy as np
from neofoam.framework.context import Context
from neofoam.postprocess.insitu import InSitu
from pydantic import PrivateAttr

from neofoam_ui import catalyst
from neofoam_ui.adapters.neofoam._fields import _FieldAccess
from neofoam_ui.adapters.neofoam._mesh import _PolyMeshGeometry
from neofoam_ui.engine._fieldview import _shown

__all__ = ()


class _CaseCells(_FieldAccess, _PolyMeshGeometry):
    """A NeoFOAM context's mesh and cell fields, read as the stepper reads them."""


@InSitu.register
class CatalystView(InSitu):
    """One Catalyst view of the running case: ``field`` coloured by ``component``."""

    type: Literal["catalyst"] = "catalyst"
    field: Optional[str] = None
    component: str = "mag"

    _session: Any = PrivateAttr(default=None)
    _geometry: Optional[dict[str, Any]] = PrivateAttr(default=None)

    def execute(self, ctx: Context) -> None:
        requested = {"field": self.field or "", "component": self.component}
        driver = catalyst.frame_driver()
        if driver is not None:
            driver(requested)
            return
        self._render(ctx, requested)

    def finalize(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            session.finalize()

    def _render(self, ctx: Context, requested: dict[str, str]) -> None:
        """Render on our own Catalyst session (no stepper in this process)."""
        cells = _CaseCells()
        if self._geometry is None:
            self._geometry = cells.mesh(ctx)
        fields = {k: v for k, v in cells.view_fields(ctx).items() if v.ndim in (1, 2)}
        params = _shown((requested["field"], requested["component"]), fields)
        if self._geometry is None or params is None:
            return
        if self._session is None:
            out_dir = os.environ.get(catalyst.OUT_ENV) or os.path.join("postProcessing", self.name)
            self._session = catalyst.InSitu(out_dir=out_dir)
        published = fields if catalyst.custom_script() else {params["field"]: fields[params["field"]]}
        time_value = float(np.asarray(getattr(ctx.time, "value", 0.0)))
        self._session.execute(self._geometry, published, time_value, params)
