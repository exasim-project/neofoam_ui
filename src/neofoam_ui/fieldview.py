"""Field view pane: the selected solver field on the mesh, redrawn at every pause.

Rendering is a small canvas-2D renderer in the browser (``FIELD_JS``): the
geometry arrives once (``fv_mesh``, base64 float32/int32 arrays, see
:mod:`neofoam_ui.geometry`), then one float32 array per pause (``fv_frame``).
2D cells are filled polygons batched per colour (256 fills per frame), 1D is a
line plot with the previous frame as a ghost, anything else coloured cell centres.
Wheel zooms, drag pans, double click resets, hover shows the nearest cell's value.
"""

from __future__ import annotations

from typing import Any

from trame.widgets import client, html
from trame.widgets import vuetify3 as v3

__all__ = ["FIELD_CSS", "FIELD_JS", "component_items", "field_pane"]

FIELD_CSS = r"""
.nf-field { display: flex; flex-direction: column; min-width: 0; min-height: 0;
  border-left: 1px solid rgba(128,128,128,.25); background: rgb(var(--v-theme-surface)); }
.nf-field-bar { display: flex; align-items: center; gap: 8px; padding: 4px 10px; flex-wrap: wrap;
  border-bottom: 1px solid rgba(128,128,128,.2); }
.nf-field-bar .v-select { flex: 0 1 150px; min-width: 110px; }
.nf-field-msg { font-size: 12px; opacity: .7; flex: 1 1 160px; min-width: 0; }
.nf-field-canvas { flex: 1 1 auto; min-height: 0; width: 100%; display: block; cursor: crosshair; }
"""

