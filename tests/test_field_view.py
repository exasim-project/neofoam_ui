import base64
import gzip
import json
import subprocess
import sys

import numpy as np
import pytest

from neofoam_ui.engine import Session
from neofoam_ui.geometry import (
    cell_centres_geometry,
    encode_geometry,
    front_polygons,
    read_polymesh,
)
from neofoam_ui.toy.heat1d import Heat1D


def decode(b64, dtype="<f4"):
    return np.frombuffer(base64.b64decode(b64), dtype=dtype)


# -- engine: the view protocol ------------------------------------------------------------


def session_after_init():
    messages = []
    session = Session(Heat1D(), messages.append)
    session.start()
    session.advance("skip_init")
    return session, messages


def of(messages, kind):
    return [m for m in messages if m["type"] == kind]


def test_closed_view_sends_nothing():
    session, messages = session_after_init()
    session.advance("time_step")
    assert not of(messages, "mesh") and not of(messages, "field")


def test_view_before_context_waits_for_initialization():
    messages = []
    session = Session(Heat1D(), messages.append)
    session.start()
    session.set_view("T")
    assert not of(messages, "field")  # no ctx during init
    session.advance("skip_init")
    assert len(of(messages, "mesh")) == 1 and len(of(messages, "field")) == 1


def test_mesh_once_then_field_values_when_changed():
    session, messages = session_after_init()
    session.set_view("T")
    (mesh,) = of(messages, "mesh")
    assert mesh["kind"] == "line" and mesh["n"] == 40
    assert np.allclose(decode(mesh["x"]), session.ctx.x)
    (frame,) = of(messages, "field")
    assert frame["field"] == "T" and frame["label"] == "T"
    assert np.allclose(decode(frame["values"]), session.ctx.fields["T"])
    # only cell-sized fields are offered (flux lives on the 41 faces)
    assert {f["name"] for f in frame["fields"]} == {"T", "T_old", "kappa", "source_mask"}

    session.advance("into")  # set_time_step: T unchanged -> no new frame
    assert len(of(messages, "field")) == 1
    session.advance("time_step")  # T changed
    frames = of(messages, "field")
    assert len(frames) == 2 and np.allclose(decode(frames[-1]["values"]), session.ctx.fields["T"])
    assert len(of(messages, "mesh")) == 1
    # each frame precedes the pause it belongs to
    assert messages[-1]["type"] == "paused" and messages[-2] is frames[-1]


def test_switching_field_and_closing():
    session, messages = session_after_init()
    session.set_view("T")
    session.set_view("kappa")
    assert of(messages, "field")[-1]["field"] == "kappa"
    session.set_view(None, on=False)
    n = len(of(messages, "field"))
    session.advance("time_step")
    assert len(of(messages, "field")) == n


def test_unknown_field_falls_back_to_a_default():
    session, messages = session_after_init()
    session.set_view("flux")  # not cell-sized
    assert of(messages, "field")[-1]["field"] == "T"


class Vec(Heat1D):
    """Heat1D with a vector field and a 2D point geometry."""

    def fields(self, ctx):
        n = len(ctx.x)
        return {**ctx.fields, "U": np.stack([ctx.fields["T"], np.ones(n), np.zeros(n)], axis=1)}

    def mesh(self, ctx):
        return cell_centres_geometry(np.stack([ctx.x, 0.1 * ctx.x, np.zeros_like(ctx.x)], axis=1))


def test_vector_components_and_magnitude():
    messages = []
    session = Session(Vec(), messages.append)
    session.start()
    session.advance("skip_init")
    session.set_view("U", "mag")
    frame = of(messages, "field")[-1]
    u = Vec().fields(session.ctx)["U"]
    assert frame["label"] == "|U|" and np.allclose(decode(frame["values"]), np.linalg.norm(u, axis=1))
    session.set_view("U", "1")
    frame = of(messages, "field")[-1]
    assert frame["label"] == "U.y" and np.allclose(decode(frame["values"]), 1.0)
    assert {"name": "U", "ncomp": 3} in frame["fields"]
    mesh = of(messages, "mesh")[0]
    assert mesh["kind"] == "points" and mesh["axes"] == ["x", "y"]


class NoMesh(Heat1D):
    mesh = None


def test_solver_without_geometry_reports_it_once():
    messages = []
    session = Session(NoMesh(), messages.append)
    session.start()
    session.advance("skip_init")
    session.set_view("T")
    session.advance("time_step")
    frames = of(messages, "field")
    assert len(frames) == 1 and frames[0]["field"] is None and "no mesh" in frames[0]["error"]


def test_worker_view_command():
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
        assert [m["type"] for m in seen if m["type"] in ("mesh", "field")] == ["mesh", "field"]
        assert seen[-2]["field"] == "T"
        send({"cmd": "stop"})
        until("stopped")
    finally:
        proc.kill()
        proc.wait()


# -- geometry: polyMesh files → polygons ------------------------------------------------------

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
def test_read_polymesh_and_front_polygons(tmp_path, fmt):
    if fmt == "binary":
        write_binary(tmp_path)
    else:
        write_ascii(tmp_path, gz=fmt == "gz")
    mesh = read_polymesh(str(tmp_path))
    assert mesh["points"].shape == (12, 3) and len(mesh["offsets"]) == 12 and int(mesh["n_internal"]) == 1
    geom = front_polygons(mesh, 2)
    assert geom is not None and geom["kind"] == "polygons" and geom["axes"] == ["x", "y"]
    cells = [geom["points"][geom["conn"][geom["offsets"][i]:geom["offsets"][i + 1]]] for i in range(2)]
    assert np.allclose(cells[0].mean(axis=0), [0.5, 0.5])  # cell order, not face order
    assert np.allclose(cells[1].mean(axis=0), [1.5, 0.5])
    enc = encode_geometry(geom)
    assert enc["n"] == 2 and enc["bounds"] == [0.0, 2.0, 0.0, 1.0]
    assert np.array_equal(decode(enc["offsets"], "<i4"), [0, 4, 8])


def test_front_polygons_rejects_3d(tmp_path):
    write_ascii(tmp_path)
    mesh = read_polymesh(str(tmp_path))
    mesh["points"] = mesh["points"].copy()
    mesh["points"][0, 2] = 0.05  # no longer one cell thick
    assert front_polygons(mesh, 2) is None


def test_cell_centres_geometry_picks_widest_axes():
    c = np.array([[0.0, 5.0, 1.0], [1.0, 5.0, 3.0], [2.0, 5.0, 2.0]])
    g = cell_centres_geometry(c)
    assert g["axes"] == ["x", "z"] and np.allclose(g["points"], c[:, [0, 2]])
