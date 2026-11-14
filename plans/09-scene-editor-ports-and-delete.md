# Scene editor: visible ports and deleting

Status: **agreed, not started.** Follows [`08-scenes-and-scene-editor.md`](08-scenes-and-scene-editor.md).
Mockup: [`mockups/scene-editor-ports.html`](mockups/scene-editor-ports.html).

## Decisions (user)

| # | Decision |
|---|---|
| P1 | **Named ports:** 12 px dots on the node edge, named in a row under the header (`mesh`, `input`, `output`, `to view`, `displays`), coloured by type (data blue, display purple). |
| P3 | **Many-input port:** View's `displays` port is a bar; several Display links join it. |
| P5 | **Del / Backspace** removes the selected node or link. The removal goes through the server like every edit (draft, Apply / Revert). Fixes today's bug: Backspace removes the node in the browser only and the canvas goes blank. |
| P6 | **Links can be selected:** a click selects a link (drawn thick); right-click → Remove link, or Del, removes it. |
| P7 | **Deleting is by right-click** (Del / Backspace as the shortcut). Node menu: remove (Del), remove keeping links (⇧Del), duplicate (Ctrl D), disconnect input, disconnect outputs; not on `grid` / `View`. Link menu: remove link (Del). |
| P8 | **Remove keeping links:** the removed filter's input is linked to each of its outputs (only where the port types match). |
| — | **Not part of this plan:** P2 (glow fitting ports while dragging), P4 (× on node headers), × on links, P9 (footer button). The form's "Remove node" button goes. |
| Q1 | **"+ New" scene** starts as `grid → Display → View`. |
| Q2 | **One data input per filter.** StreamTracer seeds stay a property. |
| Q3 | **Duplicate** links the copy to the original's input; its output stays free. |
| Q4 | **Port names come from the node models** (next to `takes`/`gives`), so the palette, the editor and validation share them. |

## Steps

| # | Step | Tests |
|---|---|---|
| 1 | **Menu with delete, and ports.** Right-click menus with *Remove node* / *Remove link* only (`node-context-menu`, `edge-context-menu`; none on grid/View); link selection (P6); Del/Backspace through the server, Vue Flow's own delete off (`delete-key-code: null`, P5, fixes the blank canvas); the form's button goes. Named ports from the node models (P1, Q4) and View's bar (P3) | Pane tests: remove node/link, Revert brings it back, server and canvas stay in step; Playwright: right-click → remove, Backspace, canvas stays drawn; golden UI updated |
| 2 | **The rest of the menu:** duplicate (Q3), disconnect input/outputs, remove keeping links (P8) | Unit tests on the graph edits (keep links: types checked, no duplicate edges) |
| 3 | **"+ New"** starts as grid → Display → View (Q1) | Pane test |
