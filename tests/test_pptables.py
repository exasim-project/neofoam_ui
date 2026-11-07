"""NeoFOAM post-processing tables: the worker's messages, the diagrams, the history and the UI.

A fake ``PostProcessor`` stands in for ``neofoam.postprocess.model.PostProcessor``: the
same private ``_tables`` / ``_open`` / ``_runtime`` shape and ``write(ctx)`` loop, with
duck-typed nodes (``model_fields`` like pydantic models).
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

from neofoam_ui.engine import Session
from neofoam_ui._ui._layout._pipeline import _build_pipeline_flow as build_pipeline_flow
from neofoam_ui._ui.panes._table_history import _TableHistory as TableHistory
from neofoam_ui.pptables import describe_table, observe, rows_message
from neofoam_ui.toy.heat1d import Heat1D

# -- fakes ----------------------------------------------------------------------------------


class Model:
    """A pydantic stand-in: the fields are listed in ``model_fields``."""

    model_fields: dict[str, Any] = {}

    def __init__(self, **values: Any) -> None:
        for key in self.model_fields:
            setattr(self, key, values.get(key, getattr(type(self), key, None)))


class InternalField(Model):
    model_fields = {"type": None, "field": None}
    type = "internal"


class Box(Model):
    model_fields = {"type": None, "min": None, "max": None}
    type = "box"


class Not(Model):
    model_fields = {"type": None, "region": None}
    type = "not"


class Binary(Model):
    model_fields = {"type": None, "op": None, "left": None, "right": None}
    type = "binary"


class Mean(Model):
    model_fields = {"type": None, "name": None}
    type = "mean"


Box.__module__ = Not.__module__ = Binary.__module__ = "neofoam.postprocess.nodes.selectors"
Mean.__module__ = "neofoam.postprocess.nodes.aggregators"


class Interval(Model):
    model_fields = {"write_control_type": None, "interval": None}
    write_control_type = "timeStep"

    def should_write(self, t: Any) -> bool:
        return t.index % self.interval == 0


class CsvWriter(Model):
    model_fields = {"type": None}
    type = "csv"

    def __init__(self, **values: Any) -> None:
        super().__init__(**values)
        self.rows: list[tuple[float, Any]] = []

    def write(self, time_value: float, result: Any) -> None:
        self.rows.append((time_value, result))


class Pipeline:
    def __init__(self, source: Any, steps: list[Any], result: Any) -> None:
        self.source, self.steps, self._result = source, steps, result

    def compute(self, ctx: Any) -> Any:
        return self._result(ctx)


class Table(NamedTuple):
    name: str
    pipeline: Pipeline
    write_control: Any
    writer: Any


class Runtime(NamedTuple):
    write_control: Any
    writer: Any


class Row:
    def __init__(self, value: Any, group: Any = None, group_name: Any = None) -> None:
        self.value, self.group, self.group_name = value, group, group_name


class Aggregated:
    """``AggregatedDataSet``: value columns first, then the group labels."""

    def __init__(self, name: str, values: list[Row]) -> None:
        self.name, self.values = name, values

    @property
    def headers(self) -> list[str]:
        first = self.values[0]
        cols = [f"{self.name}_{i}" for i in range(len(first.value))] if isinstance(first.value, list) \
            else [self.name]
        return [*cols, *(first.group_name or [])]

    @property
    def grouped_values(self) -> list[list[Any]]:
        return [[*(r.value if isinstance(r.value, list) else [r.value]), *(r.group or [])]
                for r in self.values]


class PostProcessor:
    """The shape of NeoFOAM's: private runtime copies opened on the first write."""

    def __init__(self, tables: list[Table]) -> None:
        self._tables = tables
        self._runtime: dict[str, Runtime] = {}
        self.writers: dict[str, CsvWriter] = {}

    def write(self, ctx: Any) -> None:
        if not self._runtime:
            self._runtime = self._open(False)
        for table in self._tables:
            control, writer = self._runtime[table.name]
            if control.should_write(ctx.time):
                writer.write(ctx.time.value, table.pipeline.compute(ctx))

    def _open(self, append: bool) -> dict[str, Runtime]:
        self.writers = {t.name: CsvWriter() for t in self._tables}
        return {t.name: Runtime(t.write_control, self.writers[t.name]) for t in self._tables}


class Time(NamedTuple):
    value: float
    index: int


