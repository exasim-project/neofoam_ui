"""In-situ field view: the view protocol (Catalyst faked), geometry, and — where a
Catalyst + ParaView install is configured — a real frame rendered through Catalyst 2."""

import gzip
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from neofoam_ui import catalyst
from neofoam_ui.engine import Session
from neofoam_ui.geometry import cell_centres_geometry, polyhedral, read_polymesh
from neofoam_ui.toy.heat1d import Heat1D

HELPERS = os.path.join(os.path.dirname(__file__), "helpers")

# -- engine: the view protocol ------------------------------------------------------------


class FakeInSitu:
    calls: list = []

    def __init__(self):
        self.step = 0

    def execute(self, geom, fields, time, params):
        FakeInSitu.calls.append({"geom": geom, "fields": fields, "time": time, "params": params})
        self.step += 1
        return f"view_{self.step - 1:06d}.png"

    def describe(self):
        FakeInSitu.calls.append("describe")
        return {**json.loads(json.dumps(FakeInSitu.pipeline)), "ms": {"total": 2.5, "pv1": 0.5}}

    def finalize(self):
        FakeInSitu.calls.append("finalize")


PIPELINE = {
    "nodes": [{"id": "pv1", "kind": "source", "name": "grid", "type": "Conduit", "settings": []},
              {"id": "pv2", "kind": "view", "name": "RenderView1", "type": "Render View",
               "settings": [["size", "800×400"]]}],
    "edges": [{"source": "pv1", "target": "pv2"}],
    "script": "view.py",
}


@pytest.fixture(autouse=True)
def fake_catalyst(monkeypatch, tmp_path):
    FakeInSitu.calls = []
    FakeInSitu.pipeline = json.loads(json.dumps(PIPELINE))
    monkeypatch.setenv(catalyst.ENV, "/nowhere")
    monkeypatch.setenv(catalyst.OUT_ENV, str(tmp_path))
    monkeypatch.setattr(catalyst, "InSitu", FakeInSitu)
    return FakeInSitu.calls


def renders(calls):
    return [c for c in calls if isinstance(c, dict)]


def session_after_init(solver=None):
    messages = []
    session = Session(solver or Heat1D(), messages.append)
    session.start()
    session.advance("skip_init")
    return session, messages


def of(messages, kind):
    return [m for m in messages if m["type"] == kind]


def test_closed_view_renders_nothing(fake_catalyst):
    session, messages = session_after_init()
    session.advance("time_step")
    assert not of(messages, "frame") and not fake_catalyst


def test_view_before_context_waits_for_initialization(fake_catalyst):
    messages = []
    session = Session(Heat1D(), messages.append)
    session.start()
    session.set_view("T")
    assert not of(messages, "frame")  # no ctx during init
    session.advance("skip_init")
    assert len(of(messages, "frame")) == 1 and len(renders(fake_catalyst)) == 1


def test_frames_only_when_values_change(fake_catalyst):
    session, messages = session_after_init()
    session.set_view("T")
    (frame,) = of(messages, "frame")
    assert frame["field"] == "T" and frame["file"] == "view_000000.png"
    call = renders(fake_catalyst)[0]
    assert call["params"] == {"field": "T", "component": "mag"}
    assert call["geom"]["kind"] == "line"
    # every cell-sized field goes to Catalyst (flux lives on the 41 faces)
    assert set(call["fields"]) == {"T", "T_old", "kappa", "source_mask"}
    assert {f["name"] for f in frame["fields"]} == {"T", "T_old", "kappa", "source_mask"}

    session.advance("into")  # set_time_step: T unchanged -> no new frame
    assert len(of(messages, "frame")) == 1
    session.advance("time_step")  # T changed
    frames = of(messages, "frame")
    assert len(frames) == 2 and frames[-1]["file"] == "view_000001.png"
    # each frame precedes the pause it belongs to
    assert messages[-1]["type"] == "paused" and messages[-2] is frames[-1]


