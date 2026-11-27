"""In-situ field view: the view protocol (Catalyst faked), geometry, and — where a
Catalyst + ParaView install is configured — a real frame rendered through Catalyst 2."""

import base64
import gzip
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from neofoam_ui import catalyst
from neofoam_ui.adapters.neofoam._base import _NeoFoamBase as _FieldsMixin
from neofoam_ui.catalyst._blueprint import _blueprint
from neofoam_ui.catalyst._native import _import
from neofoam_ui.config import CatalystConfig
from neofoam_ui.engine import Session
from neofoam_ui.geometry import cell_centres_geometry, cell_count, merged, polyhedral, read_polymesh
from neofoam_ui.toy.heat1d import Heat1D

HELPERS = os.path.join(os.path.dirname(__file__), "helpers")
SCENES = Path(__file__).parent / "scenes" / "cases" / "builder" / "system" / "scenes"

# -- engine: the view protocol ------------------------------------------------------------


class FakeInSitu:
    calls: list = []
    out_dirs: list = []
    ms: dict = {}

    def __init__(self, out_dir=None):
        self.step = 0
        FakeInSitu.out_dirs.append(out_dir)

    def execute(self, geom, fields, time, params):
        FakeInSitu.calls.append({"geom": geom, "fields": fields, "time": time, "params": params})
        self.step += 1
        return None if params["mode"] == "data" else f"png{self.step - 1}".encode()

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
def fake_catalyst(monkeypatch):
    FakeInSitu.calls = []
    FakeInSitu.out_dirs = []
    FakeInSitu.ms = {}
    FakeInSitu.pipeline = json.loads(json.dumps(PIPELINE))
    catalyst.use(CatalystConfig(prefix="/nowhere"))
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
    assert frame["field"] == "T" and base64.b64decode(frame["image"]) == b"png0"
    call = renders(fake_catalyst)[0]
    assert call["params"] == {"field": "T", "component": "mag", "mode": "image"}
    assert call["geom"]["kind"] == "line"
    # the default pipeline gets the viewed field only; the view offers every cell-sized one
    # (flux lives on the 41 faces)
    assert set(call["fields"]) == {"T"}
    assert {f["name"] for f in frame["fields"]} == {"T", "T_old", "kappa", "source_mask"}

    session.advance("into")  # set_time_step: T unchanged -> no new frame
    assert len(of(messages, "frame")) == 1
    session.advance("time_step")  # T changed
    frames = of(messages, "frame")
    assert len(frames) == 2 and base64.b64decode(frames[-1]["image"]) == b"png1"
    # each frame precedes the pause it belongs to
    assert messages[-1]["type"] == "paused" and messages[-2] is frames[-1]


def test_another_pipeline_script_gets_every_field(fake_catalyst):
    catalyst.use(CatalystConfig(prefix="/nowhere", script="/my/pipeline.py"))
    session, _ = session_after_init()
    session.set_view("T")
    assert set(renders(fake_catalyst)[0]["fields"]) == {"T", "T_old", "kappa", "source_mask"}


@pytest.mark.parametrize("script, out_dir", [(None, None), ("/my/pipeline.py", "postProcessing/catalyst")])
def test_only_another_pipeline_script_gets_an_output_directory(fake_catalyst, script, out_dir):
    """The stepper's own pipeline writes nothing; another script's extracts go into the case."""
    catalyst.use(CatalystConfig(prefix="/nowhere", script=script))
    session, _ = session_after_init()
    session.set_view("T")
    assert FakeInSitu.out_dirs == [out_dir]


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
    assert renders(fake_catalyst)[-1]["params"] == {"field": "U", "component": "1", "mode": "image"}
    session.set_view("U", "7")  # no such component
    assert of(messages, "frame")[-1]["component"] == "mag"
    assert {"name": "U", "ncomp": 3} in of(messages, "frame")[-1]["fields"]
    assert renders(fake_catalyst)[-1]["geom"]["kind"] == "points"