class Ctx(NamedTuple):
    time: Time


def processor() -> PostProcessor:
    region = Binary(op="and", left=Box(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0)),
                    right=Not(region=Box(min=(0.2, 0.2, 0.0), max=(0.4, 0.4, 1.0))))
    mean = Table("p_mean", Pipeline(InternalField(field="p"), [region, Mean(name="p_mean")],
                                    lambda ctx: Aggregated("p_mean", [Row(ctx.time.value * 2)])),
                 Interval(interval=1), CsvWriter())
    binned = Table("U_bins", Pipeline(InternalField(field="U"), [Mean()],
                                      lambda ctx: Aggregated("U", [Row([1.0, 2.0, float("nan")], [float(b)], ["bin"])
                                                                   for b in range(3)])),
                   Interval(interval=2), CsvWriter())
    return PostProcessor([mean, binned])


# -- the worker side ------------------------------------------------------------------------


def test_observed_tables_report_their_rows_and_still_write():
    pp, messages = processor(), []
    assert observe(pp, messages.append)
    assert messages[0]["type"] == "pptables"
    assert [t["name"] for t in messages[0]["tables"]] == ["p_mean", "U_bins"]
    for index in range(1, 4):
        pp.write(Ctx(Time(index * 0.1, index)))
    rows = [m for m in messages if m["type"] == "pptable"]
    assert [(m["name"], m["time"]) for m in rows] == [("p_mean", 0.1), ("p_mean", 0.2), ("U_bins", 0.2),
                                                      ("p_mean", 0.30000000000000004)]
    assert rows[0]["columns"] == ["p_mean"] and rows[0]["values"] == [[0.2]] and rows[0]["groups"] == [[]]
    binned = rows[2]
    assert binned["columns"] == ["U_0", "U_1", "U_2"] and binned["group_columns"] == ["bin"]
    assert binned["values"][0] == [1.0, 2.0, None]  # NaN is no JSON
    assert binned["groups"] == [[0], [1], [2]]
    assert all(m["ms"] >= 0 for m in rows)
    # the real writers got every result, unchanged
    assert [t for t, _ in pp.writers["p_mean"].rows] == [0.1, 0.2, 0.30000000000000004]
    assert len(pp.writers["U_bins"].rows) == 1


def test_a_processor_already_opened_is_wrapped_too():
    pp, messages = processor(), []
    pp.write(Ctx(Time(0.1, 1)))
    observe(pp, messages.append)
    pp.write(Ctx(Time(0.2, 2)))
    assert [m["name"] for m in messages if m["type"] == "pptable"] == ["p_mean", "U_bins"]


def test_something_else_is_not_observed():
    messages: list[dict[str, Any]] = []
    assert not observe(object(), messages.append)
    assert messages == []


def test_a_broken_result_is_reported_once_and_the_csv_still_written():
    pp, messages = processor(), []
    pp._tables[0].pipeline._result = lambda ctx: "not an aggregation"
    observe(pp, messages.append)
    pp.write(Ctx(Time(0.1, 1)))
    pp.write(Ctx(Time(0.2, 2)))
    assert [m["type"] for m in messages[1:]].count("stdout") <= 1
    assert len(pp.writers["p_mean"].rows) == 2


def test_rows_message_of_an_empty_result_is_none():
    assert rows_message("r", 0.1, Aggregated("r", [])) is None


def test_pipeline_is_described_from_the_node_objects():
    desc = describe_table(processor()._tables[0])
    by_id = {n["id"]: n for n in desc["nodes"]}
    src, region, mean, writer = (by_id[f"nf:p_mean:{k}"] for k in ("source", "0", "1", "writer"))
    assert (src["kind"], src["type"], src["settings"]) == ("source", "internal", [["field", "p"]])
    assert (region["kind"], region["type"], region["settings"]) == ("selector", "binary", [["op", "and"]])
    assert (mean["kind"], mean["name"]) == ("aggregator", "p_mean")
    assert writer["kind"] == "writer" and writer["type"] == "csv"
    assert writer["settings"][:2] == [["when", "timeStep"], ["interval", "1"]]
    chain = [(e["source"], e["target"]) for e in desc["edges"] if "label" not in e]
    assert chain == [("nf:p_mean:source", "nf:p_mean:0"), ("nf:p_mean:0", "nf:p_mean:1"),
                     ("nf:p_mean:1", "nf:p_mean:writer")]
    # selectors held by a selector are nodes of their own, labelled with the field
    nested = {(e["source"], e["label"]) for e in desc["edges"] if "label" in e}
    assert nested == {("nf:p_mean:0.left", "left"), ("nf:p_mean:0.right", "right"),
                      ("nf:p_mean:0.right.region", "region")}
    assert by_id["nf:p_mean:0.left"]["settings"] == [["min", "(0, 0, 0)"], ["max", "(1, 1, 1)"]]
    assert by_id["nf:p_mean:0.right"]["type"] == "not"


