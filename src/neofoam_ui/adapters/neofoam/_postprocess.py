"""NeoFOAM's post-processing tables, hooked when the ``post_processor`` init step built them."""

from __future__ import annotations

from typing import Any, Callable

from neofoam_ui.parallel import rank_info
from neofoam_ui.pptables import observe

__all__ = ()


class _PostProcessHook:
    """``observe(value, emit)``: sees every init-step result; hooks the ``PostProcessor``."""

    def observe(self, value: Any, emit: Callable[[dict[str, Any]], None]) -> None:
        """Hook NeoFOAM's ``PostProcessor`` (init step ``post_processor``) when it is built.

        Every table the case declares then reports its rows after each ``post_process``
        (:func:`neofoam_ui.pptables.observe`, on rank 0 only); the CSV files are written as before.
        """
        try:
            from neofoam.postprocess.model import PostProcessor  # noqa: PLC0415
        except ImportError:  # a NeoFOAM without post-processing
            return
        if isinstance(value, PostProcessor):
            if getattr(value, "_tables", None):
                _ensure_neon()
            if rank_info()[0] > 0:  # only rank 0's rows reach the UI: the others convert none
                return
            if not observe(value, emit):
                emit({"type": "stdout", "text": "post-processing tables: this NeoFOAM's "
                                                "PostProcessor cannot be observed"})


def _ensure_neon() -> None:
    """Bring up NeoN/Kokkos for the post-processing kernels (idempotent).

    The tables reduce with NeoN kernels, which allocate NeoN vectors even for host
    numpy input; the pybFoam ``incompressibleFluid`` never initializes Kokkos, so its
    first ``min``/``max``/``mean`` aborts the process (also in a plain ``run()``).
    """
    try:
        from neofoam.solver.neon_runtime import ensure_neon_initialized  # noqa: PLC0415
    except ImportError:
        return
    ensure_neon_initialized(["neofoam"])