# -- frames the solver's in-situ operation asks for -----------------------------------------


def test_solver_frame_draws_the_requested_field_while_the_view_is_closed(fake_catalyst):
    session, messages = session_after_init()
    session.solver_frame({"field": "kappa", "component": "mag"})
    assert of(messages, "frame")[-1]["field"] == "kappa"


def test_solver_frame_draws_the_field_picked_in_the_ui(fake_catalyst):
    session, _ = session_after_init()
    session.request_view("T")
    session.solver_frame({"field": "kappa", "component": "mag"})
    assert renders(fake_catalyst)[-1]["params"] == {"field": "T", "component": "mag", "mode": "image"}


def test_solver_frame_without_a_field_draws_a_default(fake_catalyst):
    session, messages = session_after_init()
    session.solver_frame({"field": "", "component": "mag"})
    assert of(messages, "frame")[-1]["field"] == "T"


def test_a_pause_after_a_solver_frame_of_the_same_values_renders_nothing(fake_catalyst):
    session, _ = session_after_init()
    session.request_view("T")
    session.solver_frame({"field": "T", "component": "mag"})
    session.send_view()
    assert len(renders(fake_catalyst)) == 1


def test_a_solver_frame_ends_the_ui_live_frames(fake_catalyst):
    session, _ = session_after_init()
    session.set_view("T")
    session.frame_seconds = 0
    session.solver_frame({"field": "T", "component": "mag"})
    n = len(renders(fake_catalyst))
    session.advance("run")
    assert session.finished and len(renders(fake_catalyst)) == n + 1  # the final pause only


def test_solver_frame_under_mpi_draws_the_view_rank_0_shared_at_its_last_go(fake_catalyst):
    session, _ = session_after_init()
    sent = []
    session.set_peers(SimpleNamespace(go=lambda track, view: sent.append(view), render=lambda params: None,
                                      free=lambda params: None, stats=list))
    session.request_view("T")
    session.advance("into")
    session.request_view("kappa")  # picked after the go: from the next go on
    session.solver_frame({"field": "U", "component": "mag"})
    assert sent[-1] == ("T", "mag", "image", {})
    assert renders(fake_catalyst)[-1]["params"] == {"field": "T", "component": "mag", "mode": "image"}


def test_solver_frame_under_mpi_keeps_to_the_requested_field_while_no_view_was_shared(fake_catalyst):
    session, _ = session_after_init()
    session.set_peers(SimpleNamespace(go=lambda track, view: None, render=lambda params: None))
    session.request_view("T")
    session.solver_frame({"field": "kappa", "component": "mag"})
    assert renders(fake_catalyst)[-1]["params"] == {"field": "kappa", "component": "mag", "mode": "image"}


@pytest.mark.parametrize("mode", ["image", "data"])
def test_a_follower_draws_the_view_rank_0_shared(fake_catalyst, mode):
    session, _ = session_after_init()
    session.follower = True
    session.share_view(["T", "mag", mode, {}])
    session.solver_frame({"field": "kappa", "component": "mag"})
    assert renders(fake_catalyst)[-1]["params"] == {"field": "T", "component": "mag", "mode": mode}


# -- data mode: Catalyst Live carries the data, the frame no image -----------------------------


def test_data_mode_frames_carry_no_image(fake_catalyst):
    session, messages = session_after_init()
    session.set_view("T", mode="data")
    frame = of(messages, "frame")[-1]
    assert (frame["mode"], frame["image"], frame["view"]) == ("data", None, ["T", "mag", "data"])
    assert renders(fake_catalyst)[-1]["params"] == {"field": "T", "component": "mag", "mode": "data"}


def test_switching_the_mode_renders_the_same_values_again(fake_catalyst):
    session, _ = session_after_init()
    session.set_view("T")
    session.set_view("T", mode="data")
    assert [r["params"]["mode"] for r in renders(fake_catalyst)] == ["image", "data"]


