"""Native bits: the Catalyst Python modules of an install prefix, and no FP traps around them."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import os
import sys
from typing import Any, Iterator

__all__ = ()

_FE_ALL_EXCEPT = 0x3D  # glibc x86: invalid | divbyzero | overflow | underflow | inexact
_libm: Any = None


@contextlib.contextmanager
def _no_fp_traps() -> Iterator[None]:
    """OpenFOAM traps FP exceptions (``FOAM_SIGFPE``); VTK/ParaView overflow on purpose."""
    global _libm
    if _libm is None:
        name = ctypes.util.find_library("m")
        _libm = ctypes.CDLL(name) if name else False
    if not _libm:
        yield
        return
    traps = _libm.fegetexcept()
    _libm.fedisableexcept(_FE_ALL_EXCEPT)
    try:
        yield
    finally:
        _libm.feclearexcept(_FE_ALL_EXCEPT)  # do not fire on what ParaView left behind
        if traps > 0:
            _libm.feenableexcept(traps)


def _import(prefix: str) -> tuple[Any, Any]:
    site = os.path.join(prefix, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}",
                        "site-packages")
    if site not in sys.path:
        sys.path.append(site)  # last: the solver's own numpy etc. win
    import catalyst  # noqa: PLC0415
    import catalyst_conduit  # noqa: PLC0415

    return catalyst, catalyst_conduit
