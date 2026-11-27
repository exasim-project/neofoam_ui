"""The field pane's widgets: its bar (mode, scene, field, component, live frames) and stage."""

from __future__ import annotations

from typing import Any

from trame.widgets import client, html
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
.nf-field-stage { flex: 1 1 auto; min-height: 0; position: relative; display: flex; flex-direction: column; }
.nf-field-img { flex: 1 1 auto; min-height: 0; width: 100%; height: 100%; object-fit: contain; display: block; }
.nf-field-data { flex: 1 1 auto; min-height: 0; position: relative; }
.nf-field-cam { position: absolute; top: 6px; right: 6px; z-index: 2; display: flex; gap: 2px;
  background: rgba(var(--v-theme-surface), .8); border-radius: 6px; padding: 2px; }
.nf-field-cam .v-btn { min-width: 0; padding: 0 6px; }
.nf-field-cam .cell { font-size: 11px; opacity: .7; padding: 0 4px; align-self: center; }
.nf-field-empty { flex: 1 1 auto; display: flex; align-items: center; justify-content: center;
  font-size: 13px; opacity: .6; padding: 16px; text-align: center; }
.nf-field-regions { padding: 8px 12px 0; display: flex; flex-direction: column; gap: 4px; }
.nf-field-region { display: flex; justify-content: space-between; gap: 12px; border-left: 4px solid;
  padding: 2px 8px; font-size: 13px; background: rgba(128,128,128,.06); border-radius: 4px; }
@media (max-width: 760px) { .nf-field { border-left: 0; border-top: 1px solid rgba(128,128,128,.25); } }
"""

#: The stage's size in device pixels (at most 2 per CSS pixel), reported once it settled for
#: 300 ms: the ranks render the image at that size. One poller per page (the dock may remount).
_SIZE_PROBE = (
    "if (!window.nfFvSize) { window.nfFvSize = true; let seen = '', since = 0, sent = '';"
    " window.setInterval(() => { const el = window.document.querySelector('.nf-field-stage');"
    " if (!el) return; const r = el.getBoundingClientRect(); if (r.width < 32 || r.height < 32) return;"
    " const d = Math.min(window.devicePixelRatio || 1, 2);"
    " const w = Math.round(r.width * d), h = Math.round(r.height * d), k = w + 'x' + h;"
    " if (k !== seen) { seen = k; since = Date.now(); return; }"
    " if (k !== sent && Date.now() - since >= 300) { sent = k; trigger('nf_fv_size', [w, h, d]); } }, 150); }"
)


def _mode_toggle() -> None:
    """``image | data``; data greyed out (with the reason) where it cannot work."""
    with v3.VBtnToggle(v_model=("fv_mode",), mandatory=True, density="compact", variant="outlined",
                       divided=True, style="flex: none;"):
        v3.VBtn("image", value="image", size="small", title="a picture rendered in the solver")
        with html.Span(title=("fv_data_ok ? 'Catalyst Live: the data, drawn here (rotate, zoom)' : fv_data_why",)):
            v3.VBtn("data", value="data", size="small", disabled=("!fv_data_ok",))


#: The scene picker's items: the scenes that can show, ● for unsaved edits.
_SCENE_ITEMS = "sc_list.filter(s => !s.error).map(s => ({title: s.title + (s.unsaved ? ' ●' : ''), value: s.name}))"


def _bar() -> None:
    with html.Div(classes="nf-field-bar"):
        _mode_toggle()
        v3.VSelect(v_model=("sc_current",), items=(_SCENE_ITEMS,), label="scene", density="compact",
                   hide_details=True, variant="outlined", title="the scene drawn (Scenes panel: new, save, …)")
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


def _camera_row(cell: str) -> None:
    """The presets and zoom buttons; they act on ``cell`` (a JS expression; ``null``: every cell)."""
    v3.VBtn(icon="mdi-fit-to-screen-outline", size="x-small", variant="text", title="reset the camera",
            click=f"trigger('nf_fv_camera', ['reset', {cell}])")
    for side in ("+x", "-x", "+y", "-y", "+z", "-z"):
        v3.VBtn(side, size="x-small", variant="text", title=f"look from {side}",
                click=f"trigger('nf_fv_camera', ['{side}', {cell}])")
    v3.VBtn(icon="mdi-magnify-plus-outline", size="x-small", variant="text", title="zoom in",
            click=f"trigger('nf_fv_zoom', [1.25, {cell}])")
    v3.VBtn(icon="mdi-magnify-minus-outline", size="x-small", variant="text", title="zoom out",
            click=f"trigger('nf_fv_zoom', [0.8, {cell}])")


#: Where cell ``i`` of the layout is in the picture (the image fills the stage: it is rendered at its size).
_CELL_CORNER = ("{ top: `calc(${Math.floor(i / fv_grid.cols) * 100 / fv_grid.rows}% + 6px)`,"
                " right: `calc(${(fv_grid.cols - 1 - i % fv_grid.cols) * 100 / fv_grid.cols}% + 6px)` }")
#: The cell under the mouse (a wheel event on the picture).
_CELL_AT = ("fv_grid.names[Math.min(fv_grid.rows - 1, Math.floor($event.offsetY / $event.target.clientHeight * fv_grid.rows))"
            " * fv_grid.cols + Math.min(fv_grid.cols - 1, Math.floor($event.offsetX / $event.target.clientWidth * fv_grid.cols))]")


def _camera_buttons() -> None:
    """Camera presets and zoom, over the picture (the ranks render them; data mode: this view):
    one row for every cell, or with cameras per cell one in each cell's corner."""
    shown = "fv_mode === 'image' ? fv_src : fv_data_shown"
    with html.Div(classes="nf-field-cam", v_show=f"({shown}) && !(fv_grid && fv_grid.per_cell)"):
        _camera_row("null")
    with html.Template(v_if="fv_grid && fv_grid.per_cell"):
        with html.Div(v_for="(c, i) in fv_grid.names", key="c", classes="nf-field-cam", v_show=shown,
                      style=(_CELL_CORNER,)):
            html.Span("{{ c }}", classes="cell")
            _camera_row("c")