def test_switching_field_and_closing():
    session, messages = session_after_init()
    session.set_view("T")
    session.set_view("kappa")
    assert of(messages, "frame")[-1]["field"] == "kappa"
    session.set_view(None, on=False)
    n = len(of(messages, "frame"))
    session.advance("time_step")
    assert len(of(messages, "frame")) == n


def test_unknown_field_falls_back_to_a_default():
    session, messages = session_after_init()
    session.set_view("flux")  # not cell-sized
    assert of(messages, "frame")[-1]["field"] == "T"


class Vec(Heat1D):
    """Heat1D with a vector field and a point geometry."""

    def fields(self, ctx):
        n = len(ctx.x)
        return {**ctx.fields, "U": np.stack([ctx.fields["T"], np.ones(n), np.zeros(n)], axis=1)}

    def mesh(self, ctx):
        return cell_centres_geometry(np.stack([ctx.x, 0.1 * ctx.x, np.zeros_like(ctx.x)], axis=1))


def test_vector_components(fake_catalyst):
    session, messages = session_after_init(Vec())
    session.set_view("U", "1")
    assert of(messages, "frame")[-1]["component"] == "1"
    assert renders(fake_catalyst)[-1]["params"] == {"field": "U", "component": "1"}
    session.set_view("U", "7")  # no such component
    assert of(messages, "frame")[-1]["component"] == "mag"
    assert {"name": "U", "ncomp": 3} in of(messages, "frame")[-1]["fields"]
    assert renders(fake_catalyst)[-1]["geom"]["kind"] == "points"


class NoMesh(Heat1D):
    mesh = None


def test_solver_without_geometry_reports_it_once():
    session, messages = session_after_init(NoMesh())
    session.set_view("T")
    session.advance("time_step")
    frames = of(messages, "frame")
    assert len(frames) == 1 and frames[0]["file"] is None and "no mesh" in frames[0]["error"]


def test_unconfigured_catalyst_says_so(monkeypatch):
    monkeypatch.delenv(catalyst.ENV)
    session, messages = session_after_init()
    session.set_view("T")
    (frame,) = of(messages, "frame")
    assert frame["file"] is None and catalyst.ENV in frame["error"]


def test_a_failing_catalyst_disables_the_view_not_the_session(monkeypatch):
    def boom(self, *a):
        raise RuntimeError("no GPU")

    monkeypatch.setattr(FakeInSitu, "execute", boom)
    session, messages = session_after_init()
    session.set_view("T")
    session.advance("time_step")
    assert "no GPU" in of(messages, "frame")[-1]["error"]
    assert not session.failed and messages[-1]["type"] == "paused"


def test_other_ranks_render_the_same_frame(fake_catalyst):
    session, _ = session_after_init()
    told = []
    session.on_render = told.append
    session.set_view("kappa")
    assert told == [{"field": "kappa", "component": "mag"}]
    session.finalize_view()
    assert fake_catalyst[-1] == "finalize"


def test_view_request_from_another_thread_takes_effect_at_the_next_live_frame():
    session, messages = session_after_init()
    session.set_view("T")

    def emit(msg):  # the command reader switching the field while the run is going
        messages.append(msg)
        if msg["type"] == "frame" and len(of(messages, "frame")) == 2:
            session.request_view("kappa")

    session.emit = emit
    session.advance("run")
    frames = of(messages, "frame")
    assert [f["field"] for f in frames[:3]] == ["T", "T", "kappa"]
    assert all(f["field"] == "kappa" and f["view"] == ["kappa", "mag"] for f in frames[2:])
    assert messages.index(frames[2]) < messages.index(of(messages, "paused")[-1])


def test_reopened_view_renders_again_an_unchanged_one_not():
    session, messages = session_after_init()
    session.set_view("T")
    session.set_view("T")  # same request, same values: no new frame
    assert len(of(messages, "frame")) == 1
    session.set_view(None, on=False)
    session.set_view("T")
    assert len(of(messages, "frame")) == 2


def test_frame_names_the_request_it_answers():
    session, messages = session_after_init()
    session.set_view("flux", "2")
    frame = of(messages, "frame")[-1]
    assert (frame["field"], frame["component"], frame["view"]) == ("T", "mag", ["flux", "2"])


