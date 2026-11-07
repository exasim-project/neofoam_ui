"""Node colours: a fixed colour per known domain / pipeline kind, fallbacks for the others."""

from __future__ import annotations

from typing import Optional

__all__ = ()

_DOMAIN_COLORS = {
    "solutionLoop": "#5b6fb5",
    "outer": "#d08a2e",
    "heat": "#c0504d",
    "heatSource": "#8e5bb5",
    "material": "#2f9e8f",
    "fieldWriter": "#5b7a99",
    "postProcess": "#6aa84f",
    "mesh": "#7f8c8d",
    "config": "#95a5a6",
    "field": "#2e86c1",
    "model": "#8e5bb5",
}
_FALLBACK = ["#e67e22", "#16a085", "#c0392b", "#2980b9", "#8e44ad", "#27ae60"]

_PIPELINE_COLORS = {
    # Catalyst / ParaView
    "source": "#2e86c1",
    "filter": "#6aa84f",
    "display": "#d08a2e",
    "view": "#8e5bb5",
    "extractor": "#c0504d",
    # NeoFOAM post-processing (neofoam_ui.pptables: the module a node is filed in)
    "selector": "#e0a030",
    "field function": "#16a085",
    "binning": "#8e44ad",
    "aggregator": "#b5475b",
    "writer": "#5b7a99",
    "custom": "#7f8c8d",
}


class _Palette:
    """Colours by key; an unknown key gets the next fallback colour, kept for the next time."""

    def __init__(self, colors: dict[str, str], none: str = "#9aa3ad") -> None:
        self._colors = dict(colors)
        self._none = none

    def color(self, key: Optional[str]) -> str:
        if not key:
            return self._none
        if key not in self._colors:
            self._colors[key] = _FALLBACK[len(self._colors) % len(_FALLBACK)]
        return self._colors[key]


#: Operations and init steps by their domain / category.
_DOMAIN = _Palette(_DOMAIN_COLORS)
#: Post-processing pipeline nodes by their kind.
_PIPELINE = _Palette(_PIPELINE_COLORS)
