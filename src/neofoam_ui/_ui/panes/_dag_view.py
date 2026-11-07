"""The DAG pane's widgets: the activity diagram, its inspector, and the containment tree."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from trame.widgets import html
from trame.widgets import vuetify3 as v3
from trame_flow.widgets.flow import Background, Controls, CustomNode, Handle, MiniMap, NodeEditor

if TYPE_CHECKING:
    from neofoam_ui._ui.panes._dag import _DagPane

__all__ = ()

#: Background of a node in the timing heat map (``heat`` holds a CSS colour per node id).
_HEAT = "heat[props.id] ? `background:${heat[props.id]}` : ''"


def _canvas(pane: _DagPane, minimap: str) -> None:
    """The diagram (``minimap``: when its minimap shows)."""
    html.Div("{{ status }}", classes="nf-status",
             style=("failed ? 'color:#d22' : running ? '' : 'color:#e0362c'",))
    with html.Div(classes="nf-regions", v_if="regions.length"):
        with html.Span(v_for="r in regions", key="r.name", classes="nf-region"):
            html.Span(classes="nf-dot", style=("`background:${r.color}`",))
            html.Span("{{ r.name }}")
    cls_node = "props.id === cur_node && cur_kind !== 'test'"
    with NodeEditor(style="width: 100%; height: 100%;",
                    node_click=(pane.select, "[$event.node.id]")) as editor:
        pane.editor = editor
        Background(gap=16, size=1, pattern_color="#c3c8d2")
        Controls(show_interactive=False)
        MiniMap(pannable=True, zoomable=True, v_show=minimap)

        with CustomNode("action"):
            with html.Div(classes=(
                "['nf-node', 'nf-action', { cur: " + cls_node + ", sel: props.id === selected,"
                " done: visited.includes(props.id) && props.id !== cur_node, inj: !!props.data.injected_by,"
                " hlw: hl[props.id] === 'w', hlr: hl[props.id] === 'r' }]",
            ), style=(_HEAT,)):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                Handle(position="right", type="source", id="r")
                html.Span(classes=("['nf-bp', { on: bps.includes(props.id) }]",),
                          raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.id])"'])
                html.Span(classes="nf-stripe", style=("`background:${props.data.color}`",))
                html.Span("{{ props.data.label }}", classes="nf-label")
                html.Span("{{ heat_ms[props.id] || (props.data.guard ? '[' + props.data.guard + '] '"
                          " : '') + props.data.number }}", classes="nf-meta")
                html.Span("⊕ {{ props.data.injected_by }}", classes="nf-badge",
                          v_if="props.data.injected_by")

        with CustomNode("init"):
            with html.Div(classes=(
                "['nf-node', 'nf-init', { cur: " + cls_node + ", sel: props.id === selected,"
                " done: visited.includes(props.id) && props.id !== cur_node }]",
            ), style=(_HEAT,)):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                html.Span(classes=("['nf-bp', { on: bps.includes(props.id) }]",),
                          raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.id])"'])
                html.Span(classes="nf-stripe", style=("`background:${props.data.color}`",))
                html.Span("{{ props.data.label }}", classes="nf-label")
                html.Span("{{ heat_ms[props.id] }}", classes="nf-meta", v_if="heat_ms[props.id]")

        with CustomNode("loop"):
            with html.Div(classes=(
                "['nf-node', 'nf-loop', { active: active_loops.includes(props.id),"
                " collapsed: props.data.collapsed, sel: props.data.collapsed && props.id === selected,"
                " hlw: hl[props.id] === 'w', hlr: hl[props.id] === 'r' }]",
            ), style=("props.data.collapsed ? " + _HEAT + " : ''",)):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                with html.Div(classes="nf-loop-title"):
                    html.Span("{{ props.data.collapsed ? '▸' : '▾' }}", classes="nf-tog",
                              title="collapse / expand",
                              raw_attrs=['@click.stop="trigger(\'nf_collapse\', [props.id])"'])
                    html.Span("«loop» {{ props.data.number }} {{ props.data.label }}")
                    html.Span(" · pass {{ loop_iter[props.id] }}", classes="nf-loop-pass",
                              v_if="loop_iter[props.id]")
                html.Div("test: {{ props.data.test }}", classes="nf-loop-test")
                html.Div("{{ heat_ms[props.id] ? heat_ms[props.id] + ' · ' : '' }}"
                         "{{ props.data.size }} actions", classes="nf-loop-size",
                         v_if="props.data.collapsed")
                html.Span(classes=("['nf-bp', { on: bps.includes(props.id) }]",),
                          v_if="props.data.collapsed",
                          raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.id])"'])

        with CustomNode("decision"):
            with html.Div(classes=(
                "['nf-decision', { cur: props.data.loop === cur_node && cur_kind === 'test',"
                " sel: props.data.loop === selected }]",
            )):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                Handle(position="right", type="target", id="r")
                html.Div(classes="d")
                html.Span(classes=("['nf-bp', { on: bps.includes(props.data.loop) }]",),
                          raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.data.loop])"'])

        with CustomNode("activity"):
            with html.Div(classes="nf-node nf-activity"):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                html.Div("{{ props.data.label }}", classes="nf-activity-title")

        with CustomNode("initial"):
            with html.Div(classes="nf-initial"):
                Handle(position="bottom", type="source", id="b")

        with CustomNode("final"):
            with html.Div(classes=("['nf-final', { cur: cur_node === 'end' }]",)):
                Handle(position="top", type="target", id="t")

        with CustomNode("placeholder"):
            with html.Div(classes="nf-node nf-placeholder"):
                html.Span("execution graph appears after initialization (it needs the run context)")

    editor._attributes["min_zoom"] = ':min-zoom="0.1"'


def _inspector(step: Callable[[str, Optional[str]], Any]) -> None:
    """The selected node, the call stack and the breakpoints (``step``: "Run to here")."""
    html.Div("Inspector", classes="nf-h")
    with html.Div(v_if="insp"):
        with html.Div(classes="nf-sect"):
            with html.Div(classes="px-3 pb-1 d-flex align-center", style="gap: 8px;"):
                html.Span(classes="nf-dot", style=("`background:${insp.color}`",))
                html.Span("{{ insp.name }}", classes="text-subtitle-1 font-weight-bold")
            html.Div("{{ insp.type }}", classes="px-3 text-caption", style="opacity:.7")
            with html.Div(classes="nf-kv mt-1"):
                with html.Template(v_for="r in insp.rows", key="r.k"):
                    html.Span("{{ r.k }}")
                    html.Span("{{ r.v }}", classes="nf-mono", style="word-break: break-all;")
            html.Div("{{ insp.doc }}", v_if="insp.doc", classes="px-3 mt-2 text-body-2",
                     style="opacity:.8")
        with html.Div(classes="nf-sect"):
            html.Div("Navigate", classes="nf-h")
            with html.Div(classes="px-3"):
                v3.VChip("{{ n.dir }} {{ n.name }}", v_for="n in insp.nav", key="n.dir + n.id",
                         size="small", classes="mr-1 mb-1", variant="tonal",
                         click="trigger('nf_select', [n.id])")
        with html.Div(classes="nf-sect", v_if="insp.seen.length"):
            html.Div("Observed writes", classes="nf-h")
            with html.Div(classes="px-3"):
                v3.VChip("{{ f }}", v_for="f in insp.seen", key="'s' + f", size="x-small",
                         color="warning", variant="tonal", classes="mr-1 mb-1",
                         prepend_icon="mdi-pencil", click="trigger('nf_field', [f])")
        with html.Div(classes="nf-sect", v_if="insp.reads.length || insp.writes.length"):
            html.Div("Reads / writes (declared)", classes="nf-h")
            with html.Div(classes="px-3"):
                v3.VChip("{{ f }}", v_for="f in insp.reads", key="'r' + f", size="x-small",
                         classes="mr-1 mb-1", prepend_icon="mdi-arrow-down")
                v3.VChip("{{ f }}", v_for="f in insp.writes", key="'w' + f", size="x-small",
                         color="warning", classes="mr-1 mb-1", prepend_icon="mdi-pencil")
        with html.Div(classes="nf-sect px-3 pt-2"):
            v3.VBtn("{{ insp.bp ? 'Remove breakpoint' : 'Add breakpoint' }}", size="small",
                    variant="tonal", classes="mr-2", click="trigger('nf_toggle_bp', [insp.id])")
            v3.VBtn("Run to here", size="small", variant="tonal",
                    disabled=("!connected || running || finished",),
                    click=(lambda node: step("run_to", node), "[insp.id]"))
    html.Div("Select a node.", v_if="!insp", classes="px-3 text-caption")
    html.Div("Call stack", classes="nf-h mt-2")
    with html.Div(classes="nf-stack"):
        html.Div("{{ c }}", v_for="(c, i) in callstack", key="i")
    html.Div("Breakpoints", classes="nf-h mt-2")
    with html.Div(classes="nf-tree"):
        with html.Div(v_for="b in bp_list", key="b.id", click="trigger('nf_select', [b.id])"):
            html.Span("●", classes="nf-bpmark")
            html.Span("{{ b.name }}")
            v3.VSpacer()
            v3.VIcon("mdi-close", size="x-small", raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [b.id])"'])
    html.Div("Click the gutter left of a node to add one.", v_if="!bp_list.length",
             classes="px-3 text-caption", style="opacity:.6")


def _drawer(drawer: Any) -> None:
    """The containment tree and the legend."""
    drawer.width = 250
    drawer.temporary = ("is_mobile",)
    html.Div("Containment", classes="nf-h")
    with html.Div(classes="nf-tree"):
        with html.Div(
            v_for="n in tree", key="n.id",
            classes=("{ sel: n.id === selected, cur: n.id === cur_node, done: visited.includes(n.id) && n.id !== cur_node }",),
            style=("`padding-left: ${12 + n.depth * 14}px`",),
            click="trigger('nf_select', [n.id])",
        ):
            html.Span("{{ bps.includes(n.id) ? '●' : '' }}", classes="nf-bpmark")
            html.Span(classes="nf-dot", style=("`background:${n.color}`",),
                      v_if="n.kind !== 'activity'")
            html.Span("{{ n.kind === 'loop' ? '↺ ' : '' }}{{ n.name }}",
                      style=("n.kind === 'activity' ? 'font-weight:600; opacity:.75' : ''",))
    html.Div("Legend", classes="nf-h mt-2")
    with html.Div(classes="text-caption px-3", style="line-height: 1.9; opacity: .8;"):
        html.Div("▭ action (SequentialOp)")
        html.Div("▢ loop node (IterativeOp), ◇ loop test")
        html.Div("dashed ▭ ⊕ injected by an optional model")
        html.Div("red = next to execute (token)")
        html.Div("green = executed this time step")
        html.Div("● breakpoint — click the gutter left of a node")
        html.Div("▾ / ▸ on a loop: collapse / expand its body")
        html.Div("click a field: ops that wrote (orange) / read (blue) it")
        html.Div("stripe colour: the op's region (multi-region), else its model", v_if="regions.length")