#: Client-side renderer. Commands queue up in ``window.nfFieldQ`` until the script ran.
FIELD_JS = r"""
(function () {
  if (window.nfField) { window.nfField.flush(); return; }
  const STOPS = ['#440154', '#472c7a', '#3b518b', '#2c718e', '#21908d', '#27ad81', '#5cc863', '#aadc32', '#fde725'];
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const LUT = [];
  for (let i = 0; i < 256; i++) {
    const t = i / 255 * (STOPS.length - 1), k = Math.min(Math.floor(t), STOPS.length - 2), f = t - k;
    const a = rgb(STOPS[k]), b = rgb(STOPS[k + 1]);
    LUT.push(`rgb(${a.map((v, j) => Math.round(v + (b[j] - v) * f)).join(',')})`);
  }
  const decode = (b64, T) => {
    const s = atob(b64), u = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i);
    return new T(u.buffer);
  };
  const fmt = (v) => (v === null || v === undefined || !isFinite(v)) ? '—'
    : (Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-3 && v !== 0)) ? v.toExponential(3) : (+v.toPrecision(5)).toString();
  const F = { mesh: null, frame: null, prev: null, lock: false, range: null, zoom: 1, cx: 0, cy: 0,
              el: null, ro: null, hover: null, buf: null, dirty: true, queued: false };
  window.nfField = F;

  F.flush = () => {
    const q = window.nfFieldQ || [];
    window.nfFieldQ = [];
    for (const [cmd, arg] of q) F[cmd](arg);
  };
  F.setMesh = (m) => {
    F.mesh = null; F.prev = null;  // a frame may arrive before its mesh: keep it
    if (m && m.kind) {
      const g = { ...m };
      if (m.points) g.pts = decode(m.points, Float32Array);
      if (m.x) g.xs = decode(m.x, Float32Array);
      if (m.offsets) { g.off = decode(m.offsets, Int32Array); g.conn = decode(m.conn, Int32Array); }
      g.cen = new Float32Array(2 * m.n);  // cell centres for hover
      for (let i = 0; i < m.n; i++) {
        if (g.off) {
          let sx = 0, sy = 0; const a = g.off[i], b = g.off[i + 1];
          for (let j = a; j < b; j++) { sx += g.pts[2 * g.conn[j]]; sy += g.pts[2 * g.conn[j] + 1]; }
          g.cen[2 * i] = sx / (b - a); g.cen[2 * i + 1] = sy / (b - a);
        } else if (g.pts) { g.cen[2 * i] = g.pts[2 * i]; g.cen[2 * i + 1] = g.pts[2 * i + 1]; }
      }
      F.mesh = g;
    }
    F.zoom = 1; F.cx = 0; F.cy = 0; F.redraw();
  };
  F.setFrame = (f) => {
    if (!f || !f.values) { F.frame = null; F.redraw(); return; }
    const fr = { ...f, vals: decode(f.values, Float32Array) };
    if (F.frame && F.frame.field === fr.field && F.frame.component === fr.component) F.prev = F.frame;
    else F.prev = null;
    F.frame = fr;
    if (F.lock && !F.range) F.range = [fr.min, fr.max];
    F.redraw();
  };
  F.setLock = (on) => { F.lock = !!on; F.range = on && F.frame ? [F.frame.min, F.frame.max] : null; F.redraw(); };
  F.redraw = () => { F.dirty = true; F.schedule(); };
  F.schedule = () => {
    if (F.queued) return;
    F.queued = true;
    requestAnimationFrame(() => { F.queued = false; F.paint(); });
  };

  F.attach = () => {
    const el = document.getElementById('nf-field-canvas');
    if (!el || el === F.el) return el;
    F.el = el;
    if (F.ro) F.ro.disconnect();
    F.ro = new ResizeObserver(() => F.redraw());
    F.ro.observe(el);
    el.addEventListener('wheel', (e) => {
      if (!F.mesh || F.mesh.kind === 'line') return;
      e.preventDefault();
      const r = el.getBoundingClientRect(), k = Math.exp(-e.deltaY * 0.0015);
      const mx = e.clientX - r.left - r.width / 2, my = e.clientY - r.top - F.plotH() / 2;
      F.cx = mx - (mx - F.cx) * k; F.cy = my - (my - F.cy) * k; F.zoom *= k; F.redraw();
    }, { passive: false });
    let drag = null;
    el.addEventListener('pointerdown', (e) => { if (e.pointerType === 'mouse') drag = [e.clientX, e.clientY]; });
    window.addEventListener('pointerup', () => { drag = null; });
    el.addEventListener('pointermove', (e) => {
      const r = el.getBoundingClientRect();
      if (drag && F.mesh && F.mesh.kind !== 'line') {
        F.cx += e.clientX - drag[0]; F.cy += e.clientY - drag[1]; drag = [e.clientX, e.clientY]; F.redraw();
        return;
      }
      F.hover = [e.clientX - r.left, e.clientY - r.top]; F.schedule();
    });
    el.addEventListener('pointerleave', () => { F.hover = null; F.schedule(); });
    el.addEventListener('dblclick', () => { F.zoom = 1; F.cx = 0; F.cy = 0; F.redraw(); });
    return el;
  };
  F.plotH = () => (F.el ? F.el.clientHeight : 0) - 46;  // colour bar strip at the bottom

  F.range_ = () => {
    let [lo, hi] = F.lock && F.range ? F.range : [F.frame.min, F.frame.max];
    if (lo === null || hi === null) { lo = 0; hi = 1; }
    if (hi - lo <= Math.abs(hi) * 1e-12) { hi = lo + (Math.abs(lo) || 1) * 1e-6; }
    return [lo, hi];
  };
  // world → screen for 2D kinds: fit the bounds, keep the aspect ratio, then zoom/pan.
  F.xf = (W, H) => {
    const [x0, x1, y0, y1] = F.mesh.bounds, pad = 14;
    const s = Math.min((W - 2 * pad) / ((x1 - x0) || 1), (H - 2 * pad) / ((y1 - y0) || 1)) * F.zoom;
    const ox = W / 2 + F.cx - s * (x0 + x1) / 2, oy = H / 2 + F.cy + s * (y0 + y1) / 2;
    return { s, ox, oy };
  };

  F.paint = () => {
    const el = F.attach();
    if (!el) return;
    const dpr = window.devicePixelRatio || 1, W = el.clientWidth, H = el.clientHeight;
    if (!W || !H) return;
    if (el.width !== Math.round(W * dpr) || el.height !== Math.round(H * dpr)) {
      el.width = Math.round(W * dpr); el.height = Math.round(H * dpr); F.dirty = true;
    }
    const css = getComputedStyle(el), fg = css.color || '#222';
    if (F.dirty || !F.buf) { F.buf = F.render(W, H, dpr, fg); F.dirty = false; }
    const ctx = el.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, el.width, el.height);
    ctx.drawImage(F.buf, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    F.overlay(ctx, W, H, fg);
  };

  F.render = (W, H, dpr, fg) => {
    const c = document.createElement('canvas');
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
    const ctx = c.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.font = '12px system-ui, sans-serif'; ctx.fillStyle = fg; ctx.strokeStyle = fg;
    const m = F.mesh, fr = F.frame;
    if (!m || !fr || fr.vals.length !== m.n) {
      ctx.globalAlpha = 0.6; ctx.textAlign = 'center';
      ctx.fillText(!m ? 'waiting for the mesh…' : 'waiting for field values…', W / 2, H / 2);
      return c;
    }
    const [lo, hi] = F.range_(), PH = H - 46;
    if (m.kind === 'line') { F.renderLine(ctx, W, H, lo, hi, fg); return c; }
    const { s, ox, oy } = F.xf(W, PH);
    const bins = Array.from({ length: 256 }, () => []);
    for (let i = 0; i < m.n; i++) {
      const v = fr.vals[i];
      const q = isFinite(v) ? Math.max(0, Math.min(255, Math.round((v - lo) / (hi - lo) * 255))) : -1;
      if (q >= 0) bins[q].push(i);
    }
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, W, PH); ctx.clip();
    ctx.lineJoin = 'round';
    const size = m.kind === 'points'
      ? Math.max(1.5, Math.sqrt((m.bounds[1] - m.bounds[0]) * (m.bounds[3] - m.bounds[2]) / m.n) * s) : 0;
    for (let q = 0; q < 256; q++) {
      const cells = bins[q];
      if (!cells.length) continue;
      ctx.fillStyle = LUT[q]; ctx.strokeStyle = LUT[q];
      ctx.beginPath();
      for (const i of cells) {
        if (m.kind === 'points') {
          ctx.rect(ox + s * m.pts[2 * i] - size / 2, oy - s * m.pts[2 * i + 1] - size / 2, size, size);
          continue;
        }
        const a = m.off[i], b = m.off[i + 1];
        for (let j = a; j < b; j++) {
          const p = m.conn[j], x = ox + s * m.pts[2 * p], y = oy - s * m.pts[2 * p + 1];
          if (j === a) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
      }
      ctx.fill();
      if (m.kind === 'polygons') { ctx.lineWidth = 0.7; ctx.stroke(); }  // hide anti-aliasing seams
    }
    ctx.restore();
    F.colorBar(ctx, W, H, lo, hi, fg);
    return c;
  };

  F.colorBar = (ctx, W, H, lo, hi, fg) => {
    const x0 = 60, x1 = Math.max(x0 + 40, W - 60), y = H - 34, h = 10;
    for (let i = 0; i < 256; i++) {
      ctx.fillStyle = LUT[i];
      ctx.fillRect(x0 + (x1 - x0) * i / 256, y, (x1 - x0) / 256 + 0.6, h);
    }
    ctx.fillStyle = fg; ctx.textBaseline = 'top'; ctx.textAlign = 'right';
    ctx.fillText(fmt(lo), x0 - 6, y - 1);
    ctx.textAlign = 'left'; ctx.fillText(fmt(hi), x1 + 6, y - 1);
    ctx.textAlign = 'center'; ctx.globalAlpha = 0.75;
    ctx.fillText(F.frame.label + (F.lock ? '  (range fixed)' : ''), (x0 + x1) / 2, y + h + 3);
    ctx.globalAlpha = 1;
  };

  F.lineXf = (W, H, lo, hi) => {
    const m = F.mesh, [a, b] = m.bounds, L = 64, R = 16, T = 16, B = 40;
    const pad = (hi - lo) * 0.08;
    return { X: (x) => L + (x - a) / ((b - a) || 1) * (W - L - R),
             Y: (v) => T + (1 - (v - lo + pad) / (hi - lo + 2 * pad)) * (H - T - B), L, R, T, B };
  };
  F.renderLine = (ctx, W, H, lo, hi, fg) => {
    const m = F.mesh, fr = F.frame, t = F.lineXf(W, H, lo, hi);
    ctx.globalAlpha = 0.35; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(t.L, t.T); ctx.lineTo(t.L, H - t.B); ctx.lineTo(W - t.R, H - t.B); ctx.stroke();
    ctx.globalAlpha = 0.8; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (let k = 0; k <= 4; k++) {
      const v = lo + (hi - lo) * k / 4;
      ctx.fillText(fmt(v), t.L - 6, t.Y(v));
      ctx.globalAlpha = 0.12; ctx.beginPath(); ctx.moveTo(t.L, t.Y(v)); ctx.lineTo(W - t.R, t.Y(v)); ctx.stroke();
      ctx.globalAlpha = 0.8;
    }
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText((m.axes && m.axes[0]) || 'x', (t.L + W - t.R) / 2, H - t.B + 6);
    ctx.textAlign = 'left'; ctx.fillText(fmt(m.bounds[0]), t.L, H - t.B + 6);
    ctx.textAlign = 'right'; ctx.fillText(fmt(m.bounds[1]), W - t.R, H - t.B + 6);
    ctx.textAlign = 'center';
    ctx.save(); ctx.translate(14, (t.T + H - t.B) / 2); ctx.rotate(-Math.PI / 2);
    ctx.fillText(fr.label + (F.lock ? ' (range fixed)' : ''), 0, -8); ctx.restore();
    const line = (vals, color, width, dash) => {
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash); ctx.beginPath();
      for (let i = 0; i < m.n; i++) {
        const x = t.X(m.xs[i]), y = t.Y(vals[i]);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke(); ctx.setLineDash([]);
    };
    ctx.globalAlpha = 1;
    if (F.prev && F.prev.vals.length === m.n) line(F.prev.vals, 'rgba(128,128,128,.7)', 1.2, [4, 3]);
    line(fr.vals, '#2b6fd6', 2, []);
    ctx.fillStyle = '#2b6fd6';
    for (let i = 0; i < m.n; i++) { ctx.beginPath(); ctx.arc(t.X(m.xs[i]), t.Y(fr.vals[i]), 2.2, 0, 7); ctx.fill(); }
    if (F.prev) {
      ctx.fillStyle = fg; ctx.globalAlpha = 0.6; ctx.textAlign = 'right';
      ctx.fillText('- - previous pause', W - t.R, t.T); ctx.globalAlpha = 1;
    }
  };

  // Hover: nearest cell (by centre) under the pointer.
  F.overlay = (ctx, W, H, fg) => {
    const m = F.mesh, fr = F.frame;
    if (!F.hover || !m || !fr || fr.vals.length !== m.n) return;
    const [hx, hy] = F.hover;
    let best = -1, bd = Infinity, sx = 0, sy = 0;
    if (m.kind === 'line') {
      const [lo, hi] = F.range_(), t = F.lineXf(W, H, lo, hi);
      for (let i = 0; i < m.n; i++) { const d = Math.abs(t.X(m.xs[i]) - hx); if (d < bd) { bd = d; best = i; } }
      sx = t.X(m.xs[best]); sy = t.Y(fr.vals[best]);
    } else {
      if (hy > H - 46) return;
      const { s, ox, oy } = F.xf(W, H - 46);
      const wx = (hx - ox) / s, wy = (oy - hy) / s;
      for (let i = 0; i < m.n; i++) {
        const dx = m.cen[2 * i] - wx, dy = m.cen[2 * i + 1] - wy, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = i; }
      }
      sx = ox + s * m.cen[2 * best]; sy = oy - s * m.cen[2 * best + 1];
    }
    if (best < 0) return;
    ctx.strokeStyle = '#e0362c'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(sx, sy, 4, 0, 7); ctx.stroke();
    const text = `cell ${best}: ${fr.label} = ${fmt(fr.vals[best])}`;
    ctx.font = '12px system-ui, sans-serif';
    const w = ctx.measureText(text).width + 12, bx = Math.min(Math.max(4, hx + 12), W - w - 4), by = Math.max(4, hy - 28);
    ctx.fillStyle = 'rgba(29,36,48,.88)'; ctx.fillRect(bx, by, w, 20);
    ctx.fillStyle = '#fff'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(text, bx + 6, by + 10);
  };

  F.flush();
})();
"""