# -- post-processing diagram: the pipeline read back after each execute ------------------------


def test_pipeline_goes_with_the_frame_only_when_it_changes(fake_catalyst):
    session, messages = session_after_init()
    session.set_view("T")
    first = of(messages, "frame")[-1]
    assert first["pipeline"] == PIPELINE and "ms" not in first["pipeline"]
    assert first["pipeline_ms"] == {"total": 2.5, "pv1": 0.5}
    session.advance("time_step")  # same pipeline: only the timings
    frame = of(messages, "frame")[-1]
    assert "pipeline" not in frame and frame["pipeline_ms"]["total"] == 2.5
    FakeInSitu.pipeline["nodes"][1]["settings"] = [["size", "640×480"]]  # e.g. a colour range moved
    session.advance("time_step")
    assert of(messages, "frame")[-1]["pipeline"]["nodes"][1]["settings"] == [["size", "640×480"]]


def test_only_the_deciding_rank_reads_the_pipeline_back(fake_catalyst):
    session, _ = session_after_init()
    session.render({"field": "T", "component": "mag"})  # how a follower renders
    assert "describe" not in fake_catalyst and session.pipeline is None


def test_a_failing_description_keeps_the_frames(monkeypatch):
    def boom(self):
        raise RuntimeError("no proxy manager")

    monkeypatch.setattr(FakeInSitu, "describe", boom)
    session, messages = session_after_init()
    session.set_view("T")
    session.advance("time_step")
    frames = of(messages, "frame")
    assert len(frames) == 2 and all(f["file"] and "pipeline" not in f for f in frames)
    reports = [m for m in of(messages, "stdout") if "no proxy manager" in m["text"]]
    assert len(reports) == 1  # once, then no more diagrams


def test_pipeline_layout_layers_by_data_flow():
    from neofoam_ui.layout import build_pipeline_flow

    desc = {"script": "x.py", "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"},
                                        {"source": "a", "target": "e"}, {"source": "c", "target": "gone"}],
            "nodes": [{"id": i, "kind": k, "name": i, "type": k, "settings": [["k", "v"]] * n}
                      for i, k, n in (("e", "extractor", 1), ("c", "view", 2), ("b", "filter", 0),
                                      ("a", "source", 0))]}
    flow = build_pipeline_flow(desc)
    pos = {n["id"]: n["position"]["y"] for n in flow["nodes"] if n["type"] == "pp"}
    assert pos["a"] < pos["b"] < pos["c"]
    assert pos["a"] < pos["e"] < pos["c"]  # e is fed by a only: the row below it
    assert flow["nodes"][0]["data"]["label"] == "ParaView (Catalyst) · x.py"
    assert {(e["source"], e["target"]) for e in flow["edges"]} == {("a", "b"), ("b", "c"), ("a", "e")}


class Extra(Heat1D):
    """Heat1D with a field only the view offers (like a turbulence model's ``k``)."""

    def view_fields(self, ctx):
        return {**self.fields(ctx), "k": 2.0 * ctx.fields["T"]}


def test_view_offers_view_fields_the_stats_table_does_not(fake_catalyst):
    session, messages = session_after_init(Extra())
    session.set_view("k")
    frame = of(messages, "frame")[-1]
    assert frame["field"] == "k" and {"name": "k", "ncomp": 1} in frame["fields"]
    assert "k" in renders(fake_catalyst)[-1]["fields"]
    assert "k" not in {row["field"] for row in session.summary()["fields"]}


