"""How numbers, times and paths read in the UI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import neofoam_ui

__all__ = ()

#: The directory holding the package (``src`` of a checkout): paths below it are shown relative.
_SRC = str(Path(neofoam_ui.__file__).resolve().parent.parent)


def _fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.2f} s" if ms >= 1000 else f"{ms:.1f} ms"


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _short_path(path: Optional[str]) -> str:
    if not path:
        return "—"
    for root in (os.getcwd(), _SRC):
        if path.startswith(root + os.sep):
            return os.path.relpath(path, root)
    return path