def _stage(data: Any) -> None:
    """The picture (image mode) or the interactive view (data mode), with the camera buttons."""
    with html.Div(classes="nf-field-stage"):
        client.ClientTriggers(mounted=_SIZE_PROBE)
        _camera_buttons()
        html.Img(src=("fv_src",), classes="nf-field-img", v_show="fv_mode === 'image' && fv_src",
                 alt="in-situ view",
                 raw_attrs=[f'@wheel.prevent="trigger(\'nf_fv_zoom\', [$event.deltaY < 0 ? 1.25 : 0.8, {_CELL_AT}])"'])
        if data is not None:
            with html.Div(classes="nf-field-data", v_show="fv_mode === 'data' && fv_data_shown"):
                data.widget()
        # a multi-region solver without an image (no mesh, or no Catalyst): each region's fields,
        # grouped in the boxes' order, in the region's colour (the variables table's rows)
        with html.Div(classes="nf-field-regions", v_if="!fv_src && !fv_data_shown && fields_rows.some(f => f.region)"):
            with html.Div(v_for="f in fields_rows.filter(f => f.region)", key="f.name",
                          classes="nf-field-region", style=("`border-left-color:${f.color}`",)):
                html.Span("{{ f.name }}", classes="nf-mono", style=("`color:${f.color}`",))
                html.Span("{{ f.min === f.max ? f.mean : f.min + ' … ' + f.max + ' (mean ' + f.mean + ')' }}")
        html.Div("{{ fv_msg }}", classes="nf-field-empty", v_show="fv_mode === 'image' ? !fv_src : !fv_data_shown")


def _field_pane(data: Any) -> None:
    """The pane (bar + stage); shown while ``fv_on``. ``data``: the data mode's view, or ``None``."""
    with html.Div(classes="nf-field", v_show="fv_on"):
        _bar()
        _stage(data)