def test_neofoam_view_fields_from_the_mesh_registry(monkeypatch):
    from types import SimpleNamespace

    from neofoam_ui.adapters.neofoam import _FieldsMixin

    class Field:
        def __init__(self, name, values):
            self._name, self.values = name, np.asarray(values, dtype=float)

        def name(self):
            return self._name

        def internalField(self):  # noqa: N802 - pybFoam's name
            return self.values

    registry = {"p": Field("p", [1, 2]), "k": Field("k", [3, 4]), "k_0": Field("k_0", [0, 0]),
                "pPrevIter": Field("pPrevIter", [0, 0]), "nut": Field("nut", [5, 6])}
    vectors = {"U": Field("U", [[1, 0, 0], [0, 1, 0]]), "U_0": Field("U_0", [[0, 0, 0]] * 2)}

    def kind(objects):
        return SimpleNamespace(list_objects=lambda mesh: list(objects),
                               from_registry=lambda mesh, name: objects[name])

    monkeypatch.setitem(sys.modules, "pybFoam",
                        SimpleNamespace(volScalarField=kind(registry), volVectorField=kind(vectors)))
    ctx = SimpleNamespace(mesh=object(), fields={"pressure": registry["p"], "U": vectors["U"]})
    fields = _FieldsMixin().view_fields(ctx)
    assert list(fields) == ["pressure", "U", "k", "nut"]  # p is "pressure" already
    assert fields["k"].tolist() == [3, 4]
    assert list(_FieldsMixin().fields(ctx)) == ["pressure", "U"]  # the stats table: unchanged


