"""The layout: panels in a dock (views) and sidebars (side panels), launchers, the phone's stack,
remembered per browser; no Pipeline pane.

The browser-side controller (``window.nfL``) runs under Node with a stub of trame and of the
dock, where Node is installed.
"""

import json
import re
import shutil
import subprocess

import pytest
from trame.app import get_server

from neofoam_ui._ui import _dock
from neofoam_ui.app import StepperApp

VIEWS = ["step", "plots", "cfd", "console", "fields"]
SIDE = ["tree", "breakpoints", "scenes", "inspector", "callstack", "vars", "options", "legend"]


@pytest.fixture(scope="module")
def built():
    server = get_server("dock_layout", client_type="vue3")
    app = StepperApp(server, "neofoam_ui.toy.heat1d:Heat1D")
    return server, app, server.state._pending_update


def _script(pending):
    return next(v for k, v in pending.items() if k.startswith("trame__inline_script_"))


def test_every_panel_is_a_template_with_a_launcher(built):
    _, app, pending = built
    assert [p.id for p in app.panels] == VIEWS + SIDE
    assert [p.kind for p in app.panels] == ["main"] * len(VIEWS) + ["side"] * len(SIDE)
    main = pending["trame__template_main"]
    for p in app.panels:
        assert f"trame__template_{p.template}" in pending
        # launchers: the activity bar (a side panel's: in both, one shows), the phone's picker
        # and drawer; Options also the toolbar's ⚙
        assert main.count(f"window.nfL.toggle('{p.id}')") == (4 if p.kind == "side" else 3) + (p.id == "options")
    # the side panels live in both sidebars (one shows) and in the phone's stack
    for p in app.panels:
        n = main.count(f'<trame-template templateName="{p.template}" />')
        assert n == (3 if p.kind == "side" else 1)
    # the views' flags: what the server must know
    assert {p.id: p.flag for p in app.panels if p.flag} == {"step": "dag_on", "plots": "pt_on", "cfd": "fv_on"}
    assert "nfL.toggle('options')" in main.split("</header>")[0]  # ⚙ in the toolbar


def test_the_panels_keep_their_widgets(built):
    _, _, pending = built
    assert "<node-editor" in pending["trame__template_nf_p_step"]
    assert "trigger('nf_dag_mounted')" in pending["trame__template_nf_p_step"]
    assert "fv_src" in pending["trame__template_nf_p_cfd"]
    assert "pt_charts" in pending["trame__template_nf_p_plots"]
    assert "logs" in pending["trame__template_nf_p_console"]
    assert "fields_rows" in pending["trame__template_nf_p_fields"]
    assert "Run to here" in pending["trame__template_nf_p_inspector"]
    assert "callstack" in pending["trame__template_nf_p_callstack"]
    assert "bp_list" in pending["trame__template_nf_p_breakpoints"]
    assert "n in tree" in pending["trame__template_nf_p_tree"]
    assert "scalars" in pending["trame__template_nf_p_vars"]
    options = pending["trame__template_nf_p_options"]
    assert all(k in options for k in ("delay_ms", "wait_ui", "follow", "heat_on", "minimap_on", "nfL.reset()"))


def test_no_pipeline_pane_and_no_title(built):
    _, app, pending = built
    assert all(p.name != "pipeline" for p in app.panes)
    assert "pp_on" not in pending and not any("pipeline" in k for k in pending)
    main = pending["trame__template_main"]
    assert "DAG stepper" not in main and "Pipeline" not in main


def test_the_layout_is_the_browsers(built):
    server, _, pending = built
    assert {"nf_lay", "nf_dk", "nf_phone", "nf_drawer"} <= set(pending["trame__client_only"])
    main = pending["trame__template_main"]
    dock = re.search(r"<dock-view .*/>", main).group(0)
    for attr in ('@ready="window.nfL.ready()"', '@layoutChanged="window.nfL.layout($event)"',
                 '@panelVisibility="window.nfL.vis($event)"', '@removePanel="window.nfL.removed($event)"',
                 "trame.refs['nf_dock']"):
        assert attr in dock
    script = _script(pending)
    assert json.dumps(_dock._KEY) in script and "localStorage" in script
    assert "nfL.reset()" in main  # the footer's (and the drawer's) Reset layout
    assert server.state.nf_phone is None  # the browser decides, before anything is laid out


def test_a_mounted_step_view_gets_the_graph_again(built):
    _, app, _ = built
    calls = []
    app.server.js_call = lambda ref, method, *args: calls.append(method)
    app.dag.mounted()
    assert calls == ["setNodes", "setEdges", "fitView"]


# -- the browser side ------------------------------------------------------------------------

