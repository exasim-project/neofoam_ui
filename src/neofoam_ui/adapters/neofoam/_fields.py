"""A NeoFOAM run context's scalars and fields, as the stepper reads them."""

from __future__ import annotations

import re
from typing import Any, Optional

import numpy as np

from neofoam_ui.engine import DEFAULT_REGION

__all__ = ()

#: Registry names of old-time and previous-iteration copies of a field.
_OLD_COPY = re.compile(r"(_0)+$|PrevIter$")


def _region_contexts(ctx: Any) -> Optional[dict[str, Any]]:
    """``{region: context}`` of a multi-region driver (multiRun's ``ctx.models["regions"]``), else ``None``."""
    regions = (getattr(ctx, "models", None) or {}).get("regions")
    if not isinstance(regions, dict):
        return None
    return {name: entry[1] for name, entry in regions.items()
            if isinstance(entry, tuple) and len(entry) == 2}


class _Region:
    """One region of a NeoFOAM case: its context's cell fields and, if it has one, its mesh."""

    def __init__(self, name: str, solver: "_FieldAccess", ctx: Optional[Any] = None) -> None:
        self.name = name
        self._solver = solver
        #: The region's own context; ``None``: the context the stepper hands in (one region).
        self._ctx = ctx

    def mesh(self, ctx: Any) -> Optional[dict[str, Any]]:
        if self._ctx is not None and getattr(self._ctx, "mesh", None) is None:
            return None  # a region without a mesh (e.g. a test solver): no field view
        mesh = getattr(self._solver, "mesh", None)
        return mesh(self._ctx if self._ctx is not None else ctx) if mesh else None

    def fields(self, ctx: Any) -> dict[str, np.ndarray]:
        if self._ctx is not None:
            return self._solver._own_fields(self._ctx)
        return self._solver.view_fields(ctx)


class _FieldAccess:
    """``scalars`` / ``fields`` / ``view_fields`` of a NeoFOAM context (``ctx.time``, ``ctx.fields``).

    A multi-region driver's context (multiRun) is read through :meth:`regions`: every region's
    fields, named ``<region>.<field>``.
    """

    def regions(self, ctx: Any) -> list[Any]:
        """The case's regions (:class:`~neofoam_ui.engine.Region`); one, ``region0``, unless multi-region."""
        contexts = _region_contexts(ctx)
        if not contexts:
            return [_Region(DEFAULT_REGION, self)]
        return [_Region(name, self, region_ctx) for name, region_ctx in contexts.items()]

    def scalars(self, ctx: Any) -> dict[str, Any]:
        t = getattr(ctx, "time", None)
        if t is None:
            return {}
        return {"time": t.value, "deltaT": t.delta_t, "timeIndex": t.index,
                "writeTime": t.write_time}

    def fields(self, ctx: Any) -> dict[str, np.ndarray]:
        contexts = _region_contexts(ctx)
        if not contexts:
            return self._own_fields(ctx)
        return {f"{region}.{name}": values
                for region, region_ctx in contexts.items()
                for name, values in self._own_fields(region_ctx).items()}

    def _own_fields(self, ctx: Any) -> dict[str, np.ndarray]:
        """The cell fields of one context: geometric fields and plain numpy arrays."""
        out: dict[str, np.ndarray] = {}
        for name, field in (getattr(ctx, "fields", None) or {}).items():
            if isinstance(field, np.ndarray):
                out[name] = np.atleast_1d(field)
                continue
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
