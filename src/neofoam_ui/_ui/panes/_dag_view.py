"""The DAG pane's widgets: the activity diagram (the step view) and its side panels: inspector,
call stack, breakpoints, containment tree and legend."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from trame.widgets import html
from trame.widgets import vuetify3 as v3
from trame_flow.widgets.flow import Background, Controls, CustomNode, Handle, MiniMap, NodeEditor

if TYPE_CHECKING:
    from neofoam_ui._ui.panes._dag import _DagPane

__all__ = ()

#: An op marked final (runs in the final pass of its wrapping loops only): its badge, a toggle
#: to run it in every pass instead (``every_pass``, its region's choice).
_EVERY = "every_pass.includes(props.data.ref)"
_EVERY_TITLE = ("(" + _EVERY + " ? 'runs in every pass (debug)' : 'runs in the final pass only')"
                " + ' of its coupling loops: click to toggle'")

#: Background of a node in the timing heat map (``heat`` holds a CSS colour per node id). Nodes
#: bind to ``data.ref``, the op they stand for.
_HEAT = "heat[props.data.ref] ? `background:${heat[props.data.ref]}` : ''"


def _canvas(pane: _DagPane, minimap: str) -> None:
    """The diagram (``minimap``: when its minimap shows)."""
    with html.Div(classes="nf-regions", v_if="regions.length"):
        with html.Span(v_for="r in regions", key="r.name", classes="nf-region"):
            html.Span(classes="nf-dot", style=("`background:${r.color}`",))
            html.Span("{{ r.name }}")
    # a token per region (cur_nodes; one for a single-region solver); a region waiting for its
    # partner's data marks where it waits (wait_nodes)
    cls_node = "cur_nodes.includes(props.data.ref)"
    cls_wait = "wait_nodes.includes(props.data.ref)"
    # hot-<key>: the coupling line <key> exchanges now (see _styles: its dashes move)
    with NodeEditor(style="width: 100%; height: 100%;", classes=("ex_hot.map(k => 'hot-' + k)",),
                    node_click=(pane.select, "[$event.node.data && ($event.node.data.ref || $event.node.data.loop)"
                                       " || $event.node.id]"),
                    # a tap on the background closes a mapping node's popover (it shows while focused)
                    pane_click="document.activeElement && document.activeElement.blur()") as editor:
        pane.editor = editor
        Background(gap=16, size=1, pattern_color="#c3c8d2")
        Controls(show_interactive=False)
        MiniMap(pannable=True, zoomable=True, v_show=f"minimap_on && {minimap}")

        with CustomNode("action"):
            with html.Div(classes=(
                "['nf-node', 'nf-action', { cur: " + cls_node + ", wait: " + cls_wait + ", sel: props.data.ref === selected,"
                " done: visited.includes(props.data.ref) && !cur_nodes.includes(props.data.ref), inj: !!props.data.injected_by,"
                " cpl: !!props.data.exchange, hlw: hl[props.data.ref] === 'w', hlr: hl[props.data.ref] === 'r' }]",
            ), style=(_HEAT,)):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                Handle(position="right", type="source", id="r")
                # the lines of a coupling op, in the gap between region boxes
                Handle(position="left", type="source", id="ls")
                Handle(position="left", type="target", id="lt")
                Handle(position="right", type="target", id="rt")
                html.Span(classes=("['nf-bp', { on: bps.includes(props.data.ref) }]",),
                          raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.data.ref])"'])
                html.Span(classes="nf-stripe", style=("`background:${props.data.color}`",))
                html.Span("{{ props.data.label }}", classes="nf-label")
                html.Span("{{ heat_ms[props.data.ref] || (props.data.guard ? '[' + props.data.guard + '] '"
                          " : '') + props.data.number }}", classes="nf-meta")
                html.Span("⊕ {{ props.data.injected_by }}", classes="nf-badge",
                          v_if="props.data.injected_by")
                html.Span("⇄ {{ props.data.exchange }}", classes="nf-cpl", v_if="props.data.exchange")
                _every_pass_toggle()

        with CustomNode("init"):
            with html.Div(classes=(
                "['nf-node', 'nf-init', { cur: " + cls_node + ", sel: props.data.ref === selected,"
                " done: visited.includes(props.data.ref) && !cur_nodes.includes(props.data.ref) }]",
            ), style=(_HEAT,)):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                html.Span(classes=("['nf-bp', { on: bps.includes(props.data.ref) }]",),
                          raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.data.ref])"'])
                html.Span(classes="nf-stripe", style=("`background:${props.data.color}`",))
                html.Span("{{ props.data.label }}", classes="nf-label")
                html.Span("{{ heat_ms[props.data.ref] }}", classes="nf-meta", v_if="heat_ms[props.data.ref]")

        with CustomNode("loop"):
            with html.Div(classes=(
                "['nf-node', 'nf-loop', { active: active_loops.includes(props.data.ref),"
                " control: props.data.control, collapsed: props.data.collapsed, sel: props.data.collapsed && props.data.ref === selected,"
                " hlw: hl[props.data.ref] === 'w', hlr: hl[props.data.ref] === 'r' }]",
            ), style=("props.data.collapsed ? " + _HEAT + " : ''",),
               title=("props.data.control && props.data.test ? 'test: ' + props.data.test : ''",)):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                # a loop in a region box: its data goes to a coupling action, which may feed it
                Handle(position="right", type="source", id="r")
                Handle(position="left", type="source", id="ls")
                Handle(position="left", type="target", id="lt")
                Handle(position="right", type="target", id="rt")
                with html.Div(classes="nf-loop-title"):
                    html.Span("{{ props.data.collapsed ? '▸' : '▾' }}", classes="nf-tog",
                              title="collapse / expand",
                              raw_attrs=['@click.stop="trigger(\'nf_collapse\', [props.data.ref])"'])
                    html.Span("{{ props.data.control ? '' : '«loop» ' }}{{ props.data.number }} {{ props.data.label }}")
                    html.Span(" · pass {{ loop_iter[props.data.ref] }}", classes="nf-loop-pass",
                              v_if="loop_iter[props.data.ref]")
                    html.Span(" · {{ loop_res[props.data.ref] }}", classes="nf-loop-res",
                              v_if="loop_res[props.data.ref]")
                    html.Span("{{ props.data.tag }}", classes="nf-tag", v_if="props.data.tag",
                              title="decides whether the coupling group runs another pass")
                    html.Span("final pass", classes="nf-tag nf-final-pass", v_if="loop_final[props.data.ref]",
                              title="the pass running now is the loop's last: the ops marked final run in it")
                _every_pass_toggle()
                html.Div("test: {{ props.data.test }}", classes="nf-loop-test", v_if="!props.data.control")
                html.Div("⇄ {{ props.data.exchange }}", classes="nf-cpl nf-loop-cpl", v_if="props.data.exchange")
                html.Div("{{ heat_ms[props.data.ref] ? heat_ms[props.data.ref] + ' · ' : '' }}"
                         "{{ props.data.size }} actions", classes="nf-loop-size",
                         v_if="props.data.collapsed")
                html.Span(classes=("['nf-bp', { on: bps.includes(props.data.ref) }]",),
                          v_if="props.data.collapsed",
                          raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.data.ref])"'])

        with CustomNode("exmap"):
            # an interface's mapping node, on its coupling line between the boxes: a chip with
            # the mapping kind, lit while the exchange runs. Hover (a tap focuses it) shows the
            # rest in a popover, live: each port's points and, per way, what the sending port
            # sends now, else the fields, marked mapped / as is / ? (see _styles)
            with html.Div(classes=("['nf-exmap', { hot: ex_hot.includes(props.data.key), unk: !props.data.kind }]",),
                          tabindex="0"):
                Handle(position="left", type="target", id="lt")
                Handle(position="right", type="source", id="r")
                html.Span("≈", classes="nf-map-icon")
                html.Span("{{ props.data.kind || '?' }}", classes="nf-map-kind")
                with html.Div(classes="nf-map-tip"):
                    html.Div("exchange {{ props.data.name }}", classes="nf-map-op", v_if="props.data.name")
                    with html.Div(classes="nf-map-head"):
                        html.Span("⇆ mapping", classes="nf-map-title")
                        html.Span("{{ props.data.kind || 'unknown: the solver does not tell' }}",
                                  classes="nf-map-tkind")
                    html.Div("{{ props.data.left }} · {{ props.data.points[props.data.left] ?? '?' }} pt"
                             "  ⇄  {{ props.data.right }} · {{ props.data.points[props.data.right] ?? '?' }} pt",
                             classes="nf-map-pts")
                    with html.Div(v_for="w in props.data.ways", key="w.from", classes="nf-map-way"):
                        html.Span("{{ w.from }} → {{ w.to }}", classes="nf-map-dir")
                        html.Span("{{ w.fields.map(f => f.name + (f.mapped === true ? ' (mapped)' : f.mapped === false"
                                  " ? ' (as is)' : ' (?)')).join(', ') }}")
                        html.Div("{{ (ex_vals[props.data.key] || {})[w.from] }}", classes="nf-map-val",
                                 v_if="(ex_vals[props.data.key] || {})[w.from]")

        with CustomNode("decision"):
            with html.Div(classes=(
                "['nf-decision', { cur: cur_tests.includes(props.data.loop),"
                " wait: wait_nodes.includes(props.data.loop), sel: props.data.loop === selected }]",
            )):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                Handle(position="right", type="target", id="r")
                html.Div(classes="d")
                html.Span(classes=("['nf-bp', { on: bps.includes(props.data.loop) }]",),
                          raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [props.data.loop])"'])

        with CustomNode("region"):
            with html.Div(classes=("['nf-box', { closed: props.data.closed }]",),
                          style=("`border-color:${props.data.color}`",)):
                with html.Div(classes="nf-box-head", style=("`color:${props.data.color}`",)):
                    # an execution box collapses to its header and coupling ops
                    html.Span("{{ props.data.closed ? '▸' : '▾' }}", classes="nf-tog", v_if="props.data.toggle",
                              title="collapse / expand this solver",
                              raw_attrs=['@click.stop="trigger(\'nf_box\', [props.data.toggle])"'])
                    html.Span(classes="nf-dot", style=("`background:${props.data.color}`",))
                    html.Span("{{ props.data.label }}")
                    # its region's thread waits for a partner's data (it moves once it arrives)
                    html.Span("⏳ {{ waiting[props.data.region] }}", classes="nf-box-wait",
                              v_if="waiting[props.data.region]")
                # the init view's coupling lines start and end at the box
                Handle(position="right", type="source", id="r")
                Handle(position="left", type="target", id="lt")

        with CustomNode("activity"):
            with html.Div(classes="nf-node nf-activity"):
                Handle(position="top", type="target", id="t")
                Handle(position="bottom", type="source", id="b")
                html.Div("{{ props.data.label }}", classes="nf-activity-title")

        with CustomNode("initial"):
            with html.Div(classes="nf-initial"):
                Handle(position="bottom", type="source", id="b")

        with CustomNode("final"):
            with html.Div(classes=("['nf-final', { cur: cur_node === 'end' || done_regions.includes(props.data.region) }]",)):
                Handle(position="top", type="target", id="t")
                Handle(position="left", type="target", id="lt")  # a loop's [false] exit

        with CustomNode("placeholder"):
            with html.Div(classes="nf-node nf-placeholder"):
                html.Span("execution graph appears after initialization (it needs the run context)")

    editor._attributes["min_zoom"] = ':min-zoom="0.1"'


def _every_pass_toggle() -> None:
    """The badge of an op marked final; a click runs it in every pass (or the final one only)."""
    html.Span("{{ " + _EVERY + " ? '↻ every pass' : 'final' }}",
              classes=("['nf-every', { on: " + _EVERY + " }]",), v_if="props.data.final", title=(_EVERY_TITLE,),
              raw_attrs=['@click.stop="trigger(\'nf_every_pass\', [props.data.ref])"'])


def _inspector(step: Callable[[str, Optional[str]], Any]) -> None:
    """The selected node (``step``: "Run to here")."""
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
    html.Div("Select a node.", v_if="!insp", classes="px-3 py-2 text-caption")


def _callstack() -> None:
    """Where each region is: its op, the loops around it and their passes."""
    with html.Div(classes="nf-stack"):
        html.Div("{{ c }}", v_for="(c, i) in callstack", key="i")


def _breakpoints() -> None:
    """The breakpoints: a click selects the node, × removes it."""
    with html.Div(classes="nf-tree"):
        with html.Div(v_for="b in bp_list", key="b.id", click="trigger('nf_select', [b.id])"):
            html.Span("●", classes="nf-bpmark")
            html.Span("{{ b.name }}")
            v3.VSpacer()
            v3.VIcon("mdi-close", size="x-small", raw_attrs=['@click.stop="trigger(\'nf_toggle_bp\', [b.id])"'])
    html.Div("Click the gutter left of a node to add one.", v_if="!bp_list.length",
             classes="px-3 text-caption", style="opacity:.6")


def _tree() -> None:
    """The containment tree: the init steps and the ops per region; a click selects."""
    with html.Div(classes="nf-tree"):
        with html.Div(
            v_for="n in tree", key="n.id",
            classes=("{ sel: n.id === selected, cur: cur_nodes.includes(n.id) || cur_tests.includes(n.id),"
                     " done: visited.includes(n.id) && !cur_nodes.includes(n.id) }",),
            style=("`padding-left: ${12 + n.depth * 14}px`",),
            click="trigger('nf_select', [n.id])",
        ):
            html.Span("{{ bps.includes(n.id) ? '●' : '' }}", classes="nf-bpmark")
            html.Span(classes="nf-dot", style=("`background:${n.color}`",),
                      v_if="n.kind !== 'activity'")
            html.Span("{{ n.kind === 'loop' ? '↺ ' : '' }}{{ n.name }}",
                      style=("n.kind === 'activity' ? 'font-weight:600; opacity:.75' : ''",))


def _legend() -> None:
    """What the diagram's marks mean."""
    with html.Div(classes="text-caption px-3", style="line-height: 1.9; opacity: .8;"):
        html.Div("▭ action · red = next (token) · green = ran this time step")
        html.Div("( loop ◇ ) loop: pass · residuals; ◇ its test (hover: the test); ▾/▸ collapse")
        html.Div("● breakpoint — click the gutter left of a node")
        html.Div("click a field: ops that wrote (orange) / read (blue) it")
        html.Div("dashed ▭ ⊕ injected by an optional model")
        html.Div("'final': runs in the final pass of its coupling loops only; click: '↻ every pass' (debug)")
        with html.Div(v_if="regions.length"):
            html.Div("▢ a box per region: its own solver, graph and thread (its region's colour);"
                     " a token per box, every step moves all of them")
            html.Div("⏳ amber: the region waits for its partner's data; it moves once that arrives")
            html.Div("⇄ coupling action: hands its op the partner's data")
            html.Div("⇄ (loop) a region's coupling op: its test hands the region the partners' data;"
                     " 'leader': the one that decides whether the group goes on")
            html.Div("- - coupling line, through the interface's ≈ mapping chip (its kind; hover or tap:"
                     " how the data goes onto the partner's points, mapped or as is, the points per port,"
                     " what each port sends); red, moving: exchanging now")