SLICE = {"scene": "slice", "graph": (SCENES / "slice.json").read_text()}


def test_another_scene_renders_unchanged_values_again(fake_catalyst):
    session, _ = session_after_init()
    session.set_view_options({"scene": "Field"})
    session.set_view("T")
    session.set_view_options(SLICE)
    session.set_view("T")
    assert [r["params"]["scene"] for r in renders(fake_catalyst)] == ["Field", "slice"]


def test_the_scene_reaches_the_followers_with_the_shared_view(fake_catalyst):
    session, _ = session_after_init()
    sent = []
    session.set_peers(SimpleNamespace(go=lambda track, view: sent.append(view), render=lambda params: None,
                                      free=lambda params: None, stats=list))
    session.set_view_options({**SLICE, "width": "640", "height": "360"})
    session.request_view("T")
    session.advance("into")
    assert sent[-1] == ("T", "mag", "image", {**SLICE, "width": "640", "height": "360"})


def test_the_built_in_scene_gets_the_viewed_field_only(fake_catalyst):
    session, _ = session_after_init()
    session.set_view_options({"scene": "Field"})
    session.set_view("T")
    assert list(renders(fake_catalyst)[-1]["fields"]) == ["T"]


def test_a_scene_of_the_case_gets_every_field(fake_catalyst):
    session, _ = session_after_init()
    session.set_view_options(SLICE)
    session.set_view("T")
    assert sorted(renders(fake_catalyst)[-1]["fields"]) == ["T", "T_old", "kappa", "source_mask"]


def test_a_failing_scene_sends_its_error_and_the_view_goes_on(fake_catalyst, monkeypatch):
    error = "scene 'slice': node 's1' (Slice): broken"
    monkeypatch.setattr(FakeInSitu, "report", {"error": error}, raising=False)
    session, messages = session_after_init()
    session.set_view_options(SLICE)
    session.set_view("T")
    assert (of(messages, "frame")[-1]["error"], of(messages, "frame")[-1]["scene"]) == (error, "slice")
    monkeypatch.setattr(FakeInSitu, "report", {}, raising=False)
    session.set_view_options({"scene": "Field"})
    session.set_view("T")
    assert of(messages, "frame")[-1]["image"] is not None


def test_the_renderers_report_goes_with_the_frame(fake_catalyst, monkeypatch):
    monkeypatch.setattr(FakeInSitu, "report", {"bounds": [0, 1, 0, 1, 0, 1], "axis": "z", "notes": []}, raising=False)
    session, messages = session_after_init()
    session.set_view("T")
    assert of(messages, "frame")[-1]["report"] == {"bounds": [0, 1, 0, 1, 0, 1], "axis": "z", "notes": []}


class NoMesh(Heat1D):
    mesh = None


def test_solver_without_geometry_reports_it_once():
    session, messages = session_after_init(NoMesh())
    session.set_view("T")
    session.advance("time_step")
    frames = of(messages, "frame")
    assert len(frames) == 1 and frames[0]["image"] is None and "no mesh" in frames[0]["error"]


def test_unconfigured_catalyst_says_so():
    catalyst.use(CatalystConfig())
    session, messages = session_after_init()
    session.set_view("T")
    (frame,) = of(messages, "frame")
    assert frame["image"] is None and "catalyst.prefix" in frame["error"]


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

    class Peers:
        def go(self, track, view):
            pass

        def free(self, params):
            pass

        def render(self, params):
            told.append(params)

        def stats(self):
            return []

        def stop(self):
            pass

    session.set_peers(Peers())
    session.set_view("kappa")
    assert told == [{"field": "kappa", "component": "mag", "mode": "image"}]
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
    session.frame_seconds = 0
    session.advance("run")
    frames = of(messages, "frame")
    assert [f["field"] for f in frames[:3]] == ["T", "T", "kappa"]
    assert all(f["field"] == "kappa" and f["view"] == ["kappa", "mag", "image"] for f in frames[2:])
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
    assert (frame["field"], frame["component"], frame["view"]) == ("T", "mag", ["flux", "2", "image"])