def test_worker_switches_the_field_while_running():
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([HELPERS, os.environ.get("PYTHONPATH", "")])}
    proc = subprocess.Popen([sys.executable, "-m", "neofoam_ui.worker", "fake_view_toy:ViewHeat"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)

    def send(cmd):
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()

    try:
        seen = []
        for line in proc.stdout:
            seen.append(json.loads(line))
            if seen[-1]["type"] == "paused" and len(of(seen, "paused")) == 1:
                send({"cmd": "step", "mode": "skip_init"})
                send({"cmd": "view", "on": True, "field": "T", "component": "mag"})
                send({"cmd": "delay", "seconds": 0.02})
                send({"cmd": "step", "mode": "run"})
            elif seen[-1]["type"] == "frame" and len(of(seen, "frame")) == 3:
                send({"cmd": "view", "on": True, "field": "kappa", "component": "mag"})
            elif seen[-1]["type"] == "paused" and seen[-1]["finished"]:
                break
        frames = of(seen, "frame")
        switch = next(i for i, f in enumerate(frames) if f["field"] == "kappa")
        assert switch >= 3 and all(f["field"] == "kappa" for f in frames[switch:])
        # rendered by a live frame of the run, not first at the pause that ended it
        assert any(m["type"] == "log" for m in seen[seen.index(frames[switch]):])
        send({"cmd": "stop"})
        for line in proc.stdout:
            if json.loads(line)["type"] == "stopped":
                break
    finally:
        proc.kill()
        proc.wait()


def test_worker_reports_unconfigured_view():
    env = {k: v for k, v in os.environ.items() if k != catalyst.ENV}
    proc = subprocess.Popen([sys.executable, "-m", "neofoam_ui.worker", "neofoam_ui.toy.heat1d:Heat1D"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)

    def send(cmd):
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()

    def until(kind):
        seen = []
        for line in proc.stdout:
            seen.append(json.loads(line))
            if seen[-1]["type"] == kind:
                return seen
        raise AssertionError(seen)

    try:
        until("paused")
        send({"cmd": "view", "on": True, "field": None, "component": "mag"})
        send({"cmd": "step", "mode": "skip_init"})
        seen = until("paused")
        (frame,) = [m for m in seen if m["type"] == "frame"]
        assert frame["file"] is None and catalyst.ENV in frame["error"]
        send({"cmd": "stop"})
        until("stopped")
    finally:
        proc.kill()
        proc.wait()


# -- geometry: polyMesh files → polyhedra ------------------------------------------------------

_HEADER = """FoamFile
{{
    version     2.0;
    format      {fmt};
    arch        "LSB;label=32;scalar=64";
    class       {cls};
    object      {obj};
}}
// * * //
"""


def two_cells():
    """2 x 1 hex cells, one cell thick in z (a 2D case)."""
    pts = [(x, y, z) for z in (0.0, 0.1) for y in (0.0, 1.0) for x in (0.0, 1.0, 2.0)]
    p = lambda x, y, z: pts.index((x, y, z))  # noqa: E731
    faces = [
        [p(1, 0, 0), p(1, 1, 0), p(1, 1, .1), p(1, 0, .1)],  # internal x = 1
        [p(0, 0, 0), p(0, 0, .1), p(0, 1, .1), p(0, 1, 0)],  # left
        [p(2, 0, 0), p(2, 1, 0), p(2, 1, .1), p(2, 0, .1)],  # right
        [p(0, 0, 0), p(1, 0, 0), p(1, 0, .1), p(0, 0, .1)],  # bottom
        [p(1, 0, 0), p(2, 0, 0), p(2, 0, .1), p(1, 0, .1)],
        [p(0, 1, 0), p(0, 1, .1), p(1, 1, .1), p(1, 1, 0)],  # top
        [p(1, 1, 0), p(1, 1, .1), p(2, 1, .1), p(2, 1, 0)],
        [p(0, 0, 0), p(0, 1, 0), p(1, 1, 0), p(1, 0, 0)],  # back z = 0 (cell 0)
        [p(1, 0, 0), p(1, 1, 0), p(2, 1, 0), p(2, 0, 0)],  # back z = 0 (cell 1)
        [p(0, 0, .1), p(1, 0, .1), p(1, 1, .1), p(0, 1, .1)],  # front z = 0.1
        [p(1, 0, .1), p(2, 0, .1), p(2, 1, .1), p(1, 1, .1)],
    ]
    owner = [0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
    # list the cell-1 back face first: the polygons must still come out in cell order
    faces[7], faces[8] = faces[8], faces[7]
    owner[7], owner[8] = 1, 0
    return np.array(pts), faces, owner, [1]


def write_ascii(d, gz=False):
    pts, faces, owner, neighbour = two_cells()
    files = {
        "points": ("vectorField", f"{len(pts)}\n(\n" + "\n".join(f"({x} {y} {z})" for x, y, z in pts) + "\n)\n"),
        "faces": ("faceList", f"{len(faces)}\n(\n" + "\n".join(f"{len(f)}({' '.join(map(str, f))})"
                                                             for f in faces) + "\n)\n"),
        "owner": ("labelList", f"{len(owner)}\n(\n" + "\n".join(map(str, owner)) + "\n)\n"),
        "neighbour": ("labelList", f"{len(neighbour)}\n(\n" + "\n".join(map(str, neighbour)) + "\n)\n"),
    }
    for name, (cls, body) in files.items():
        text = (_HEADER.format(fmt="ascii", cls=cls, obj=name) + body).encode()
        if gz:
            (d / f"{name}.gz").write_bytes(gzip.compress(text))
        else:
            (d / name).write_bytes(text)


def write_binary(d):
    pts, faces, owner, neighbour = two_cells()

    def blist(arr, dtype):
        a = np.asarray(arr, dtype=dtype)
        return f"{len(a) if a.ndim == 1 else a.shape[0]}\n(".encode() + a.tobytes() + b")\n"

    hdr = lambda cls, obj: _HEADER.format(fmt="binary", cls=cls, obj=obj).encode()  # noqa: E731
    (d / "points").write_bytes(hdr("vectorField", "points") + blist(pts, "<f8"))
    offsets = np.concatenate([[0], np.cumsum([len(f) for f in faces])])
    (d / "faces").write_bytes(hdr("faceCompactList", "faces") + blist(offsets, "<i4")
                              + blist(np.concatenate(faces), "<i4"))
    (d / "owner").write_bytes(hdr("labelList", "owner") + blist(owner, "<i4"))
    (d / "neighbour").write_bytes(hdr("labelList", "neighbour") + blist(neighbour, "<i4"))


@pytest.mark.parametrize("fmt", ["ascii", "gz", "binary"])
def test_read_polymesh_and_polyhedra(tmp_path, fmt):
    if fmt == "binary":
        write_binary(tmp_path)
    else:
        write_ascii(tmp_path, gz=fmt == "gz")
    mesh = read_polymesh(str(tmp_path))
    assert mesh["points"].shape == (12, 3) and len(mesh["offsets"]) == 12 and list(mesh["neighbour"]) == [1]
    geom = polyhedral(mesh, 2)
    assert geom["kind"] == "polyhedral" and list(geom["cell_offsets"]) == [0, 6, 12]
    for cell, centre in ((0, [0.5, 0.5, 0.05]), (1, [1.5, 0.5, 0.05])):
        faces = geom["cell_faces"][geom["cell_offsets"][cell]:geom["cell_offsets"][cell + 1]]
        assert 0 in faces  # the internal face belongs to both cells
        pts = np.concatenate([mesh["conn"][mesh["offsets"][f]:mesh["offsets"][f + 1]] for f in faces])
        assert np.allclose(mesh["points"][np.unique(pts)].mean(axis=0), centre)


def test_polyhedral_rejects_a_cell_count_mismatch(tmp_path):
    write_ascii(tmp_path)
    with pytest.raises(ValueError):
        polyhedral(read_polymesh(str(tmp_path)), 3)


def test_cell_centres_geometry():
    c = np.array([[0.0, 5.0], [1.0, 5.0]])
    g = cell_centres_geometry(c)
    assert g["kind"] == "points" and g["points"].shape == (2, 3)
    assert cell_centres_geometry(np.array([0.0, 1.0]))["kind"] == "line"


# -- the real thing: conduit Blueprint + ParaView through Catalyst 2 ---------------------------

PREFIX = os.environ.get(catalyst.ENV)
needs_catalyst = pytest.mark.skipif(not PREFIX, reason=f"set {catalyst.ENV} to a Catalyst + ParaView install")


@needs_catalyst
def test_blueprint_of_the_polymesh_verifies(tmp_path):
    _, conduit = catalyst._import(PREFIX)
    from catalyst_conduit import blueprint  # noqa: F401  (registers conduit.blueprint)

    write_ascii(tmp_path)
    geom = polyhedral(read_polymesh(str(tmp_path)), 2)
    node, keep = conduit.Node(), []
    catalyst.blueprint(node, geom, {"p": np.array([1.0, 2.0]), "U": np.ones((2, 3))}, keep)
    info = conduit.Node()
    assert conduit.blueprint.mesh.verify(node, info), info


@needs_catalyst
@pytest.mark.parametrize("kind", ["polyhedral", "line"])
def test_paraview_renders_a_frame(tmp_path, kind):
    """Runs in a fresh interpreter: Catalyst stays initialised for the life of a process."""
    write_ascii(tmp_path)
    code = f"""
import numpy as np
from neofoam_ui import catalyst
from neofoam_ui.geometry import polyhedral, read_polymesh
if {kind!r} == "line":
    geom, fields = {{"kind": "line", "x": np.linspace(0, 1, 10)}}, {{"T": np.arange(10.0)}}
else:
    geom, fields = polyhedral(read_polymesh({str(tmp_path)!r}), 2), {{"p": np.array([1.0, 2.0])}}
insitu = catalyst.InSitu({PREFIX!r}, {str(tmp_path / "out")!r})
print("FILE", insitu.execute(geom, fields, 0.5, {{"field": next(iter(fields)), "component": "mag"}}))
insitu.finalize()
"""
    python = os.environ.get("NEOFOAM_PYTHON", sys.executable)
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    res = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=300,
                         env={**os.environ, "PYTHONPATH": src})
    assert res.returncode == 0, res.stderr[-3000:]
    name = res.stdout.split("FILE ")[1].split()[0]
    png = tmp_path / "out" / name
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@needs_catalyst
def test_paraview_colours_by_the_requested_field(tmp_path):
    """The pipeline reads the ``field`` parameter: p and its mirror image render differently."""
    write_ascii(tmp_path)
    code = f"""
import numpy as np
from neofoam_ui import catalyst
from neofoam_ui.geometry import polyhedral, read_polymesh
geom = polyhedral(read_polymesh({str(tmp_path)!r}), 2)
fields = {{"p": np.array([1.0, 2.0]), "q": np.array([2.0, 1.0])}}
insitu = catalyst.InSitu({PREFIX!r}, {str(tmp_path / "out")!r})
for name in ("p", "q"):
    print("FILE", insitu.execute(geom, fields, 0.5, {{"field": name, "component": "mag"}}))
insitu.finalize()
"""
    python = os.environ.get("NEOFOAM_PYTHON", sys.executable)
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    res = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=300,
                         env={**os.environ, "PYTHONPATH": src})
    assert res.returncode == 0, res.stderr[-3000:]
    p, q = [(tmp_path / "out" / part.split()[0]).read_bytes() for part in res.stdout.split("FILE ")[1:]]
    assert p != q  # an ignored parameter colours both by the default field


def test_live_frames_at_every_time_step_while_running():
    session, messages = session_after_init()
    session.set_view("T")
    n = len(of(messages, "frame"))
    session.breakpoints = set()
    session.advance("run")
    assert session.finished
    steps = sum(1 for m in messages if m["type"] == "log" and m["kind"] == "test" and not m["stack"])
    assert len(of(messages, "frame")) - n >= steps - 1 > 1


def test_live_frames_can_be_turned_off():
    session, messages = session_after_init()
    session.set_view("T")
    session.live_view = False
    n = len(of(messages, "frame"))
    session.advance("run")
    assert session.finished and len(of(messages, "frame")) == n + 1  # the final pause only


@needs_catalyst
@pytest.mark.parametrize("script", ["default", "other"])
def test_paraview_pipeline_is_described(tmp_path, script):
    """Rank 0's read-back of the live pipeline: nodes, edges, settings, per-node times."""
    other = os.path.join(HELPERS, "catalyst_threshold.py")
    code = f"""
import json
import numpy as np
from neofoam_ui import catalyst
geom = {{"kind": "line", "x": np.linspace(0, 1, 10)}}
fields = {{"p": np.arange(10.0), "U": np.ones((10, 3))}}
insitu = catalyst.InSitu({PREFIX!r}, {str(tmp_path / "out")!r}, script={other if script == "other" else None!r})
for comp in ("mag", "1"):
    insitu.execute(geom, fields, 0.5, {{"field": "U", "component": comp}})
    print("DESC", json.dumps(insitu.describe()))
insitu.finalize()
"""
    python = os.environ.get("NEOFOAM_PYTHON", sys.executable)
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    res = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=300,
                         env={**os.environ, "PYTHONPATH": src})
    assert res.returncode == 0, res.stderr[-3000:]
    first, second = [json.loads(part.splitlines()[0]) for part in res.stdout.split("DESC ")[1:]]
    nodes = {n["name"]: n for n in second["nodes"]}
    ids = {n["id"]: n["name"] for n in second["nodes"]}
    edges = {(ids[e["source"]], ids[e["target"]]) for e in second["edges"]}
    view = next(n["name"] for n in second["nodes"] if n["kind"] == "view")
    display = next(n for n in second["nodes"] if n["kind"] == "display")
    if script == "default":
        assert second["script"] == "view.py"
        assert [nodes[n]["kind"] for n in ("grid", "surface", "png")] == ["source", "filter", "extractor"]
        assert {("grid", "surface"), ("surface", display["name"]), (display["name"], view),
                (view, "png")} == edges
        assert dict(first["nodes"][2]["settings"])["colour"] == "|U| (cells)"
        assert dict(display["settings"])["colour"] == "U[1] (cells)"
        assert dict(display["settings"])["range"] == "1 … 1"
        assert dict(nodes["png"]["settings"])["file"] == "view_{timestep:06d}.png"
    else:
        assert second["script"] == "catalyst_threshold.py"
        assert dict(nodes["hot"]["settings"])["Scalars"] == "[CELLS, p]"
        assert dict(nodes["data"]["settings"]) == {"file": "grid_{timestep:06d}.vtpd", "every": "5 step(s)"}
        assert {("grid", "hot"), ("grid", "data"), (view, "image")} <= edges
        assert dict(display["settings"])["colour"] == "solid"
    # times: the whole execute always; per node from the second execute on (observers attached)
    assert "total" in first["ms"] and set(first["ms"]) == {"total"}
    by_name = {ids[k]: v for k, v in second["ms"].items() if k in ids}
    assert {"grid", view} <= set(by_name) and all(v >= 0 for v in by_name.values())
