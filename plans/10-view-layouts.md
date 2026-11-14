# A View with a layout

Status: **agreed, in progress.** Follows [`09-scene-editor-ports-and-delete.md`](09-scene-editor-ports-and-delete.md).
Mockup: [`mockups/scene-view-layout.html`](mockups/scene-view-layout.html).

## Goal

The View node gets a **layout**. Each cell of the layout is an input port of its own; the
Displays linked to a port are drawn in that cell. The solver renders one ParaView render view
per cell, arranged in a ParaView layout, and captures the whole layout as one picture.

## Decisions (user)

| # | Decision |
|---|---|
| L1 | **Fixed layouts:** `1`, `1x2`, `2x1`, `2x2`. Port names follow the layout: `1` → `displays`; `1x2` → `left`, `right`; `2x1` → `top`, `bottom`; `2x2` → `tl`, `tr`, `bl`, `br`. |
| L2 | **Cameras linked by default**; `per cell` is a View property. Linked: one camera (as today). Per cell: one camera per cell, saved in the scene. |
| L3 | **Fewer cells:** switching to a layout with fewer cells drops the links of the removed cells (a normal draft edit; Revert restores them). |
| L4 | **Image mode only** in this plan. Data mode is greyed out (with the reason) for a scene whose View has more than one cell. |
| L5 | **Grid per region** is a separate, later plan; decision 7 of plan 08 stays. |
| L6 | **Layout change maps cells by position** (`displays` → first cell; 2x2 `tl` → 1x2 `left`, …); cells beyond the new count lose their links. |
| L7 | **Per-cell cameras** are `cell_cameras: {cell: {camera, zoom}}`; the View's own `camera`/`zoom` is the linked camera and the fallback. |
| L8 | **Linked cameras share one fit** (room for the most colour bars any cell has). |
| L9 | **Export and `out=` write one combined PNG** of the whole layout per step, not one per cell. |

## Scene file

An edge to the View names the cell: `["d1", "view", "left"]` (a third element, the target
port). Edges to a one-cell View may omit it (`displays`). The View's props gain
`layout` (default `1`) and `cameras` (`linked` | `per_cell`, default `linked`); per cell, the
View holds a camera per cell (`camera`/`zoom` keyed by cell).

## Steps

| # | Step | Tests |
|---|---|---|
| 1 | Model: `layout`, `cameras`, per-cell cameras on View; edge target port; validation (port exists for the layout, a Display links to one cell); the layout's port names from the model | Unit tests: valid/invalid graphs, old files load unchanged |
| 2 | Solver (`pipelines/view.py`): one render view per cell in a ParaView layout, Displays shown in their cell's view; diff-apply when the layout changes; capture the layout as one PNG at the pane size (MPI np 2); per-cell or linked camera; timings per cell | Real ParaView: 1x2 and 2x2 frames, layout change without rebuilding the filters, np 2 |
| 3 | Editor: View node with one bar port per cell (named), edges carry the target port; layout and cameras in the form; fewer cells drop links | Pane tests; golden UI |
| 4 | Pane: camera buttons and zoom act on the cell under the mouse when cameras are per cell (all cells when linked); data mode greyed out for multi-cell | Pane tests; Playwright: build 1x2, Apply, picture shows two cells |
| 5 | Export `.py` writes the layout | pvbatch smoke test of a 1x2 scene |
