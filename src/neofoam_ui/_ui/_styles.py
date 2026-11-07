"""The shell's CSS: grid, toolbar, drawer, console, and the activity diagram's nodes.

Each pane adds its own (``Pane.css``); the pipeline pane borrows the ``nf-field`` frame of
the field pane and the ``nf-node`` / ``nf-activity`` looks of the diagram.
"""

__all__ = ()

_CSS = r"""
.nf-grid { display: grid; grid-template-columns: minmax(0, 1fr) 340px; grid-template-rows: minmax(0, 1fr) 230px;
  height: calc(100vh - 64px); }
.nf-canvas { grid-column: 1; grid-row: 1; min-height: 0; display: flex; }
.nf-diagram { flex: 1 1 0; min-width: 0; position: relative; }
.nf-idle { flex: 1 1 0; display: flex; align-items: center; justify-content: center; text-align: center;
  padding: 24px; font-size: 13px; opacity: .6; }
.nf-field { flex: 1 1 0; }
.nf-side { grid-column: 2; grid-row: 1 / 3; overflow: auto; border-left: 1px solid rgba(128,128,128,.25); }
.nf-bottom { grid-column: 1; grid-row: 2; display: grid; grid-template-columns: 1fr 1fr; min-height: 0;
  border-top: 1px solid rgba(128,128,128,.25); }
.nf-bottom > div { overflow: auto; min-height: 0; }
.nf-bottom > div:first-child { border-right: 1px solid rgba(128,128,128,.25); }
.nf-status { position: absolute; left: 10px; top: 8px; z-index: 5; font-size: 13px; padding: 2px 10px;
  border-radius: 12px; background: rgba(var(--v-theme-surface), .9); box-shadow: 0 1px 3px rgba(0,0,0,.15); }
.nf-tools { display: flex; align-items: center; overflow-x: auto; flex: 1 1 auto; min-width: 0; scrollbar-width: none; }
.nf-tools .v-btn { flex: none; }
@media (max-width: 900px) {
  .nf-grid { grid-template-columns: 1fr; grid-template-rows: 58vh auto auto; height: auto; }
  .nf-canvas { grid-column: 1; grid-row: 1; flex-direction: column; }
  .nf-grid.fv { grid-template-rows: auto auto auto; }
  .nf-grid.fv .nf-diagram { flex: none; height: 52vh; }
  .nf-grid.fv .nf-field { flex: none; height: 62vh; border-left: 0; border-top: 1px solid rgba(128,128,128,.25); }
  .nf-grid.fv .nf-pp { height: 70vh; }
  .nf-grid.fv .nf-pt { height: auto; }
  .nf-grid.fv .nf-pt .nf-pt-list { overflow: visible; }
  .nf-bottom { grid-column: 1; grid-row: 2; grid-template-columns: 1fr; }
  .nf-bottom > div { max-height: 34vh; }
  .nf-bottom > div:first-child { border-right: 0; border-bottom: 1px solid rgba(128,128,128,.25); }
  .nf-side { grid-column: 1; grid-row: 3; border-left: 0; border-top: 1px solid rgba(128,128,128,.25); }
  .nf-btxt, .nf-wide { display: none; }
  .v-toolbar-title { flex: none !important; min-width: 0 !important; }
  /* the toolbar scrolls sideways: Run…Restart, then the pane toggles, then stepping */
  .nf-tools > * { order: 2; }
  .nf-tools > :nth-child(-n+4) { order: 0; }
  .nf-tools > .nf-view { order: 1; }
  .nf-tools .v-btn { min-width: 0; padding: 0 8px; }  /* Run…Restart and the three panes fit 390 px */
  /* the field image at its own aspect, not letterboxed into a fixed height */
  .nf-grid.fv .nf-field:not(.nf-pp) { height: auto; }
  .nf-grid.fv .nf-field-img { flex: none; height: auto; }
  .nf-grid.fv .nf-field-empty { min-height: 30vh; }
  .v-footer { display: none; }
}
.nf-h { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; opacity: .65; padding: 8px 12px 4px;
  position: sticky; top: 0; background: rgb(var(--v-theme-surface)); z-index: 1; }
.nf-mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.nf-console div { padding: 0 12px; white-space: pre; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.nf-console .test { color: #5b6fb5; } .nf-console .bp { color: #d22; font-weight: 600; }
.nf-console .out { opacity: .7; } .nf-console .err { color: #d22; } .nf-console .init { color: #2e86c1; }
.nf-vars td, .nf-vars th { padding: 2px 10px !important; height: 22px !important; font-size: 12px; }
.nf-vars tr.changed td { background: rgba(255, 200, 0, .22); }
.nf-tree div { padding: 2px 12px; cursor: pointer; white-space: nowrap; display: flex; align-items: center; gap: 6px; font-size: 13px; }
.nf-tree div:hover { background: rgba(128,128,128,.12); }
.nf-tree .sel { outline: 1px solid #2b6fd6; background: rgba(43,111,214,.08); }
.nf-tree .cur { color: #e0362c; font-weight: 600; } .nf-tree .done { color: #2f9e5b; }
.nf-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
.nf-bpmark { width: 10px; color: #d22; font-size: 10px; }
.nf-kv { display: grid; grid-template-columns: 92px 1fr; gap: 2px 8px; font-size: 13px; padding: 0 12px; }
.nf-kv > span:nth-child(odd) { opacity: .6; }
.nf-sect { padding: 4px 0 8px; border-bottom: 1px solid rgba(128,128,128,.2); }
.nf-stack div { padding: 1px 12px; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.nf-stack div:first-child { color: #e0362c; font-weight: 600; }

/* --- activity diagram nodes --- */
.vue-flow__handle { opacity: 0; width: 4px; height: 4px; min-width: 0; min-height: 0; border: 0; }
.vue-flow__edge-text { font-size: 11px; }
.nf-node { position: relative; width: 100%; height: 100%; box-sizing: border-box; }
.nf-action, .nf-init { border: 1.4px solid #b9a24a; background: #fffbe6; border-radius: 14px; display: flex;
  align-items: center; padding: 0 10px 0 14px; gap: 8px; font-size: 13px; color: #1d2430; cursor: pointer; }
.nf-init { border-color: #7a9cc6; background: #eef4fb; border-radius: 8px; }
.nf-action.inj { border-style: dashed; }
.nf-stripe { width: 4px; align-self: stretch; margin: 9px 0; border-radius: 2px; flex: none; }
.nf-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nf-meta { font-size: 11px; opacity: .6; white-space: nowrap; }
.nf-badge { position: absolute; top: -9px; right: 10px; font-size: 10px; background: #8e5bb5; color: #fff;
  border-radius: 8px; padding: 0 6px; line-height: 16px; }
.nf-node.done { background: #e6f6ec; border-color: #2f9e5b; }
.nf-node.sel { box-shadow: 0 0 0 2.5px #2b6fd6; }
.nf-node.cur { background: #ffe9e7; border-color: #e0362c; border-width: 2.5px; }
.nf-node.cur::after { content: ""; position: absolute; right: -16px; top: calc(50% - 6px); width: 12px; height: 12px;
  border-radius: 50%; background: #e0362c; box-shadow: 0 0 0 4px rgba(224,54,44,.25); animation: nfpulse 1s infinite; }
@keyframes nfpulse { 50% { box-shadow: 0 0 0 8px rgba(224,54,44,.05); } }
.nf-bp { position: absolute; left: -22px; top: calc(50% - 6px); width: 12px; height: 12px; border-radius: 50%;
  border: 1.5px dashed rgba(128,128,128,.5); cursor: pointer; }
.nf-bp.on { background: #d22; border: 1.5px solid #d22; }
.nf-loop { border: 1.4px solid #7b8fc7; background: rgba(123,143,199,.07); border-radius: 8px; }
.nf-loop.active { border-color: #e0362c; border-width: 2px; }
.nf-loop.collapsed { background: #f3f6ff; border-radius: 12px; cursor: pointer; }
.nf-loop.collapsed.active { background: #ffe9e7; }
.nf-tog { display: inline-block; width: 16px; cursor: pointer; font-size: 13px; opacity: .9; }
.nf-loop-size { position: absolute; right: 10px; bottom: 6px; font-size: 11px; opacity: .6; }
.nf-node.hlw { box-shadow: 0 0 0 3px #f08c00; }
.nf-node.hlr { box-shadow: 0 0 0 3px #4d8fe0; }
.nf-vars tr.hl td { outline: 1px solid #f08c00; background: rgba(240,140,0,.12); }
.nf-loop-title { position: absolute; left: 10px; top: 6px; font-size: 12px; opacity: .8; }
.nf-loop-pass { font-weight: 600; color: #e0362c; }
.nf-loop-res { font-family: monospace; font-size: 11px; opacity: .85; }
.nf-box { width: 100%; height: 100%; border: 1.5px solid; border-radius: 10px; opacity: .9;
  background: rgba(255,255,255,.55); pointer-events: none; }
.nf-box-head { position: absolute; left: 10px; top: 4px; font-size: 12px; font-weight: 600;
  display: inline-flex; align-items: center; gap: 5px; }
.vue-flow__node-region { z-index: 0 !important; pointer-events: none; }
.nf-action.cpl { border-style: dashed; border-color: #6741d9; background: #f4f0ff; }
.nf-cpl { position: absolute; top: -9px; right: 10px; font-size: 10px; background: #6741d9; color: #fff;
  border-radius: 8px; padding: 0 6px; line-height: 16px; white-space: nowrap; }
.vue-flow__edge.nf-couple { z-index: 5; }
.nf-regions { position: absolute; right: 10px; top: 8px; z-index: 5; font-size: 12px; padding: 2px 8px;
  display: flex; gap: 10px; background: rgba(128,128,128,.08); border-radius: 10px; }
.nf-region { display: inline-flex; align-items: center; gap: 4px; }
.nf-loop-test { position: absolute; left: 26px; top: 24px; font-size: 11px; opacity: .65; font-family: ui-monospace, Menlo, monospace; }
.nf-decision { width: 100%; height: 100%; position: relative; cursor: pointer; }
.nf-decision .d { position: absolute; inset: 5px; transform: rotate(45deg); border: 1.6px solid #7b8fc7; background: #f3f6ff; }
.nf-decision.cur .d { border-color: #e0362c; border-width: 2.5px; background: #ffe9e7; }
.nf-decision.cur::after { content: ""; position: absolute; left: calc(50% - 5px); top: calc(50% - 5px); width: 10px;
  height: 10px; border-radius: 50%; background: #e0362c; }
.nf-decision.sel .d { box-shadow: 0 0 0 2.5px #2b6fd6; }
.nf-decision .nf-bp { left: -26px; }
.nf-activity { border: 1.6px solid #8a94a6; border-radius: 18px; background: rgba(138,148,166,.05); }
.nf-activity-title { position: absolute; left: 14px; top: 9px; font-size: 13px; font-weight: 600; opacity: .75; }
.nf-initial { width: 100%; height: 100%; border-radius: 50%; background: #1d2430; }
.nf-final { width: 100%; height: 100%; border-radius: 50%; border: 2px solid #1d2430; position: relative; }
.nf-final::after { content: ""; position: absolute; inset: 4px; border-radius: 50%; background: #1d2430; }
.nf-final.cur::after { background: #e0362c; }
.nf-placeholder { border: 1.4px dashed rgba(128,128,128,.6); border-radius: 12px; display: flex; align-items: center;
  justify-content: center; text-align: center; font-size: 12px; opacity: .7; padding: 6px; }
"""
