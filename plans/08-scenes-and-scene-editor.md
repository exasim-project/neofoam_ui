# Scenes and a scene editor for the in-situ view

Status: **proposal.** Follows [`03-field-view-and-catalyst.md`](03-field-view-and-catalyst.md).

## Goal

Build in-situ visualisations as node graphs in the UI, save them as scenes in the case, and
switch between them without rebuilding ParaView. The 3D pane shrinks to *scene ▾ · field ▾ ·
camera*; everything else (cuts, colours, filters) lives in the scene.

## Decisions (user)

| # | Decision |
|---|---|
| 1 | A scene is a **JSON graph in the case**: `system/scenes/<name>.json` (versioned with the case). |
| 2 | Editor changes reach the solver on an **explicit Apply** (like ParaView's Apply). |
| 3 | First node set: **core set**: `grid`, Surface, Slice, Clip, Contour, Threshold, Calculator, Glyph, StreamTracer, Display, View. Properties come from a curated list per node. |
| 4 | **The pane shrinks to scene + field + camera.** The built-in scene `Field` reproduces today's options (show, cut, colours) as its default graph. |

## 1. Scene file

```json
{
  "version": 1,
  "nodes": [
    {"id": "grid",  "type": "grid"},
    {"id": "s1",    "type": "Slice",   "props": {"axis": "z", "position": 0.5}},
    {"id": "d1",    "type": "Display", "props": {"field": "@field", "component": "@component",
                                                 "colormap": "Viridis", "range": "auto", "log": false}},
    {"id": "view",  "type": "View",    "props": {"camera": "+z", "zoom": 1.0}}
  ],
  "edges": [["grid", "s1"], ["s1", "d1"], ["d1", "view"]],
  "layout": {"grid": [0, 0], "s1": [200, 0], "d1": [400, 0], "view": [600, 0]}
}
```

- `@field` / `@component` bind a property to the pane's field pick. A scene can also name a
  fixed field.
- Positions (`position`) are 0–1 of the domain extent, as the cut slider is today.
- Several Display nodes can feed one View, e.g. a slice coloured by `p` plus a contour of `U`.
- `layout` holds the editor's node positions only. The solver ignores it.
- The schema is checked on load and on Apply. A bad graph is refused with the node and the
  reason, and the running scene stays.

## 2. Solver side: one ParaView session, scenes kept alive

- `pipelines/view.py` becomes a **graph builder**:
  - It holds every scene that has been applied, as live proxies (`{scene: {node_id: proxy}}`).
  - It builds a scene on first use and keeps it.
- On Apply the builder **diffs** the new graph against the live one:
  - It changes properties, adds or removes nodes and relinks inputs.
  - Unchanged nodes stay as they are.
- **Switching scenes** changes which View is rendered and captured. Only that scene's
  pipeline updates (unshown scenes don't execute).
- The scene graph travels with the view request (`view` command → `options.scene`). Under MPI
  it is part of the view rank 0 shares (`go` / `render`), so every rank builds the same proxies.
  The build is collective, on the solver thread, as today.
- **Data mode:** the extracted source is the shown scene's last filter before its first
  Display (Live sends only that). Several Displays mean several extracts.
- **Errors:** a proxy that fails to build disables that scene only, and the message names the
  node.

## 3. UI

| Panel | Contents |
|---|---|
| **Scenes** (new side panel) | The case's scenes: switch, new, duplicate, rename, delete, save. Current scene highlighted; unsaved edits marked `●`. |
| **Scene editor** (new main tab) | trame-flow node graph (same library as the step graph): palette, drag to connect, selected node's properties, **Apply** / **Revert**. Ports are typed (data → filter → Display → View), so wrong links are refused while dragging. |
| **CFD 3D** (shrunk) | `image \| data`, scene ▾, field ▾, component ▾, live/cadence, camera overlay. The cut and colour controls go into the `Field` scene's nodes. |
| **Pipeline diagram** (existing) | Stays read-only: it shows the live ParaView pipeline with timings. It could be dropped in favour of the editor showing timings on its nodes. Not part of this plan. |

- Save writes `system/scenes/<name>.json` (UI process, case directory). Apply sends the graph
  to the worker. Save and Apply are separate actions.
- A case without `system/scenes/` gets the built-in `Field` scene. Saving it writes the file.

## 4. Export and import

- **Export → Catalyst `.py`:** the builder writes the scene as a standalone Catalyst script,
  using ParaView's own exporter (`paraview.detail.catalyst_export.save_catalyst_state`), so
  the scene also runs without the UI (`NEOFOAM_UI_CATALYST_SCRIPT` or a postProcess `insitu`
  entry).
- **Import:** not in the first version. A ParaView state cannot be mapped back to the core node
  set in general.

## 5. Steps

| # | Step | Tests |
|---|---|---|
| 1 | Scene schema and validation; the built-in `Field` scene equal to today's options | Unit tests: valid and invalid graphs; `Field` gives today's frames |
| 2 | Graph builder in `view.py`: build, diff-apply, switch, per-scene errors | Real ParaView: each node type, property change without rebuild (same proxy id), switching, MPI np 2 |
| 3 | Worker/protocol: `scene` in the view options, shared under MPI | Engine tests, as for the B–D options |
| 4 | Scenes panel and save/load in the case | Pane tests; golden UI updated |
| 5 | Scene editor: palette, typed ports, properties, Apply/Revert | Pane tests; Playwright: build slice + contour, Apply, switch scenes |
| 6 | Pane shrink; the cut and colour controls move into the `Field` scene | Golden UI; today's B–D checks rerun through the scene |
| 7 | Data mode with scenes (extract = the shown scene's display input) | Live test: switching scenes changes the extract |
| 8 | Export to a Catalyst `.py`; run it standalone | Generated script runs on a written case, as a pvbatch smoke test |

## 6. Further decisions (user)

| # | Decision |
|---|---|
| 5 | **The camera is saved in the scene**: the View node holds camera and zoom. Switching restores it. The overlay edits the current scene's view, unsaved until Save. |
| 6 | **Only the shown scene runs.** Hidden scenes stay built but idle. |
| 7 | **multiRun: one `grid` with the `region` field**, as today. A Threshold on `region` picks one region. |
| 8 | **Nodes are pydantic models.** There is one model per node type, and a `Scene` holds a discriminated union over `type`. Validation runs on load and on Apply. The editor's property forms come from `model_json_schema()`. pydantic is a dependency of neofoam_ui. The worker needs it only to apply a custom scene, so the built-in `Field` scene runs without it. |
| 9 | **`Field` is a read-only template.** Editing it creates a copy (`system/scenes/field-copy.json`). |
| 10 | **Two milestones.** **M1:** models, solver builder, protocol, Scenes panel, pane shrink (steps 1–4 and 6). **M2:** node editor, Apply/Revert, data mode per scene, export to `.py` (steps 5, 7, 8). There is a review with screenshots after M1. |
| 11 | **Adding nodes:** click a palette entry to add the node after the selected node, already linked; drag an entry onto the canvas to place it unlinked. |
| 12 | **The property form sits below the graph** in the editor tab, with Apply and Revert, as in the mockup. |
| 13 | **Nodes show errors and timings:** a node that failed to build gets a red border and the message; each node shows its last execute time in ms. The read-only pipeline diagram stays. |
| 14 | **Unapplied edits are kept per scene** until Apply or Revert, and switching back shows them. |

Mockup: [`mockups/scene-editor.html`](mockups/scene-editor.html).
