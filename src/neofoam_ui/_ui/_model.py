"""What the UI knows of the run: the graph (indexed for navigation) and the timings (no trame)."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from neofoam_ui._ui._layout._palette import _REGION, _node_color

__all__ = ()


class _RunModel:
    """The graph messages, an index of every node, and the books of the last ``paused``."""

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.init_steps: list[dict[str, Any]] = []
        self.ops: list[dict[str, Any]] = []
        self.index: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, int] = {}
        self.last_ms: dict[str, float] = {}
        self.total_ms: dict[str, float] = {}
        #: Fields each action was observed to change.
        self.writes_seen: dict[str, list[str]] = {}
        #: A coupling loop's residuals after its last pass (relative change per field).
        self.residuals: dict[str, dict[str, float]] = {}

    def graph(self, msg: dict[str, Any]) -> None:
        """A ``graph`` message: the init DAG or the execution tree."""
        if msg["activity"] == "init":
            self.init_steps = msg["steps"]
        else:
            self.ops = msg["ops"]

    def books(self, msg: dict[str, Any]) -> None:
        """The timings and observed writes of a ``paused`` message."""
        self.calls, self.last_ms = msg["calls"], msg["last_ms"]
        self.total_ms = msg.get("total_ms", {})
        self.writes_seen = msg.get("writes_seen", {})

    def reindex(self, solver_name: str) -> list[dict[str, Any]]:
        """Index every node (neighbours, parents, children); the containment tree's rows."""
        self.index = {}
        tree: list[dict[str, Any]] = [{"id": "act:init", "name": "Initialization", "depth": 0,
                                       "color": "#8a94a6", "kind": "activity"}]
        for s in self.init_steps:
            self.index[s["id"]] = {**s, "kind": "init",
                                   "preds": s["depends_on"],
                                   "succs": [t["id"] for t in self.init_steps if s["id"] in t["depends_on"]]}
            tree.append({"id": s["id"], "name": s["name"], "depth": 1, "kind": "init",
                         "color": _node_color(s, "category")})
        tree.append({"id": "act:exec", "name": f"Execution · {solver_name}", "depth": 0,
                     "color": "#8a94a6", "kind": "activity"})

        def walk(ops: list[dict[str, Any]], parent: Optional[str], depth: int) -> None:
            for i, op in enumerate(ops):
                prev = ops[i - 1]["id"] if i > 0 else parent
                nxt = ops[i + 1]["id"] if i < len(ops) - 1 else parent
                self.index[op["id"]] = {**op, "parent": parent, "prev": prev, "next": nxt,
                                        "child_ids": [c["id"] for c in op["children"]]}
                tree.append({"id": op["id"], "name": op["name"], "depth": depth, "kind": op["kind"],
                             "color": _node_color(op, "domain")})
                walk(op["children"], op["id"], depth + 1)

        walk(self.ops, None, 1)
        return tree

    def regions(self) -> list[dict[str, str]]:
        """The regions of the graph, in order of appearance (execution graph first), with their colour (the legend)."""
        seen: dict[str, None] = {}

        def walk(ops: list[dict[str, Any]]) -> None:
            for op in ops:
                if op.get("region"):
                    seen.setdefault(op["region"], None)
                walk(op["children"])

        walk(self.ops)  # (the execution graph's order first: the regions' stepping order)
        for s in self.init_steps:
            if s.get("region"):
                seen.setdefault(s["region"], None)
        return [{"name": r, "color": _REGION.color(r)} for r in seen]

    def name(self, node_id: Optional[str]) -> str:
        if not node_id:
            return "—"
        info = self.index.get(node_id)
        return info["name"] if info else node_id

    def shown(self, node_id: str, collapsed: Iterable[str]) -> str:
        """``node_id``, or the outermost collapsed loop hiding it."""
        collapsed = set(collapsed)
        shown, cur = node_id, self.index.get(node_id, {}).get("parent")
        while cur:
            if cur in collapsed:
                shown = cur
            cur = self.index[cur]["parent"]
        return shown

    def subtree_ms(self, op_id: str) -> float:
        info = self.index[op_id]
        return self.total_ms.get(op_id, 0.0) + sum(self.subtree_ms(c) for c in info["child_ids"])

    def event_label(self, ev: dict[str, Any]) -> str:
        if ev["kind"] == "end":
            return "end"
        name = self.name(ev["node"])
        return f"◇ {name} test #{ev['iter']}" if ev["kind"] == "test" else name
