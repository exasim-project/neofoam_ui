"""The shell's CSS: toolbar, activity bars, sidebars, dock, phone layout, console, and the
activity diagram's nodes.

Each pane adds its own (``Pane.css``); the plots pane borrows the ``nf-field`` frame of
the field pane.
"""

__all__ = ()

_CSS = r"""
/* --- the shell: toolbar, activity bars, sidebars, dock, footer --- */
:root { --nf-bg: #f6f7f9; --nf-panel: #fff; --nf-strip: #eef0f4; --nf-ink: #1c2330; --nf-muted: #6b7486;
  --nf-faint: #9aa3b2; --nf-line: #e3e6ec; --nf-line2: #d3d8e0; --nf-hover: #f0f2f6; --nf-sel: #e8f0fe;
  --nf-accent: #2f6fe4; --nf-red: #e03131; --nf-shadow: rgba(20,30,50,.06); }
html, body { overflow: hidden; }
.v-application { background: var(--nf-bg) !important; }
.nf-shell { display: flex; flex-direction: column; height: 100vh; color: var(--nf-ink); font-size: 13px; }
.nf-grow { flex: 1 1 auto; min-width: 8px; }
.nf-mut { color: var(--nf-muted); }
.nf-tb { display: flex; align-items: center; gap: 2px; padding: 5px 10px; min-height: 46px; flex: none;
  background: var(--nf-panel); border-bottom: 1px solid var(--nf-line); }
.nf-tb .v-btn { flex: none; }
.nf-tb .nf-btn { padding: 0 8px; }
.nf-tb .v-btn { text-transform: none; letter-spacing: normal; font-size: 13px; }
.nf-chip { flex: none; margin-right: 4px; }
.nf-sep { width: 1px; height: 22px; background: var(--nf-line); margin: 0 5px; flex: none; }
.nf-seg { flex: none; height: 28px !important; }
.nf-status { display: inline-block; padding: 3px 12px; border-radius: 999px; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; min-width: 0; flex: 0 1 auto; background: #fff8e6; border: 1px solid #f3d9a4;
  color: #9a5b00; font-weight: 500; }
.nf-status.run { background: #e9f6ec; border-color: #b6dfc0; color: #2b8a3e; }
.nf-status.err { background: #fff5f5; border-color: #f5b5b5; color: #d22; }
.nf-gear.on { color: var(--nf-accent); background: var(--nf-sel); }
.nf-body { flex: 1 1 auto; display: flex; min-height: 0; }
.nf-abar { width: 44px; flex: none; background: var(--nf-panel); display: flex; flex-direction: column;
  align-items: center; padding: 4px 0 6px; gap: 2px; }
.nf-abar.left { border-right: 1px solid var(--nf-line); }
.nf-abar.right { border-left: 1px solid var(--nf-line); }
.nf-lbl { font-size: 8.5px; letter-spacing: .06em; color: var(--nf-faint); text-transform: uppercase; margin: 6px 0 1px; }
.nf-div { width: 24px; height: 1px; background: var(--nf-line); margin: 6px 0 0; }
.ab { position: relative; width: 34px; height: 34px; border-radius: 8px; color: var(--nf-muted);
  display: grid; place-items: center; flex: none; }
.ab:hover { background: var(--nf-hover); color: var(--nf-ink); }
.ab.on { color: var(--nf-accent); background: var(--nf-sel); }
.ab.bg { color: var(--nf-accent); }
.ab.on::before, .ab.bg::before { content: ""; position: absolute; top: 7px; bottom: 7px; width: 3px; border-radius: 2px;
  background: var(--nf-accent); }
.ab.bg::before { opacity: .35; }
.nf-abar.left .ab::before { left: -5px; } .nf-abar.right .ab::before { right: -5px; }
.nf-badge { position: absolute; top: 2px; right: 2px; min-width: 14px; height: 14px; border-radius: 7px; font-size: 9px;
  background: var(--nf-red); color: #fff; display: grid; place-items: center; padding: 0 3px; font-weight: 700; line-height: 1; }
.nf-sbar { width: 300px; flex: none; display: flex; flex-direction: column; gap: 6px; padding: 6px; min-height: 0; }
.nf-sbar.right { width: 330px; }
.nf-main { flex: 1 1 auto; min-width: 0; min-height: 0; padding: 6px 0; position: relative; }
.nf-body > .nf-abar.left + .nf-main { padding-left: 6px; }
.nf-body > .nf-main:has(+ .nf-abar.right) { padding-right: 6px; }
.nf-panel { background: var(--nf-panel); border: 1px solid var(--nf-line); border-radius: 10px; display: flex;
  flex-direction: column; min-height: 0; min-width: 0; overflow: hidden; flex: 1 1 0; box-shadow: 0 1px 2px var(--nf-shadow); }
.nf-panel.collapsed { flex: 0 0 auto !important; }
.nf-sbar .nf-panel[data-id=tree] { flex-grow: 1.4; } .nf-sbar .nf-panel[data-id=inspector] { flex-grow: 1.3; }
.nf-sbar .nf-panel[data-id=callstack] { flex-grow: .8; } .nf-sbar .nf-panel[data-id=breakpoints] { flex-grow: .6; }
.nf-sbar .nf-panel[data-id=options] { flex-grow: .8; } .nf-sbar .nf-panel[data-id=legend] { flex-grow: .9; }
.nf-ph { display: flex; align-items: center; gap: 6px; padding: 4px 6px 4px 10px; border-bottom: 1px solid var(--nf-line);
  font-size: 11px; font-weight: 650; letter-spacing: .05em; text-transform: uppercase; color: var(--nf-muted); flex: none; }
.nf-panel.collapsed .nf-ph { border-bottom: 0; }
.nf-ph .t { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hb { width: 24px; height: 24px; border-radius: 5px; color: var(--nf-faint); font-size: 13px; line-height: 1;
  display: grid; place-items: center; flex: none; }
.hb:hover { background: var(--nf-hover); color: var(--nf-ink); }
.nf-pb { flex: 1 1 auto; overflow: auto; min-height: 0; padding: 4px 0; }
.nf-empty { font-size: 12px; opacity: .6; padding: 6px 12px; }
.nf-foot { display: flex; align-items: center; gap: 6px; padding: 3px 12px; font-size: 11.5px; flex: none;
  background: var(--nf-panel); border-top: 1px solid var(--nf-line); white-space: nowrap; overflow: hidden; }
.nf-lnk { color: var(--nf-accent); cursor: pointer; }
.nf-lnk:hover { text-decoration: underline; }
/* the panels' templates (in the dock, a sidebar or the phone's stack) */
.nf-pc { height: 100%; min-height: 0; display: flex; flex-direction: column; }
.nf-pc-main { overflow: auto; background: var(--nf-panel); }
.nf-pc-step { position: relative; overflow: hidden; }
.nf-pc-console, .nf-pc-fields { padding: 4px 0; }
.nf-kvt { border-collapse: collapse; width: 100%; font-size: 12.5px; }
.nf-kvt td { padding: 2px 12px; border-bottom: 1px solid var(--nf-line); }
.nf-kvt td:first-child { color: var(--nf-muted); width: 40%; }
.nf-opts { padding: 2px 12px 8px; }
.nf-opts .v-selection-control { min-height: 30px; }
.nf-opts .v-label { font-size: 13px; }
.nf-opts .v-selection-control__wrapper, .nf-opts .v-selection-control__input { width: 30px; height: 30px; }
.nf-pc > .nf-field { flex: 1 1 auto; min-height: 0; border-left: 0; }
/* the dock (trame-dockview, theme Light): cards like the side panels */
.nf-main .dockview-theme-light { --dv-background-color: var(--nf-bg); --dv-group-view-background-color: var(--nf-panel);
  --dv-tabs-and-actions-container-background-color: var(--nf-strip); --dv-separator-border: transparent;
  --dv-paneview-header-border-color: var(--nf-line); --dv-tabs-and-actions-container-font-size: 12.5px;
  --dv-tabs-and-actions-container-height: 34px; --dv-activegroup-visiblepanel-tab-background-color: var(--nf-panel);
  --dv-inactivegroup-visiblepanel-tab-background-color: var(--nf-panel); --dv-tab-divider-color: var(--nf-line);
  --dv-activegroup-visiblepanel-tab-color: var(--nf-ink); --dv-inactivegroup-visiblepanel-tab-color: var(--nf-ink);
  --dv-activegroup-hiddenpanel-tab-color: var(--nf-muted); --dv-inactivegroup-hiddenpanel-tab-color: var(--nf-muted); }
.nf-main .dv-tab.dv-active-tab { font-weight: 600; box-shadow: inset 0 2px 0 var(--nf-accent); }
/* a view behind another tab stays mounted, hidden; vue-flow sets its nodes visible: keep them hidden */
.nf-main .dv-render-overlay[style*="visibility: hidden"] * { visibility: hidden !important; }
.nf-main .dv-groupview { border: 1px solid var(--nf-line); border-radius: 10px; overflow: hidden; }
/* --- phone: picker row, stack, drawer --- */
.nf-pbar, .nf-pstack { display: none; }
@media (max-width: 1280px) {
  .nf-tb .nf-btxt { display: none; }
  .nf-sbar { width: 270px; } .nf-sbar.right { width: 300px; }
}
@media (max-width: 760px) {
  html, body { overflow-x: hidden; overflow-y: auto; }
  .nf-shell { height: auto; min-height: 100vh; }
  .nf-tb { flex-wrap: wrap; padding: 6px 16px; row-gap: 4px; gap: 2px; }
  .nf-tb .nf-chip { order: -3; margin-right: auto; }
  .nf-tb .nf-gear { order: -2; }
  .nf-tb > .nf-grow { order: -1; flex: 0 0 100%; height: 0; min-width: 0; }
  .nf-tb .nf-btn { flex: 1 1 0; min-width: 0; padding: 0 4px; }
  .nf-tb > .nf-sep.nf-brk { order: 9; display: block; flex: 0 0 100%; height: 0; margin: 0; }
  .nf-tb .nf-status { order: 10; flex: 1 1 0; white-space: normal; border-radius: 10px; }
  .nf-tb .nf-seg { order: 11; margin-left: 4px; }
  .nf-tb .nf-sep { display: none; }  /* nf-brk breaks the row before the status */
  .nf-pbar { display: flex; position: sticky; top: 0; z-index: 20; background: var(--nf-panel);
    border-bottom: 1px solid var(--nf-line); align-items: stretch; box-shadow: 0 2px 6px var(--nf-shadow); }
  .nf-pmenu { flex: none; width: 52px; border-right: 1px solid var(--nf-line); display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 2px; font-size: 10px; padding-left: 8px; }
  .nf-pscroll { flex: 1; display: flex; overflow-x: auto; gap: 2px; padding: 4px 16px 4px 6px; scrollbar-width: none;
    -webkit-mask-image: linear-gradient(90deg, #000 calc(100% - 28px), transparent);
    mask-image: linear-gradient(90deg, #000 calc(100% - 28px), transparent); }
  .nf-pscroll::-webkit-scrollbar { display: none; }
  .pb2 { position: relative; flex: none; width: 56px; height: 50px; border-radius: 9px; color: var(--nf-muted);
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; font-size: 10.5px; }
  .pb2.on { color: var(--nf-accent); background: var(--nf-sel); font-weight: 600; }
  .pb2.on::after { content: ""; position: absolute; left: 14px; right: 14px; bottom: -4px; height: 3px;
    border-radius: 2px; background: var(--nf-accent); }
  .pb2 .nf-badge { top: 3px; right: 10px; }
  .nf-pgap { flex: none; width: 1px; background: var(--nf-line); margin: 8px 4px; }
  .nf-pstack { display: flex; flex-direction: column; gap: 10px; padding: 10px 16px 16px; }
  .nf-pstack .nf-panel { flex: none; }
  .nf-pstack .hb { width: 34px; height: 34px; font-size: 15px; }
  .nf-pstack .nf-pc { height: auto; }
  .nf-pstack .nf-pc-step { height: 70vh; }
  .nf-pstack .nf-pc-console .nf-console { max-height: 300px; overflow: auto; }
  .nf-pstack .nf-pc-cfd .nf-field-img { flex: none; height: auto; }
  .nf-pstack .nf-pc-cfd .nf-field-empty { min-height: 30vh; }
  .nf-pempty { border: 2px dashed var(--nf-line2); border-radius: 10px; padding: 28px 16px; text-align: center; color: var(--nf-muted); }
  .nf-scrim { position: fixed; inset: 0; background: rgba(15,20,30,.35); z-index: 40; }
  .nf-drawer { display: flex; flex-direction: column; position: fixed; top: 0; bottom: 0; left: 0; width: min(84vw, 320px);
    z-index: 41; background: var(--nf-panel); border-right: 1px solid var(--nf-line); transform: translateX(-102%);
    transition: transform .22s ease; box-shadow: 4px 0 18px var(--nf-shadow); }
  .nf-drawer.open { transform: none; }
  .nf-dh { display: flex; align-items: center; gap: 8px; padding: 12px 8px 10px 16px; border-bottom: 1px solid var(--nf-line);
    font-weight: 650; font-size: 15px; }
  .nf-dh .hb { width: 36px; height: 36px; font-size: 18px; }
  .nf-dl { flex: 1; overflow: auto; padding: 6px 0 10px; }
  .nf-dsec { font-size: 10.5px; font-weight: 650; letter-spacing: .06em; text-transform: uppercase; color: var(--nf-faint);
    margin: 12px 16px 4px; }
  .nf-drow { display: flex; align-items: center; gap: 12px; padding: 0 16px; min-height: 44px; cursor: pointer; }
  .nf-drow:hover { background: var(--nf-hover); }
  .nf-drow > span { flex: 1; }
  .nf-sw { width: 38px; height: 22px; border-radius: 11px; background: var(--nf-line2); position: relative; flex: none; }
  .nf-sw::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%;
    background: #fff; transition: transform .15s; box-shadow: 0 1px 2px rgba(0,0,0,.25); }
  .nf-drow.on .nf-sw { background: var(--nf-accent); }
  .nf-drow.on .nf-sw::after { transform: translateX(16px); }
  .nf-df { border-top: 1px solid var(--nf-line); padding: 10px 16px 14px; display: flex; flex-direction: column; gap: 8px;
    font-size: 12px; color: var(--nf-muted); }
  .nf-sbtn { border: 1px solid var(--nf-line2); border-radius: 6px; padding: 7px 12px; font-size: 12px; color: var(--nf-ink); }
  .nf-foot { flex-wrap: wrap; white-space: normal; padding: 8px 16px; }
  .nf-dhint { display: none; }
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
.nf-every { position: absolute; top: -9px; left: 14px; font-size: 10px; line-height: 16px; padding: 0 6px;
  border-radius: 8px; border: 1px solid #8a94a6; background: #fff; color: #5a6475; cursor: pointer; }
.nf-every.on { background: #f08c00; border-color: #f08c00; color: #fff; font-weight: 600; }
.nf-final-pass { color: #b35c00; background: #ffecd2; }
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
.nf-box-head { pointer-events: auto; }
.nf-box.closed { border-style: dashed; background: rgba(255,255,255,.25); }
.nf-loop.control { background: #f3f6ff; border-radius: 18px; }
.nf-loop.control .nf-loop-title { left: 10px; right: 44px; top: 9px; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; opacity: .9; }
.nf-exmap { width: 100%; height: 100%; box-sizing: border-box; padding: 0 8px; border: 1.5px dashed #6741d9;
  border-radius: 12px; background: #f4f0ff; color: #6741d9; font: 600 10.5px ui-monospace, Menlo, monospace;
  display: flex; align-items: center; gap: 5px; outline: none; cursor: help; position: relative; }
.nf-exmap.unk { color: #7a7a86; border-color: #9a9aa6; }
.nf-exmap.hot { border: 2.5px solid #e0362c; color: #e0362c; background: #fff2f1; }
.nf-map-op { opacity: .75; font-weight: 500; }
.nf-tag { margin-left: 6px; padding: 0 5px; border-radius: 6px; font-size: 9.5px; font-weight: 600;
  color: #6741d9; background: #ece6ff; vertical-align: 1px; }
.nf-map-icon { font-size: 13px; }
.nf-map-kind { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nf-map-tip { display: none; position: absolute; left: 50%; top: calc(100% + 6px); transform: translateX(-50%);
  min-width: 200px; max-width: 360px; padding: 5px 8px; border: 1.5px solid #6741d9; border-radius: 6px;
  background: #fff; color: #3d2a85; box-shadow: 0 3px 10px rgba(0,0,0,.2); line-height: 14px; cursor: default; }
.nf-exmap:hover .nf-map-tip, .nf-exmap:focus .nf-map-tip { display: block; }
.nf-exmap.hot .nf-map-tip { border-color: #e0362c; }
.nf-map-tip > div { white-space: nowrap; }
.nf-map-head { display: flex; justify-content: space-between; gap: 8px; }
.nf-map-title { opacity: .8; }
.nf-map-tkind { color: #fff; background: #6741d9; border-radius: 7px; padding: 0 6px; }
.nf-exmap.unk .nf-map-tkind { background: #9a9aa6; }
.nf-exmap.hot .nf-map-tkind { background: #e0362c; }
.nf-map-pts { font-weight: 500; opacity: .85; }
.nf-map-dir { display: inline-block; min-width: 46px; opacity: .75; font-weight: 500; }
.nf-map-val { padding-left: 46px; color: #e0362c; }
.vue-flow__node-exmap:hover, .vue-flow__node-exmap:focus-within { z-index: 50 !important; }
.vue-flow__node-exmap { z-index: 6 !important; }
.hot-0 .vue-flow__edge.cl-0 path.vue-flow__edge-path,
.hot-1 .vue-flow__edge.cl-1 path.vue-flow__edge-path,
.hot-2 .vue-flow__edge.cl-2 path.vue-flow__edge-path,
.hot-3 .vue-flow__edge.cl-3 path.vue-flow__edge-path,
.hot-4 .vue-flow__edge.cl-4 path.vue-flow__edge-path,
.hot-5 .vue-flow__edge.cl-5 path.vue-flow__edge-path,
.hot-6 .vue-flow__edge.cl-6 path.vue-flow__edge-path,
.hot-7 .vue-flow__edge.cl-7 path.vue-flow__edge-path,
.hot-8 .vue-flow__edge.cl-8 path.vue-flow__edge-path,
.hot-9 .vue-flow__edge.cl-9 path.vue-flow__edge-path,
.hot-10 .vue-flow__edge.cl-10 path.vue-flow__edge-path,
.hot-11 .vue-flow__edge.cl-11 path.vue-flow__edge-path,
.hot-12 .vue-flow__edge.cl-12 path.vue-flow__edge-path,
.hot-13 .vue-flow__edge.cl-13 path.vue-flow__edge-path,
.hot-14 .vue-flow__edge.cl-14 path.vue-flow__edge-path,
.hot-15 .vue-flow__edge.cl-15 path.vue-flow__edge-path {
  stroke: #e0362c !important; stroke-width: 3.5 !important; animation: nfdash .5s linear infinite; }
@keyframes nfdash { to { stroke-dashoffset: -24; } }
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
/* a region waiting for its partner's data, at the node it waits in (amber, dashed) */
.nf-node.wait { background: #fff4db; border-color: #e8a317; border-width: 2.5px; border-style: dashed; }
.nf-decision.wait .d { border-color: #e8a317; border-width: 2.5px; border-style: dashed; background: #fff4db; }
.nf-box-wait { color: #b7790f; font-weight: 600; margin-left: 6px; white-space: nowrap; }
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