# -- post-processing diagram: the pipeline read back after each execute ------------------------


def test_pipeline_goes_with_the_frame_only_when_it_changes(fake_catalyst):
    session, messages = session_after_init(Extra())
    session.set_view("T")
    first = of(messages, "frame")[-1]
    assert first["pipeline"] == PIPELINE and "ms" not in first["pipeline"]
    assert first["pipeline_ms"] == {"total": 2.5, "pv1": 0.5}
    FakeInSitu.ms = {"total": 1.5}
    session.advance("time_step")  # same request: not read back, the timings of the execute
    frame = of(messages, "frame")[-1]
    assert "pipeline" not in frame and fake_catalyst.count("describe") == 1
    assert frame["pipeline_ms"] == {"total": 1.5}
    FakeInSitu.pipeline["nodes"][1]["settings"] = [["size", "640×480"]]  # e.g. a colour range moved
    session.set_view("k")  # another field: read back
    assert of(messages, "frame")[-1]["pipeline"]["nodes"][1]["settings"] == [["size", "640×480"]]
    FakeInSitu.pipeline["nodes"][1]["settings"] = [["size", "1×1"]]
    session._view.describe_every = 0.0  # and again after a while
    session.advance("time_step")
    assert of(messages, "frame")[-1]["pipeline"]["nodes"][1]["settings"] == [["size", "1×1"]]


def test_only_the_deciding_rank_reads_the_pipeline_back(fake_catalyst):
    session, _ = session_after_init()
    session.render({"field": "T", "component": "mag", "mode": "image"})  # how a follower renders
    assert "describe" not in fake_catalyst and session.pipeline is None