_HARNESS = r"""
const store = {};
let phone = false;
const calls = [];
const state = {};
global.window = {
  matchMedia: () => ({ get matches() { return phone; }, addEventListener() {} }),
  localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; },
                  removeItem: k => { delete store[k]; } },
  setTimeout: f => f(),
  scrollTo() {},
  trame: { state: { get: k => state[k], set: (k, v) => { state[k] = v; } },
           refs: { nf_dock: new Proxy({}, { get: (_, m) => (...a) => calls.push([m, ...a]) }) } },
};
const load = () => { eval(SCRIPT); return window.nfL; };
const out = {};
let L = load();
out.boot = { phone: state.nf_phone, lay: state.nf_lay, dag_on: state.dag_on };
L.ready();
out.defaultDock = calls.splice(0).map(c => [c[0], c[1], c[4] && c[4].position || null]);
['step', 'console'].forEach(id => L.vis({ id, visible: true }));
['plots', 'cfd', 'fields'].forEach(id => L.vis({ id, visible: false }));
out.shown = { dag_on: state.dag_on, pt_on: state.pt_on, fv_on: state.fv_on, dk: state.nf_dk };
L.toggle('plots');  // open behind a tab: to the front
L.toggle('step');   // shows: close
L.toggle('fields'); // (stays as the dock's events have it)
out.toggles = calls.splice(0).map(c => [c[0], c[1]]);
L.removed('step');
out.closed = { dag_on: state.dag_on, open: state.nf_dk.open };
L.toggle('vars'); L.move('vars'); L.collapse('tree'); L.close('inspector');
out.side = state.nf_lay;
L.layout({ grid: { root: {} }, panels: { plots: {}, console: {} } });
L.layout({ grid: { root: {} }, panels: { nonsense: {} } });  // not ours: ignored
// a new page in the same browser
L = load();
L.ready();
out.restored = { calls: calls.splice(0), lay: state.nf_lay };
['plots', 'console'].forEach(id => L.vis({ id, visible: true }));  // as the restored dock reports
// the phone
phone = true;
L.toggle('plots');
out.phone = { list: state.nf_lay.phone, pt_on: state.pt_on, dag_on: state.dag_on, nf_phone: state.nf_phone };
L.collapse('plots');
out.phoneCollapsed = { pt_on: state.pt_on };
L.only('step');
out.only = state.nf_lay.phone;
phone = false;
L.reset();
out.reset = { stored: JSON.parse(store[KEY]), calls: calls.splice(0).map(c => [c[0], c[1]]) };
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_controller_in_a_browser(built, tmp_path):
    _, _, pending = built
    js = (f"const SCRIPT = {json.dumps(_script(pending))};\nconst KEY = {json.dumps(_dock._KEY)};\n"
          + _HARNESS)
    path = tmp_path / "harness.js"
    path.write_text(js)
    out = json.loads(subprocess.run(["node", str(path)], check=True, capture_output=True, text=True).stdout)

    assert out["boot"]["phone"] is False and out["boot"]["dag_on"] is False  # until the dock tells
    assert out["boot"]["lay"]["side"]["tree"] == {"side": "left", "open": True, "ord": 0}
    assert out["boot"]["lay"]["col"] == {"breakpoints": True} and out["boot"]["lay"]["phone"] == ["step"]
    # the default dock: step view, plots, CFD as tabs; console and the field table below
    assert out["defaultDock"] == [
        ["addPanel", "step", None],
        ["addPanel", "plots", {"referencePanel": "step", "direction": "within"}],
        ["addPanel", "cfd", {"referencePanel": "step", "direction": "within"}],
        ["addPanel", "console", {"referencePanel": "step", "direction": "below"}],
        ["addPanel", "fields", {"referencePanel": "console", "direction": "within"}]]
    # a flag follows what shows: behind a tab is hidden
    assert out["shown"]["dag_on"] is True and out["shown"]["pt_on"] is False and out["shown"]["fv_on"] is False
    assert out["shown"]["dk"]["vis"] == {"step": True, "console": True, "plots": False, "cfd": False, "fields": False}
    # a launcher: brings a hidden view to the front, closes a shown one
    assert out["toggles"] == [["activePanel", "plots"], ["removePanel", "step"], ["activePanel", "fields"]]
    assert out["closed"]["dag_on"] is False and "step" not in out["closed"]["open"]
    side = out["side"]
    assert side["side"]["vars"]["open"] and side["side"]["vars"]["side"] == "left"
    assert side["side"]["vars"]["ord"] == 3  # moved: below the left sidebar's others (tree, breakpoints, scenes)
    assert side["col"].get("tree") and not side["side"]["inspector"]["open"]
    # remembered: the dock's layout and the sidebars
    assert out["restored"]["calls"] == [["restoreLayout", {"grid": {"root": {}}, "panels": {"plots": {}, "console": {}}}]]
    assert out["restored"]["lay"]["side"]["vars"]["side"] == "left" and out["restored"]["lay"]["col"].get("tree")
    # the phone: a tapped panel on top of the stack, its flag on while it shows
    assert out["phone"] == {"list": ["plots", "step"], "pt_on": True, "dag_on": True, "nf_phone": True}
    assert out["phoneCollapsed"]["pt_on"] is False
    assert out["only"] == ["step"]
    # reset: the defaults, the dock's views replaced by the default ones
    assert out["reset"]["stored"]["side"]["vars"] == {"side": "right", "open": False, "ord": 2}
    assert out["reset"]["stored"]["dock"] is None
    assert [c[1] for c in out["reset"]["calls"] if c[0] == "removePanel"] == ["plots", "console"]
    assert [c[1] for c in out["reset"]["calls"] if c[0] == "addPanel"] == VIEWS
