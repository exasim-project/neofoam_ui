"""Post-processing pane: the Catalyst/ParaView pipeline as a diagram, beside the field view.

Rank 0 reads the live pipeline back after each in-situ execute
(:meth:`neofoam_ui.catalyst.InSitu.describe`) — sources, filters, displays, views
and extractors with a few settings and the time each took — and the worker sends
it with the frame. Another script (``NEOFOAM_UI_CATALYST_SCRIPT``) shows as itself.
"""

from __future__ import annotations

from typing import Any

from trame.widgets import html
from trame.widgets import vuetify3 as v3
from trame_flow.widgets.flow import Background, Controls, CustomNode, Handle, NodeEditor

__all__ = ["PIPELINE_CSS", "pipeline_pane"]

PIPELINE_CSS = r"""
.nf-pp { flex: 1 1 0; }
.nf-pp-flow { flex: 1 1 auto; min-height: 0; position: relative; }
.nf-pp-node { border: 1.4px solid #8a94a6; background: #f7f8fb; border-radius: 10px; display: flex;
  padding: 0 10px 0 8px; gap: 8px; font-size: 12px; color: #1d2430; }
.nf-pp-node .nf-stripe { margin: 7px 0; }
.nf-pp-body { flex: 1; min-width: 0; padding-top: 5px; }
.nf-pp-head { display: flex; gap: 6px; align-items: baseline; font-size: 13px; font-weight: 600; }
.nf-pp-type { font-size: 11px; opacity: .6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nf-pp-kv { display: grid; grid-template-columns: auto 1fr; gap: 0 8px; margin-top: 3px; line-height: 15px; }
.nf-pp-kv > span:nth-child(odd) { opacity: .6; }
.nf-pp-kv > span:nth-child(even) { font-family: ui-monospace, Menlo, monospace; font-size: 11px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
"""


def pipeline_pane() -> Any:
    """The pane (header + diagram); shown while ``pp_on``. Returns its node editor."""
    with html.Div(classes="nf-field nf-pp", v_show="pp_on"):
        with html.Div(classes="nf-field-bar"):
            v3.VIcon("mdi-graph-outline", size="small")
            html.Span("Post-processing pipeline", classes="text-body-2 font-weight-medium")
            html.Span("{{ pp_msg }}", classes="nf-field-msg")
        with html.Div(classes="nf-pp-flow", v_show="pp_graph"):
            with NodeEditor(style="width: 100%; height: 100%;") as editor:
                Background(gap=16, size=1, pattern_color="#c3c8d2")
                Controls(show_interactive=False)
                with CustomNode("pp"):
                    with html.Div(classes="nf-node nf-pp-node"):
                        Handle(position="top", type="target", id="t")
                        Handle(position="bottom", type="source", id="b")
                        html.Span(classes="nf-stripe", style=("`background:${props.data.color}`",))
                        with html.Div(classes="nf-pp-body"):
                            with html.Div(classes="nf-pp-head"):
                                html.Span("{{ props.data.label }}", classes="nf-label")
                                html.Span("{{ pp_ms[props.id] }}", classes="nf-meta", v_if="pp_ms[props.id]",
                                          title="time of the last execute (rank 0)")
                            html.Div("{{ props.data.kind }} · {{ props.data.type }}", classes="nf-pp-type")
                            with html.Div(classes="nf-pp-kv"):
                                with html.Template(v_for="(r, i) in props.data.settings", key="i"):
                                    html.Span("{{ r[0] }}")
                                    html.Span("{{ r[1] }}", title=("r[1]",))
                with CustomNode("activity"):
                    with html.Div(classes="nf-node nf-activity"):
                        html.Div("{{ props.data.label }}", classes="nf-activity-title")
            editor._attributes["min_zoom"] = ':min-zoom="0.1"'
        html.Div("{{ pp_msg }}", classes="nf-field-empty", v_show="!pp_graph")
    return editor