def test_a_failing_description_keeps_the_frames(monkeypatch):
    def boom(self):
        raise RuntimeError("no proxy manager")

    monkeypatch.setattr(FakeInSitu, "describe", boom)
    session, messages = session_after_init()
    session.set_view("T")
    session.advance("time_step")
    frames = of(messages, "frame")
    assert len(frames) == 2 and all(f["image"] and "pipeline" not in f for f in frames)
    reports = [m for m in of(messages, "stdout") if "no proxy manager" in m["text"]]
    assert len(reports) == 1  # once, then no more diagrams


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
    proc = subprocess.Popen([sys.executable, "-m", "neofoam_ui.worker", "neofoam_ui.toy.heat1d:Heat1D"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

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
        assert frame["image"] is None and "catalyst.prefix" in frame["error"]
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

PREFIX = os.environ.get("NEOFOAM_TEST_CATALYST")
needs_catalyst = pytest.mark.skipif(not PREFIX, reason="set NEOFOAM_TEST_CATALYST to a Catalyst + ParaView install")
#: The interpreter ParaView was built for.
PYTHON = os.environ.get("NEOFOAM_PYTHON", sys.executable)
#: The signature every PNG starts with.
PNG = b"\x89PNG\r\n\x1a\n"


def _catalyst_env():
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}  # headless (EGL)
    return {**env, "PYTHONPATH": os.path.join(os.path.dirname(__file__), "..", "src"), "HWLOC_COMPONENTS": "-gl"}


def _run_catalyst(code):
    """Runs in a fresh interpreter: Catalyst stays initialised for the life of a process."""
    res = subprocess.run([PYTHON, "-c", code], capture_output=True, text=True, timeout=300, env=_catalyst_env())
    assert res.returncode == 0, res.stderr[-3000:]
    return res.stdout


@needs_catalyst
def test_blueprint_of_the_polymesh_verifies(tmp_path):
    _, conduit = _import(PREFIX)
    # registers conduit.blueprint; on sys.path only once _import added Catalyst's site-packages
    from catalyst_conduit import blueprint  # noqa: F401, PLC0415

    write_ascii(tmp_path)
    geom = polyhedral(read_polymesh(str(tmp_path)), 2)
    node, keep = conduit.Node(), []
    _blueprint(node, geom, {"p": np.array([1.0, 2.0]), "U": np.ones((2, 3))}, keep)
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
insitu = catalyst.InSitu({PREFIX!r})
image = insitu.execute(geom, fields, 0.5, {{"field": next(iter(fields)), "component": "mag", "mode": "image"}})
print("IMAGE", image[:8].hex())
insitu.finalize()
"""
    res = _run_catalyst(code)
    assert res.split("IMAGE ")[1].split()[0] == PNG.hex()


@needs_catalyst
def test_paraview_data_mode_captures_no_image(tmp_path):
    """Data mode: Catalyst Live carries the data, the solver renders no picture."""
    code = f"""
import numpy as np
from neofoam_ui import catalyst
insitu = catalyst.InSitu({PREFIX!r})
geom, fields = {{"kind": "line", "x": np.linspace(0, 1, 10)}}, {{"T": np.arange(10.0)}}
print("IMAGE", insitu.execute(geom, fields, 0.5, {{"field": "T", "component": "mag", "mode": "data"}}))
insitu.finalize()
"""
    assert _run_catalyst(code).split("IMAGE ")[1].split()[0] == "None"


@needs_catalyst
def test_paraview_writes_the_images_of_an_output_directory(tmp_path):
    """A solver's own in-situ entry (no stepper) writes files and captures nothing."""
    code = f"""
import numpy as np
from neofoam_ui import catalyst
insitu = catalyst.InSitu({PREFIX!r}, {str(tmp_path / "out")!r}, capture=False)
geom, fields = {{"kind": "line", "x": np.linspace(0, 1, 10)}}, {{"T": np.arange(10.0)}}
print("IMAGE", insitu.execute(geom, fields, 0.5, {{"field": "T", "component": "mag"}}))
insitu.finalize()
"""
    assert _run_catalyst(code).split("IMAGE ")[1].split()[0] == "None"
    assert (tmp_path / "out" / "view_000000.png").read_bytes()[:8] == PNG


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
insitu = catalyst.InSitu({PREFIX!r})
for name in ("p", "q"):
    print("IMAGE", insitu.execute(geom, fields, 0.5, {{"field": name, "component": "mag", "mode": "image"}}).hex())
insitu.finalize()
"""
    p, q = [part.split()[0] for part in _run_catalyst(code).split("IMAGE ")[1:]]
    assert p != q  # an ignored parameter colours both by the default field


#: Scenes (files of ``tests/scenes/cases/options``; ``None``: the built-in ``Field``), image
#: parameters and what the image of four hexahedra along x (T = -1 … 2, Viridis: purple …
#: yellow) shows then; the camera looks along y by default. Today's field-view options, each
#: through a scene: zoom (View node), cut (Slice / Clip node), colours (Display node).
OPTIONS = [
    (None, {"width": "640", "height": "360"}, {"size": [640, 360], "purple": True, "yellow": True, "red": False,
                                               "corner_is_mesh": False}),
    ("zoom-6", {"width": "640", "height": "360"}, {"corner_is_mesh": True}),
    ("clip-x", {}, {"purple": True, "yellow": False}),
    ("clip-x-flip", {}, {"purple": False, "yellow": True}),
    ("slice-x-from-x", {}, {"purple": False, "yellow": True}),
    ("jet", {}, {"red": True}),
    ("range-5-6", {}, {"yellow": False}),  # every value below the range: the low end only
    ("log", {}, {"notes": ["log scale off: the colour range reaches 0 or below"]}),
    ("log-range", {}, {"notes": []}),
    (None, {}, {"bounds": [0.0, 4.0, 0.0, 1.0, 0.0, 1.0], "axis": "z"}),
]
OPTION_SCENES = Path(__file__).parent / "scenes" / "cases" / "options" / "system" / "scenes"


@needs_catalyst
@pytest.mark.parametrize("scene, params, shows", OPTIONS)
def test_paraview_draws_the_scene(scene, params, shows):
    files = [str(OPTION_SCENES / f"{scene}.json")] if scene else []
    res = subprocess.run([PYTHON, os.path.join(HELPERS, "catalyst_options.py"), PREFIX, json.dumps(params), *files],
                         capture_output=True, text=True, timeout=300, env=_catalyst_env())
    assert res.returncode == 0, res.stderr[-3000:]
    seen = json.loads(next(line for line in res.stdout.splitlines() if line.startswith("{")))
    assert {k: seen[k] for k in shows} == shows, scene


@needs_catalyst
def test_paraview_frames_on_alternating_threads_are_drawn():
    """A multi-region solver renders on whichever region thread is held: each frame must make
    ParaView's one EGL context current on its own thread (a frame came out black before)."""
    res = subprocess.run([PYTHON, os.path.join(HELPERS, "catalyst_two_threads.py"), PREFIX],
                         capture_output=True, text=True, timeout=300, env=_catalyst_env())
    assert res.returncode == 0, res.stderr[-3000:]
    frames = [json.loads(line) for line in res.stdout.splitlines() if line.startswith("{")]
    assert frames == [{"frame": i, "thread": f"region{i % 2}", "purple": True, "yellow": True} for i in range(4)]


@needs_catalyst
@pytest.mark.skipif(not shutil.which("mpirun"), reason="needs mpirun")
def test_paraview_image_under_mpi_is_the_whole_domain_on_rank_0():
    """mpirun -np 2: each rank publishes four hexahedra of its own value (0 or 1); rank 0's
    image shows both colour ends (purple and yellow, IceT composited), rank 1 gets none."""
    res = subprocess.run(["mpirun", "-np", "2", PYTHON, os.path.join(HELPERS, "catalyst_two_ranks.py"), PREFIX],
                         capture_output=True, text=True, timeout=300, env=_catalyst_env())
    assert res.returncode == 0, res.stderr[-3000:]
    ranks = sorted((json.loads(line) for line in res.stdout.splitlines() if line.startswith("{")),
                   key=lambda r: r["rank"])
    assert ranks == [{"rank": 0, "png": True, "purple": True, "yellow": True},
                     {"rank": 1, "png": False, "purple": False, "yellow": False}]


def test_live_frames_at_every_time_step_while_running():
    session, messages = session_after_init()
    session.set_view("T")
    session.frame_seconds = 0
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
        assert [nodes[n]["kind"] for n in ("grid", "Field:surface", "png")] == ["source", "filter", "extractor"]
        assert view == "Field:view"
        assert {("grid", "Field:surface"), ("Field:surface", display["name"]), (display["name"], view),
                (view, "png")} == edges
        first_display = next(n for n in first["nodes"] if n["kind"] == "display")
        assert dict(first_display["settings"])["colour"] == "|U| (cells)"
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


# -- extension points: regions and the render backend --------------------------------------------


class TwoRegions(Heat1D):
    """A multi-region solver (e.g. CHT) without cells to merge (lines): the view shows the first region."""

    def __init__(self):
        super().__init__()
        self.meshed = []

    def regions(self, ctx):
        solver = self

        class Region:
            def __init__(self, name, scale):
                self.name, self.scale = name, scale

            def mesh(self, ctx):
                solver.meshed.append(self.name)
                return {"kind": "line", "x": ctx.x * self.scale}

            def fields(self, ctx):
                return {f"T_{self.name}": ctx.fields["T"] * self.scale}

        return [Region("fluid", 1.0), Region("solid", 2.0)]


def test_a_region_provider_is_viewed_by_its_first_region(fake_catalyst):
    session, messages = session_after_init(TwoRegions())
    session.set_view(None)
    frame = of(messages, "frame")[-1]
    assert frame["field"] == "T_fluid" and [f["name"] for f in frame["fields"]] == ["T_fluid"]
    session.advance("time_step")
    assert session.solver.meshed == ["fluid", "solid"]  # the geometry is built once per region


def test_a_render_backend_can_replace_catalyst():
    class Backend:
        def __init__(self):
            self.rendered = []

        def unavailable(self):
            return None

        def publishes_all(self):
            return True

        def create(self):
            backend = self

            class Renderer:
                def execute(self, geom, fields, time, params):
                    backend.rendered.append(sorted(fields))
                    return b"frame"

                def finalize(self):
                    backend.rendered.append("finalized")

            return Renderer()

    backend = Backend()
    messages = []
    session = Session(Heat1D(), messages.append, renderer=backend)
    session.start()
    session.advance("skip_init")
    session.set_view("T")
    session.finalize_view()
    assert base64.b64decode(of(messages, "frame")[-1]["image"]) == b"frame"
    assert backend.rendered == [["T", "T_old", "kappa", "source_mask"], "finalized"]


def hexes(n, x0=0.0):
    """``n`` unit hexahedra along x from ``x0`` (each with its own points and faces)."""
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    cube = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                     (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], float)
    points = np.concatenate([cube + (x0 + i, 0, 0) for i in range(n)])
    conn = np.array([8 * i + v for i in range(n) for q in quads for v in q])
    return {"kind": "polyhedral", "points": points, "offsets": np.arange(0, 24 * n + 1, 4),
            "conn": conn, "cell_offsets": np.arange(0, 6 * n + 1, 6), "cell_faces": np.arange(6 * n)}


