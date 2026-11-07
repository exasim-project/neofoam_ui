"""Field view pane: the image Catalyst rendered in situ at the last pause.

The solver ranks hand their cells and fields to ParaView through Catalyst 2
(:mod:`neofoam_ui.catalyst`); ParaView writes one PNG per frame into a directory
the UI serves as ``insitu/``. No field data passes through the UI.
"""

from __future__ import annotations

from typing import Any

from trame.widgets import html
from trame.widgets import vuetify3 as v3

__all__ = ()

_FIELD_CSS = r"""
.nf-field { display: flex; flex-direction: column; min-width: 0; min-height: 0;
  border-left: 1px solid rgba(128,128,128,.25); background: rgb(var(--v-theme-surface)); }
.nf-field-bar { display: flex; align-items: center; gap: 8px; padding: 4px 10px; flex-wrap: wrap;
  border-bottom: 1px solid rgba(128,128,128,.2); }
.nf-field-bar .v-select { flex: 0 1 150px; min-width: 110px; }
.nf-field-bar .nf-num { flex: 0 0 112px; }
.nf-field-msg { font-size: 12px; opacity: .7; flex: 1 1 160px; min-width: 0; }
.nf-field-img { flex: 1 1 auto; min-height: 0; width: 100%; object-fit: contain; display: block; }
.nf-field-empty { flex: 1 1 auto; display: flex; align-items: center; justify-content: center;
  font-size: 13px; opacity: .6; padding: 16px; text-align: center; }
@media (max-width: 760px) { .nf-field { border-left: 0; border-top: 1px solid rgba(128,128,128,.25); } }
"""


def _component_items(ncomp: int) -> list[dict[str, Any]]:
    if ncomp <= 1:
        return []
    names = "xyz" if ncomp == 3 else [str(i) for i in range(ncomp)]
    return [{"title": "magnitude", "value": "mag"}] + [
        {"title": n, "value": str(i)} for i, n in enumerate(names)]


def _field_pane() -> None:
    """The pane (header + rendered image); shown while ``fv_on``."""
    with html.Div(classes="nf-field", v_show="fv_on"):
        with html.Div(classes="nf-field-bar"):
            v3.VSelect(v_model=("fv_field",), items=("fv_fields",), label="field", density="compact",
                       hide_details=True, variant="outlined", disabled=("!fv_fields.length",))
            v3.VSelect(v_model=("fv_comp",), items=("fv_comps",), label="component", density="compact",
                       hide_details=True, variant="outlined", v_show="fv_comps.length")
            v3.VCheckbox(v_model=("fv_live",), label="live", hide_details=True, density="compact",
                         style="flex: none;", title="update while running (off: only at pauses)")
            # Live frames: every N time steps, at least s seconds apart (each costs a render).
            v3.VTextField(v_model_number=("fv_every",), type="number", min=1, label="every n steps",
                          density="compact", hide_details=True, variant="outlined", v_show="fv_live",
                          classes="nf-num", title="a live frame at most every n time steps")
            v3.VTextField(v_model_number=("fv_secs",), type="number", min=0, step=0.1, label="≥ s apart",
                          density="compact", hide_details=True, variant="outlined", v_show="fv_live",
                          classes="nf-num", title="and at least this many seconds apart")
            html.Span("{{ fv_msg }}", classes="nf-field-msg")
        html.Img(src=("fv_src",), classes="nf-field-img", v_show="fv_src", alt="in-situ view")
        html.Div("{{ fv_msg }}", classes="nf-field-empty", v_show="!fv_src")
