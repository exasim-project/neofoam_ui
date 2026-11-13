"""The field pane's widgets: its bar (mode, what to show, cut, field, colours, live frames) and stage."""

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
.nf-field-cut { display: flex; align-items: center; gap: 6px; flex: 1 1 420px; min-width: 340px; }
.nf-field-cut .v-slider { flex: 1 1 140px; min-width: 120px; }
.nf-field-coord { font-size: 12px; opacity: .8; min-width: 72px; }
.nf-field-msg { font-size: 12px; opacity: .7; flex: 1 1 160px; min-width: 0; }
.nf-field-stage { flex: 1 1 auto; min-height: 0; position: relative; display: flex; flex-direction: column; }
.nf-field-img { flex: 1 1 auto; min-height: 0; width: 100%; height: 100%; object-fit: contain; display: block; }
.nf-field-data { flex: 1 1 auto; min-height: 0; position: relative; }
.nf-field-cam { position: absolute; top: 6px; right: 6px; z-index: 2; display: flex; gap: 2px;
  background: rgba(var(--v-theme-surface), .8); border-radius: 6px; padding: 2px; }
.nf-field-cam .v-btn { min-width: 0; padding: 0 6px; }
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
_AXIS = "'xyz'.indexOf(fv_axis)"
#: The cut's position as a coordinate, from the domain bounds of the last frame.
_COORD = (f"fv_bounds && fv_axis ? fv_axis + ' = ' + (fv_bounds[2 * {_AXIS}] + fv_pos / 100"
          f" * (fv_bounds[2 * {_AXIS} + 1] - fv_bounds[2 * {_AXIS}])).toPrecision(4) : fv_pos + ' %'")
_CMAP_ITEMS = [{"title": "Viridis", "value": "viridis"}, {"title": "Cool to Warm", "value": "coolwarm"},
               {"title": "Inferno", "value": "inferno"}, {"title": "Jet", "value": "jet"},
               {"title": "Grayscale", "value": "grayscale"}]


def _toggle(model: str, items: list[tuple[str, str, str]], **kwargs: Any) -> None:
    """A compact button group for ``model``: ``(label, value, tooltip)`` per button."""
    with v3.VBtnToggle(v_model=(model,), mandatory=True, density="compact", variant="outlined", divided=True,
                       style="flex: none;", **kwargs):
        for label, value, title in items:
            v3.VBtn(label, value=value, size="small", title=title)


def _mode_toggle() -> None:
    """``image | data``; data greyed out (with the reason) where it cannot work."""
    with v3.VBtnToggle(v_model=("fv_mode",), mandatory=True, density="compact", variant="outlined",
                       divided=True, style="flex: none;"):
        v3.VBtn("image", value="image", size="small", title="a picture rendered in the solver")
        with html.Span(title=("fv_data_ok ? 'Catalyst Live: the data, drawn here (rotate, zoom)' : fv_data_why",)):
            v3.VBtn("data", value="data", size="small", disabled=("!fv_data_ok",))


def _show_toggle() -> None:
    """What is drawn: the surface, a slice, a clip, or (data mode) the whole volume."""
    with v3.VBtnToggle(v_model=("fv_show",), mandatory=True, density="compact", variant="outlined",
                       divided=True, style="flex: none;"):
        v3.VBtn("surface", value="surface", size="small", title="the boundary surface")
        v3.VBtn("slice", value="slice", size="small", title="a plane through the domain")
        v3.VBtn("clip", value="clip", size="small", title="the domain cut by a plane, one side kept")
        v3.VBtn("volume", value="volume", size="small", v_if="fv_mode === 'data'",
                title="the whole grid travels")


def _cut_controls() -> None:
    """Axis, position and (clip) side of the cut; shown for a slice or a clip."""
    with html.Div(classes="nf-field-cut", v_show="fv_show === 'slice' || fv_show === 'clip'"):
        _toggle("fv_axis", [(a, a, f"cut normal to {a}") for a in "xyz"])
        v3.VSlider(v_model_number=("fv_pos",), min=0, max=100, step=0.5, density="compact", hide_details=True,
                   title="where along the axis (% of the domain)")
        html.Span(f"{{{{ {_COORD} }}}}", classes="nf-field-coord")
        v3.VCheckbox(v_model=("fv_flip",), label="flip", hide_details=True, density="compact",
                     style="flex: none;", v_show="fv_show === 'clip'", title="keep the upper side")


def _colour_menu() -> None:
    """Colour range (auto or fixed), colour map and log scale, behind the palette button."""
    with v3.VMenu(close_on_content_click=False, location="bottom"):
        with v3.Template(v_slot_activator="{ props }"):
            v3.VBtn(v_bind="props", icon="mdi-palette", size="small", variant="text", title="colours")
        with v3.VCard(classes="pa-3", min_width=260):
            _toggle("fv_range", [("auto", "auto", "the data's range"), ("fixed", "fixed", "a range of your own")])
            with html.Div(classes="d-flex ga-2 mt-2", v_show="fv_range === 'fixed'"):
                v3.VTextField(v_model_number=("fv_lo",), type="number", label="min", density="compact",
                              hide_details=True, variant="outlined")
                v3.VTextField(v_model_number=("fv_hi",), type="number", label="max", density="compact",
                              hide_details=True, variant="outlined")
            v3.VSelect(v_model=("fv_cmap",), items=("fv_cmaps", _CMAP_ITEMS), label="colour map",
                       density="compact", hide_details=True, variant="outlined", classes="mt-3")
            v3.VCheckbox(v_model=("fv_log",), label="log scale", hide_details=True, density="compact")


def _bar() -> None:
    with html.Div(classes="nf-field-bar"):
        _mode_toggle()
        _show_toggle()
        _cut_controls()
        v3.VSelect(v_model=("fv_field",), items=("fv_fields",), label="field", density="compact",
                   hide_details=True, variant="outlined", disabled=("!fv_fields.length",))
        v3.VSelect(v_model=("fv_comp",), items=("fv_comps",), label="component", density="compact",
                   hide_details=True, variant="outlined", v_show="fv_comps.length")
        _colour_menu()
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


def _camera_buttons() -> None:
    """Camera presets and zoom, over the picture (the ranks render them; data mode: this view)."""
    with html.Div(classes="nf-field-cam", v_show="fv_mode === 'image' ? fv_src : fv_data_shown"):
        v3.VBtn(icon="mdi-fit-to-screen-outline", size="x-small", variant="text", title="reset the camera",
                click="trigger('nf_fv_camera', ['reset'])")
        for side in ("+x", "-x", "+y", "-y", "+z", "-z"):
            v3.VBtn(side, size="x-small", variant="text", title=f"look from {side}",
                    click=f"trigger('nf_fv_camera', ['{side}'])")
        v3.VBtn(icon="mdi-magnify-plus-outline", size="x-small", variant="text", title="zoom in",
                click="trigger('nf_fv_zoom', [1.25])")
        v3.VBtn(icon="mdi-magnify-minus-outline", size="x-small", variant="text", title="zoom out",
                click="trigger('nf_fv_zoom', [0.8])")


def _stage(data: Any) -> None:
    """The picture (image mode) or the interactive view (data mode), with the camera buttons."""
    with html.Div(classes="nf-field-stage"):
        client.ClientTriggers(mounted=_SIZE_PROBE)
        _camera_buttons()
        html.Img(src=("fv_src",), classes="nf-field-img", v_show="fv_mode === 'image' && fv_src",
                 alt="in-situ view", raw_attrs=['@wheel.prevent="trigger(\'nf_fv_zoom\', [$event.deltaY < 0 ? 1.25 : 0.8])"'])
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