def test_merged_shifts_points_faces_and_cells():
    a, b = hexes(2), hexes(3, x0=5.0)
    m = merged([a, b])
    assert cell_count(m) == 5 and len(m["points"]) == 40 and len(m["offsets"]) == 31
    last = m["cell_faces"][m["cell_offsets"][4]:m["cell_offsets"][5]]  # the last cell of b
    face = m["conn"][m["offsets"][last[0]]:m["offsets"][last[0] + 1]]
    assert m["points"][face].min(axis=0).tolist() == [7.0, 0.0, 0.0]
    assert merged([a, {"kind": "line", "x": np.zeros(2)}]) is None


class TwoMeshedRegions(Heat1D):
    """A CHT-like solver whose regions have polyhedral cells: the view draws both together."""

    def regions(self, ctx):
        n = len(ctx.x)

        class Region:
            def __init__(self, name, cells, fields):
                self.name, self._cells, self._fields = name, cells, fields

            def mesh(self, ctx):
                return hexes(self._cells, x0=0.0 if self.name == "fluid" else 100.0)

            def fields(self, ctx):
                return self._fields(ctx)

        return [Region("fluid", n, lambda c: {"T": c.fields["T"], "U": np.ones((n, 3))}),
                Region("solid", 2, lambda c: {"T": np.full(2, 7.0)})]


def test_regions_with_cells_are_merged_into_one_view(fake_catalyst):
    session, messages = session_after_init(TwoMeshedRegions())
    session.set_view("T")
    n = len(session.ctx.x)
    frame = of(messages, "frame")[-1]
    assert frame["field"] == "T"
    assert {f["name"] for f in frame["fields"]} == {"T", "U", "region"}
    call = renders(fake_catalyst)[-1]
    assert len(call["geom"]["cell_offsets"]) - 1 == n + 2
    assert call["fields"]["T"][-2:].tolist() == [7.0, 7.0]
    session.set_view("U")
    U = renders(fake_catalyst)[-1]["fields"]["U"]
    assert U.shape == (n + 2, 3) and np.isnan(U[-2:]).all() and (U[:n] == 1).all()