def test_a_registered_describer_describes_its_nodes(monkeypatch):
    from neofoam_ui.pptables import _describe  # noqa: PLC0415
    from neofoam_ui.pptables import register_describer  # noqa: PLC0415

    monkeypatch.setattr(_describe, "_DESCRIBERS", [])

    def binary(obj):  # e.g. a case's own selector, drawn as one node
        if getattr(obj, "type", None) != "binary":
            return None
        return {"name": "region", "settings": [["rule", "left and not right"]]}

    register_describer(binary)
    desc = describe_table(processor()._tables[0])
    by_id = {n["id"]: n for n in desc["nodes"]}
    node = by_id["nf:p_mean:0"]
    assert (node["name"], node["type"], node["kind"]) == ("region", "binary", "selector")
    assert node["settings"] == [["rule", "left and not right"]]
    assert "nf:p_mean:0.left" not in by_id  # its nested plugins are its own business
    assert by_id["nf:p_mean:1"]["kind"] == "aggregator"  # the others: the generic reading


def test_tables_are_drawn_as_frames_without_catalyst():
    tables = [describe_table(t) for t in processor()._tables] * 5
    tables = [{**t, "name": f"{t['name']}{i}",
               "nodes": [{**n, "id": f"{i}{n['id']}"} for n in t["nodes"]],
               "edges": [{**e, "source": f"{i}{e['source']}", "target": f"{i}{e['target']}"}
                         for e in t["edges"]]} for i, t in enumerate(tables)]
    flow = build_pipeline_flow(None, tables)
    frames = [n for n in flow["nodes"] if n["type"] == "activity"]
    assert [f["id"] for f in frames] == [f"pp:act:{t['name']}" for t in tables]
    assert frames[0]["data"]["label"] == "NeoFOAM post-processing · p_mean0"
    assert len({f["position"]["y"] for f in frames}) > 1  # wrapped onto further rows
    inner = [n for n in flow["nodes"] if n.get("parentNode") == "pp:act:p_mean0"]
    assert len(inner) == 7  # source, binary + its 3 selectors, mean, writer
    labels = {e.get("label") for e in flow["edges"]}
    assert {"left", "right", "region"} <= labels
    both = build_pipeline_flow({"nodes": [], "edges": []}, tables[:1])
    assert [n["id"] for n in both["nodes"] if n["type"] == "activity"] == ["pp:act", "pp:act:p_mean0"]


# -- the engine hook ------------------------------------------------------------------------


class Observing(Heat1D):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[Any] = []

    def observe(self, value: Any, emit: Any) -> None:
        self.seen.append(value)
        if isinstance(value, dict) and "boom" in value:
            raise RuntimeError("observer bug")


def test_session_offers_every_init_result_to_the_solver():
    solver, messages = Observing(), []
    session = Session(solver, messages.append)
    session.start()
    session.advance("skip_init")
    assert len(solver.seen) == len(session.init)
    session.observe_result({"boom": 1})  # a failing observer is reported, not raised
    assert messages[-1]["type"] == "stdout" and "observer bug" in messages[-1]["text"]


# -- the UI side ----------------------------------------------------------------------------


def msg(name: str, t: float, values: list[list[Any]], groups: Any = None, columns: Any = None,
        group_columns: Any = None) -> dict[str, Any]:
    return {"type": "pptable", "name": name, "time": t, "columns": columns or [name],
            "group_columns": group_columns or [], "values": values,
            "groups": groups or [[] for _ in values], "ms": 0.5}


