"""The layout's script: launchers, the dock, the sidebars, the phone's stack, remembered per browser.

The layout is the browser's, not the server's: it lives in client-only state (``nf_lay``:
the sidebars, the collapsed panels and the phone's stack; ``nf_dk``: the dock's open and
visible views; ``nf_phone``: the phone layout is on) and in ``localStorage`` (key
:data:`_KEY`, with the dock's own layout JSON), so every browser keeps its own. What the
server must know goes as ordinary state: each panel's ``flag`` (e.g. ``dag_on``), true while
the panel shows.

``window.nfL`` is what the page's templates call: ``toggle(id)`` (a launcher: open, bring to
the front, close), ``close`` / ``collapse`` / ``move`` (a panel's header), ``reset()``, and
the dock's events ``ready`` / ``vis`` / ``removed`` / ``layout``.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ()

#: ``localStorage`` key of the remembered layout (bump the ``v`` inside for an incompatible change).
_KEY = "neofoam.ui.layout.v1"

#: The page's width at most which the phone layout is used.
_PHONE_PX = 760

_SCRIPT = r"""
(function () {
  const CAT = __CATALOGUE__;
  const DEF = __DEFAULTS__;
  const KEY = __KEY__;
  const BY = Object.fromEntries(CAT.map(p => [p.id, p]));
  const SIDE = CAT.filter(p => p.kind === 'side').map(p => p.id);
  const mq = window.matchMedia('(max-width: __PHONE__px)');
  const T = () => window.trame;
  const dock = () => (T() && T().refs.nf_dock) || null;
  const copy = o => JSON.parse(JSON.stringify(o));
  let L = null;          // nf_lay: {v, side: {id: {side, open, ord}}, col: {id: true}, phone: [ids]}
  let dk = {open: {}, vis: {}};   // the dock's views: open, and visible (not behind a tab)
  let dockJSON = null;   // the dock's layout (api.toJSON()), restored when it mounts

  const validDock = j => !!(j && typeof j === 'object' && j.grid && j.panels && typeof j.panels === 'object'
    && Object.keys(j.panels).every(id => BY[id] && BY[id].kind === 'main'));

  function load() {
    const d = copy(DEF);
    dockJSON = null;
    try {
      const o = JSON.parse(window.localStorage.getItem(KEY) || 'null');
      if (o && o.v === d.v) {
        SIDE.forEach(id => { if (o.side && o.side[id]) Object.assign(d.side[id], o.side[id]); });
        if (o.col && typeof o.col === 'object') d.col = o.col;
        if (Array.isArray(o.phone)) d.phone = o.phone.filter(id => BY[id]);
        if (validDock(o.dock)) dockJSON = o.dock;
      }
    } catch (e) { /* storage blocked or corrupt: the defaults */ }
    return d;
  }
  function save() {
    try { window.localStorage.setItem(KEY, JSON.stringify({...L, dock: dockJSON})); } catch (e) { /* blocked */ }
  }
  const phone = () => mq.matches;
  function shown(id) {
    if (phone()) return L.phone.includes(id) && !L.col[id];
    if (BY[id].kind === 'main') return !!dk.vis[id];
    return !!(L.side[id] && L.side[id].open) && !L.col[id];
  }
  // State changes go out on the next task: one made while the page renders (e.g. the dock's
  // events as it mounts) would not re-render it.
  let pending = false;
  function push() {
    if (pending) return;
    pending = true;
    window.setTimeout(() => { pending = false; pushNow(); }, 0);
  }
  function pushNow() {
    const t = T();
    if (!t || !L) return;
    t.state.set('nf_phone', phone());
    t.state.set('nf_lay', copy(L));
    t.state.set('nf_dk', copy(dk));
    CAT.forEach(p => {
      if (!p.flag) return;
      const on = shown(p.id);
      if (t.state.get(p.flag) !== on) t.state.set(p.flag, on);
    });
    save();
  }
  // the end of a sidebar (for panel ``self``)
  const nextOrd = (side, self) => 1 + Math.max(-1, ...SIDE.filter(id => id !== self && L.side[id].side === side)
    .map(id => L.side[id].ord));

  function defaultDock(d) {
    d.addPanel('step', BY.step.title, BY.step.tmpl, {});
    d.addPanel('plots', BY.plots.title, BY.plots.tmpl, {position: {referencePanel: 'step', direction: 'within'}, inactive: true});
    d.addPanel('editor', BY.editor.title, BY.editor.tmpl, {position: {referencePanel: 'step', direction: 'within'}, inactive: true});
    d.addPanel('cfd', BY.cfd.title, BY.cfd.tmpl, {position: {referencePanel: 'step', direction: 'within'}, inactive: true});
    d.addPanel('console', BY.console.title, BY.console.tmpl, {position: {referencePanel: 'step', direction: 'below'}, initialHeight: 260});
    d.addPanel('fields', BY.fields.title, BY.fields.tmpl, {position: {referencePanel: 'console', direction: 'within'}, inactive: true});
  }

  const api = {
    init() {
      if (L) return;
      L = load();
      mq.addEventListener('change', () => {
        dk = {open: {}, vis: {}};   // the dock unmounts (phone) or mounts afresh (desktop)
        T().state.set('nf_drawer', false);
        push();
      });
      push();
    },
    // -- the dock's events
    ready() {
      api.init();
      dk = {open: {}, vis: {}};
      const d = dock();
      if (!d) return;
      if (dockJSON) d.restoreLayout(dockJSON); else defaultDock(d);
      push();
    },
    vis(e) {
      if (!e || !BY[e.id] || phone()) return;
      dk.open[e.id] = true;
      dk.vis[e.id] = !!e.visible;
      push();
    },
    removed(id) {
      if (phone()) return;
      delete dk.open[id];
      delete dk.vis[id];
      push();
    },
    layout(j) {
      if (phone() || !validDock(j)) return;
      dockJSON = j;
      save();
    },
    // -- launchers and panel headers
    toggle(id) {
      api.init();
      const p = BY[id];
      if (!p) return;
      if (phone()) {
        if (L.phone.includes(id)) L.phone = L.phone.filter(x => x !== id);
        else { L.phone.unshift(id); delete L.col[id]; window.scrollTo({top: 0}); }
      } else if (p.kind === 'main') {
        const d = dock();
        if (!d) return;
        if (!dk.open[id]) d.addPanel(id, p.title, p.tmpl, {});
        else if (!dk.vis[id]) d.activePanel(id);
        else d.removePanel(id);
        return;   // the dock's events update the rest
      } else {
        const s = L.side[id];
        s.open = !s.open;
        if (s.open) { delete L.col[id]; s.ord = nextOrd(s.side, id); }
      }
      push();
    },
    close(id) {
      if (phone()) L.phone = L.phone.filter(x => x !== id);
      else if (L.side[id]) L.side[id].open = false;
      push();
    },
    collapse(id) {
      if (L.col[id]) delete L.col[id]; else L.col[id] = true;
      push();
    },
    move(id) {
      const s = L.side[id];
      s.side = s.side === 'left' ? 'right' : 'left';
      s.ord = nextOrd(s.side, id);
      delete L.col[id];
      push();
    },
    only(id) {
      L.phone = [id];
      T().state.set('nf_drawer', false);
      push();
    },
    reset() {
      try { window.localStorage.removeItem(KEY); } catch (e) { /* blocked */ }
      L = copy(DEF);
      dockJSON = null;
      const d = dock();
      if (d && !phone()) {
        Object.keys(dk.open).forEach(id => d.removePanel(id));
        defaultDock(d);
      }
      T().state.set('nf_drawer', false);
      push();
    },
  };
  window.nfL = api;
  if (window.trame && window.trame.state) api.init();
})();
"""


def _script(catalogue: list[dict[str, Any]], defaults: dict[str, Any]) -> str:
    """The script, for these panels (``_Panel.client()``) and this default layout."""
    return (_SCRIPT.replace("__CATALOGUE__", json.dumps(catalogue))
            .replace("__DEFAULTS__", json.dumps(defaults))
            .replace("__KEY__", json.dumps(_KEY))
            .replace("__PHONE__", str(_PHONE_PX)))