def _queue(cmd: str, arg: str) -> str:
    """JS: hand ``arg`` to ``window.nfField[cmd]`` (queued until the renderer script ran)."""
    return (f"(window.nfFieldQ = window.nfFieldQ || []).push(['{cmd}', {arg}]);"
            " window.nfField && window.nfField.flush()")


def component_items(ncomp: int) -> list[dict[str, Any]]:
    if ncomp <= 1:
        return []
    names = "xyz" if ncomp == 3 else [str(i) for i in range(ncomp)]
    return [{"title": "magnitude", "value": "mag"}] + [
        {"title": n, "value": str(i)} for i, n in enumerate(names)]


def field_pane() -> None:
    """The pane (header + canvas); shown while ``fv_on``."""
    with html.Div(classes="nf-field", v_show="fv_on"):
        with html.Div(classes="nf-field-bar"):
            v3.VSelect(v_model=("fv_field",), items=("fv_fields",), label="field", density="compact",
                       hide_details=True, variant="outlined", disabled=("!fv_fields.length",))
            v3.VSelect(v_model=("fv_comp",), items=("fv_comps",), label="component", density="compact",
                       hide_details=True, variant="outlined", v_show="fv_comps.length")
            v3.VCheckbox(v_model=("fv_lock",), label="fix range", hide_details=True, density="compact",
                         style="flex: none;", title="keep the current colour range for later pauses")
            html.Span("{{ fv_msg }}", classes="nf-field-msg")
        html.Canvas(id="nf-field-canvas", classes="nf-field-canvas")
        client.Script(FIELD_JS)
        client.ClientStateChange(value="fv_mesh", change=_queue("setMesh", "fv_mesh"),
                                 trigger_on_create=True)
        client.ClientStateChange(value="fv_frame", change=_queue("setFrame", "fv_frame"),
                                 trigger_on_create=True)
        client.ClientStateChange(value="fv_lock", change=_queue("setLock", "fv_lock"))
        # v-show toggles the size: repaint when the pane opens.
        client.ClientStateChange(value="fv_on", change="window.nfField && window.nfField.redraw()")