def test_history_is_bounded_and_one_line_per_column():
    history = TableHistory(max_points=5)
    history.declare(["F", "empty"])
    for i in range(8):
        history.add(msg("F", i / 10, [[i, -i, 1e15]], columns=["F_0", "F_1", "F_2"]))
    charts = history.charts()
    assert [c["name"] for c in charts] == ["F", "empty"]
    data = charts[0]["data"]
    assert [d["name"] for d in data] == ["F_0", "F_1", "F_2"]
    assert data[0]["x"] == [0.3, 0.4, 0.5, 0.6, 0.7]
    assert data[1]["y"] == [-3, -4, -5, -6, -7]
    assert data[2]["y"] == [None] * 5  # NeoFOAM's GREAT: no data
    assert charts[1]["data"] == [] and charts[1]["note"] == "no rows yet"


def test_binned_tables_get_a_capped_line_per_row():
    history = TableHistory(max_lines=4)
    for t in (0.1, 0.2):
        history.add(msg("prof", t, [[b * t] for b in range(6)], groups=[[b] for b in range(6)],
                        group_columns=["bin"]))
    chart = history.charts()[0]
    assert [d["name"] for d in chart["data"]] == ["bin=0", "bin=1", "bin=2", "bin=3"]
    assert "6 rows per step (by bin)" in chart["note"] and "first 4 of 6 lines" in chart["note"]
    assert chart["layout"]["showlegend"]


def test_rows_that_come_and_go_leave_gaps_and_a_repeated_step_replaces():
    history = TableHistory()
    history.add(msg("r", 0.1, [[1.0]], groups=[["p"]], group_columns=["field"]))
    history.add(msg("r", 0.2, [[2.0], [3.0]], groups=[["p"], ["U"]], group_columns=["field"]))
    history.add(msg("r", 0.2, [[4.0], [5.0]], groups=[["p"], ["U"]], group_columns=["field"]))
    data = {d["name"]: d for d in history.charts()[0]["data"]}
    assert data["field=p"]["y"] == [1.0, 4.0] and data["field=U"]["y"] == [None, 5.0]
    assert data["field=U"]["x"] == [0.1, 0.2]
    history.clear()
    assert history.charts() == []


def test_app_draws_tables_and_plots_their_rows():
    from trame.app import get_server  # noqa: PLC0415

    from neofoam_ui.app import StepperApp  # noqa: PLC0415

    server = get_server("pptables_test", client_type="vue3")
    app = StepperApp(server, "neofoam_ui.toy.heat1d:Heat1D")
    tables = [describe_table(t) for t in processor()._tables]
    s = app.state
    with s:
        app._handle({"type": "pptables", "tables": tables})
        assert s.pp_graph and "NeoFOAM: 2 tables" in s.pp_msg
        frames = [n["id"] for n in app.pp_editor.graph["nodes"] if n["type"] == "activity"]
        assert frames == ["pp:act:p_mean", "pp:act:U_bins"]
        # Catalyst not configured: the tables stay
        app._handle({"type": "frame", "file": None, "fields": [], "error": "in-situ view off"})
        assert s.pp_graph and "in-situ view off" in s.pp_msg
        app._handle(msg("p_mean", 0.1, [[1.0]]))
        assert s.pt_charts == []  # the Plots pane is closed: nothing sent
        s.pt_on = True
        app._plot(force=True)
        # the select box offers every table; the first is chosen, only chosen ones are charted
        assert s.pt_names == ["p_mean", "U_bins"] and s.pt_sel == ["p_mean"]
        assert [c["name"] for c in s.pt_charts] == ["p_mean"]
        assert s.pt_charts[0]["data"][0]["y"] == [1.0]
        s.pt_sel = ["U_bins", "p_mean"]
        app._plot(force=True)
        assert [c["name"] for c in s.pt_charts] == ["p_mean", "U_bins"]  # declaration order
        assert s.pp_ms["pp:act:p_mean"] == "0.5 ms"
        app._reset_state()  # what a restart does
        assert s.pt_charts == [] and app.history.charts() == [] and s.pt_on
        assert s.pt_sel == ["U_bins", "p_mean"]  # the choice survives


def test_no_tables_says_so():
    from trame.app import get_server  # noqa: PLC0415

    from neofoam_ui.app import StepperApp  # noqa: PLC0415

    app = StepperApp(get_server("pptables_none", client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D")
    with app.state:
        app._handle({"type": "pptables", "tables": []})
    assert "declares no post-processing tables" in app.state.pt_msg
    assert not app.state.pp_graph
    assert math.isfinite(app._plotted)
