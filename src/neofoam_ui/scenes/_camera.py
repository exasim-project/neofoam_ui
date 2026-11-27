"""A View node's camera fitted to the domain: the same in the solver's image and the UI's data view."""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ()

_PRESETS = ("+x", "-x", "+y", "-y", "+z", "-z")


def _look(camera: str, extent: list[float]) -> tuple[int, float]:
    """The axis looked along and the side looked from: a preset, else along the thinnest axis."""
    if camera in _PRESETS:
        return "xyz".index(camera[1]), 1.0 if camera[0] == "+" else -1.0
    return extent.index(min(extent)), 1.0


def _camera_fit(camera: str, zoom: float, bounds: Sequence[float], size: Sequence[float],
                bar_px: float = 0.0) -> dict[str, Any]:
    b = bounds
    extent = [b[1] - b[0], b[3] - b[2], b[5] - b[4]]
    thin, sign = _look(camera, extent)
    up_axis = 1 if thin != 1 else 2
    side = next(a for a in range(3) if a not in (thin, up_axis))
    width, height = size
    fit = 1.04 * max(extent[up_axis] / 2 * height / max(height - bar_px, 1), extent[side] / 2 * height / width)
    zoom = max(zoom, 1e-3)
    focal = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2]
    focal[up_axis] -= bar_px * fit / height / zoom  # centre the mesh above the colour bars
    position = list(focal)
    position[thin] += sign * 10 * max(max(extent), 1e-30)
    up = [0.0, 0.0, 0.0]
    up[up_axis] = 1.0
    return {"focal": focal, "position": position, "up": up, "scale": fit / zoom}
