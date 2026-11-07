"""A NeoFOAM run context's scalars and fields, as the stepper reads them."""

from __future__ import annotations

import re
from typing import Any

import numpy as np

__all__ = ()

#: Registry names of old-time and previous-iteration copies of a field.
_OLD_COPY = re.compile(r"(_0)+$|PrevIter$")


class _FieldAccess:
    """``scalars`` / ``fields`` / ``view_fields`` of a NeoFOAM context (``ctx.time``, ``ctx.fields``)."""

    def scalars(self, ctx: Any) -> dict[str, Any]:
        t = getattr(ctx, "time", None)
        if t is None:
            return {}
        return {"time": t.value, "deltaT": t.delta_t, "timeIndex": t.index,
                "writeTime": t.write_time}

    def fields(self, ctx: Any) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for name, field in (getattr(ctx, "fields", None) or {}).items():
            try:
                out[name] = np.asarray(field.internalField())
            except Exception:  # not a geometric field (or no internalField)
                continue
        return out

    def view_fields(self, ctx: Any) -> dict[str, np.ndarray]:
        """:meth:`fields` plus every other vol field on the mesh's registry (``k``, ``nut``, …).

        Model fields (turbulence) are not in ``ctx.fields``; the registry has them all. Old-time
        (``U_0``) and previous-iteration (``pPrevIter``) copies are left out.
        """
        out = self.fields(ctx)
        mesh = getattr(ctx, "mesh", None)
        try:
            import pybFoam  # noqa: PLC0415

            kinds = [(k, list(map(str, k.list_objects(mesh))))
                     for k in (pybFoam.volScalarField, pybFoam.volVectorField)]
        except Exception:  # no pybFoam mesh (another backend): the context fields only
            return out
        # registry names of the context fields already in (their key may differ)
        seen = {f.name() for k, f in (getattr(ctx, "fields", None) or {}).items() if k in out}
        for kind, names in kinds:
            for name in names:
                if name in out or name in seen or _OLD_COPY.search(name):
                    continue
                try:
                    out[name] = np.asarray(kind.from_registry(mesh, name).internalField())
                except Exception:  # noqa: BLE001 - deregistered meanwhile: skip it
                    continue
        return out
