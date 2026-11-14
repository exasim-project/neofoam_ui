"""The solver-side scene builder (``pipelines/view.py``) in a real ParaView, through Catalyst 2:
a changed graph keeps the proxies of unchanged nodes, switching keeps every scene built and runs
only the shown one, every node type builds, a bad scene fails alone, under ``mpirun -np 2``
every rank builds the same scene, and an exported
scene runs on its own (through Catalyst on a written case, and in pvbatch). A View with a layout
draws each cell's Displays in a render view of its own, captured as one picture of the requested
size; a new layout keeps the filters; cameras are linked or each cell's own; under MPI each cell
is composited over the ranks.

Each case runs ``helpers/catalyst_scenes.py`` in a fresh interpreter (Catalyst stays
initialised for the life of a process) on the scene files of ``tests/scenes/cases/builder``.
Opt-in: needs ``NEOFOAM_TEST_CATALYST`` and the interpreter ParaView was built for
(``NEOFOAM_PYTHON``)."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PREFIX = os.environ.get("NEOFOAM_TEST_CATALYST")
PYTHON = os.environ.get("NEOFOAM_PYTHON", sys.executable)
pytestmark = pytest.mark.skipif(not PREFIX, reason="set NEOFOAM_TEST_CATALYST to a Catalyst + ParaView install")
HERE = Path(__file__).resolve().parent
HELPER = str(HERE / "helpers" / "catalyst_scenes.py")
EXPORT = HERE / "helpers" / "catalyst_export.py"
SCENES = str(HERE / "scenes" / "cases" / "builder" / "system" / "scenes")


def _env():
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}  # headless (EGL)
    return {**env, "PYTHONPATH": str(HERE.parent / "src"), "HWLOC_COMPONENTS": "-gl"}


def _frames(steps, launch=(PYTHON,), out=None):
    """The helper's frames; ``out``: the pipeline also writes its pictures there (``out=``)."""
    env = {**_env(), "OUT": str(out)} if out else _env()
    res = subprocess.run([*launch, HELPER, PREFIX, json.dumps(steps), SCENES], capture_output=True, text=True,
                         timeout=300, env=env)
    assert res.returncode == 0, res.stderr[-3000:]
    return [json.loads(line) for line in res.stdout.splitlines() if line.startswith("{")]


def test_a_changed_graph_keeps_the_proxies_of_unchanged_nodes():
    """slice → its slice moved (a property) → a contour added → the contour removed."""
    frames = _frames(["slice", "slice=slice-moved", "slice=slice-contour", "slice"])
    kept = [{k: f["ids"][k] for k in ("grid", "s1", "d1", "view")} for f in frames]
    assert all(k == kept[0] for k in kept)
    assert set(frames[2]["ids"]) - set(frames[0]["ids"]) == {"c1", "d2"} and set(frames[3]["ids"]) == set(kept[0])
    # x = 3.6 shows T = 2 (yellow), then x = 0.4 T = -1 (purple); the red contour line in front
    assert [f["colours"] for f in frames] == [{"purple": False, "yellow": True, "red": False},
                                              {"purple": True, "yellow": False, "red": False},
                                              {"purple": False, "yellow": False, "red": True},
                                              {"purple": False, "yellow": True, "red": False}]


def test_switching_keeps_every_scene_built_and_runs_only_the_shown_one():
    frames = _frames(["slice", "Field", "threshold", "slice"])
    assert [f["built"] for f in frames] == [["slice"], ["Field", "slice"], ["Field", "slice", "threshold"],
                                            ["Field", "slice", "threshold"]]
    assert frames[0]["ids"] == frames[3]["ids"]
    runs = [f["runs"]["slice:s1"] for f in frames]
    assert runs[0] == runs[1] == runs[2] < runs[3]  # idle while the others show


def test_every_node_type_builds_and_draws():
    (frame,) = _frames(["every-node"])
    assert frame["error"] is None and set(frame["ids"]) == {
        "grid", "calc", "thr", "surf", "clip", "iso", "arrows", "lines", "d1", "d2", "d3", "d4", "view"}
    assert all(points > 0 for points in frame["points"].values()), frame["points"]


def test_a_scene_shows_the_whole_domain_at_every_size():
    """A slice through the four cells, seen from the other side than Field: in the image it takes
    Field's place exactly, also when the size changes with the scene built (a camera preset is
    fitted again: a stale fit cropped the domain)."""
    frames = _frames(["Field@400x300", "Field@600x300", "slice-y@400x300", "slice-y@600x300", "slice-y@400x300"])
    boxes = [f["box"] for f in frames]
    assert boxes[2] == boxes[4] == boxes[0] and boxes[3] == boxes[1]


#: The title of a magnitude's bar: ``|U|``, drawn as math where VTK's text takes ``|`` for a cell separator.
MAGNITUDE = ("|U|", "$\\vert$U$\\vert$")


def test_each_coloured_display_has_its_titled_bar_unless_turned_off():
    (frame,) = _frames(["bars"])
    (magnitude, component) = frame["bars"]
    assert magnitude in MAGNITUDE and component == "U.x"


def test_a_bad_scene_fails_alone_and_keeps_its_last_build():
    frames = _frames(["Field", "slice", "slice=broken", "Field", "slice"])
    assert frames[2]["error"] == "scene 'slice': node 's1' (Slice): props.axis: Input should be 'auto', 'x', 'y' or 'z'"
    assert frames[2]["colours"] is None
    assert frames[3]["error"] is None and frames[3]["colours"]["yellow"]
    assert frames[4]["error"] is None and frames[4]["ids"] == frames[1]["ids"]


@pytest.mark.skipif(not shutil.which("mpirun"), reason="needs mpirun")
def test_every_rank_builds_the_same_scene():
    """mpirun -np 2: rank 0 has x 0–4 (T -1…2), rank 1 x 4–8 (T 3…6). The slice at 90 % of x is
    in rank 1's cells, moved to 10 % in rank 0's: rank 0's composited image shows each in turn.
    The scenes after it (a contour, a threshold, every node type) keep the ranks in step, also
    with rank 0 alone reading the pipeline back."""
    steps = ["Field", "slice", "slice=slice-moved", "slice-contour", "threshold", "every-node", "Field"]
    frames = _frames(steps, launch=("mpirun", "-np", "2", PYTHON))
    by_rank = {r: [f for f in frames if f["rank"] == r] for r in (0, 1)}
    for rank in (0, 1):
        assert [f["scene"] for f in by_rank[rank]] == [s.partition("=")[0] for s in steps]
        assert all(f["error"] is None for f in by_rank[rank])
        assert by_rank[rank][1]["ids"] == by_rank[rank][2]["ids"]
    assert [f["colours"] for f in by_rank[0][:3]] == [{"purple": True, "yellow": True, "red": False},
                                                      {"purple": False, "yellow": True, "red": False},
                                                      {"purple": True, "yellow": False, "red": False}]
    assert all(f["colours"] is None for f in by_rank[1])


def _cell_colours(frame):
    """Per cell: which colours it shows (``empty``: only background)."""
    keys = ("empty", "purple", "yellow", "red")
    return {cell: [k for k in keys if seen[k]] for cell, seen in frame["layout"]["cells"].items()}


#: side-by-side: x 90 % (T = 2, yellow) left, x 10 % (T = -1, purple) right; quad adds a red contour, one cell empty.
SIDE_BY_SIDE = {"left": ["yellow"], "right": ["purple"]}
QUAD = {"tl": ["yellow"], "tr": ["purple"], "bl": ["red"], "br": ["empty"]}


@pytest.mark.parametrize("scene, cells", [("side-by-side", SIDE_BY_SIDE), ("quad", QUAD)])
def test_each_cell_of_a_layout_shows_its_displays(scene, cells):
    (frame,) = _frames([f"{scene}@600x400"])
    assert frame["error"] is None and frame["layout"]["size"] == [600, 400]
    assert _cell_colours(frame) == cells


def test_each_cell_is_a_render_view_of_its_own():
    (frame,) = _frames(["quad"])
    assert list(frame["cell_ids"]) == ["tl", "tr", "bl", "br"] and len(set(frame["cell_ids"].values())) == 4


def test_a_new_layout_keeps_the_filters():
    """side-by-side → its graph in a 2x2 layout (d1 top left, d2 bottom right) → back."""
    frames = _frames(["side-by-side@600x400", "side-by-side=side-by-side-2x2@600x400", "side-by-side@600x400"])
    kept = [{k: f["ids"][k] for k in ("grid", "s1", "s2", "d1", "view")} for f in frames]
    assert kept[0] == kept[1] == kept[2]
    assert [_cell_colours(f) for f in frames] == [
        SIDE_BY_SIDE, {"tl": ["yellow"], "tr": ["empty"], "bl": ["empty"], "br": ["purple"]}, SIDE_BY_SIDE]


def test_linked_cells_share_one_camera():
    (frame,) = _frames(["side-by-side@600x300"])
    left, right = (frame["layout"]["cells"][c]["camera"] for c in ("left", "right"))
    assert left == right


def test_per_cell_cameras_are_each_cells_own():
    """The right cell looks from -x at zoom 2: from the other side, half the parallel scale."""
    (frame,) = _frames(["side-by-side-per-cell@600x300"])
    left, right = (frame["layout"]["cells"][c]["camera"] for c in ("left", "right"))
    assert left[0] > 8 and right[0] < 0  # the four cells span x 0–4
    assert right[3] == pytest.approx(left[3] / 2, rel=0.02)  # a 1 px narrower cell: within 2 %


def _run(args, cwd=None):
    res = subprocess.run(args, capture_output=True, text=True, timeout=300, env=_env(), cwd=cwd)
    assert res.returncode == 0, res.stderr[-3000:]
    return [json.loads(line) for line in res.stdout.splitlines() if line.startswith("{")]


@pytest.fixture
def exported(tmp_path):
    """slice-contour exported as a Catalyst script by the pipeline that shows it."""
    script = tmp_path / "export" / "slice-contour.py"
    script.parent.mkdir()
    (said,) = _run([PYTHON, str(EXPORT), "export", PREFIX, f"{SCENES}/slice-contour.json", str(script)],
                   cwd=script.parent)
    assert said == {"exported": str(script), "error": None}
    return script


@pytest.mark.skipif(not shutil.which("mpirun"), reason="needs mpirun")
def test_every_cell_of_a_layout_is_composited_over_the_ranks():
    """mpirun -np 2: x 90 % is in rank 1's cells, x 10 % in rank 0's: rank 0's picture has both,
    each in its cell, also after the layout changed."""
    steps = ["side-by-side@600x400", "side-by-side=side-by-side-2x2@600x400", "quad@600x400"]
    frames = _frames(steps, launch=("mpirun", "-np", "2", PYTHON))
    by_rank = {r: [f for f in frames if f["rank"] == r] for r in (0, 1)}
    assert all(f["error"] is None for f in frames) and len(by_rank[1]) == 3
    assert [_cell_colours(f) for f in by_rank[0]] == [
        SIDE_BY_SIDE, {"tl": ["yellow"], "tr": ["empty"], "bl": ["empty"], "br": ["purple"]}, QUAD]


def test_an_export_writes_the_script_only(exported):
    """Its PNG extractor exists only while the state is saved: no extract is written now."""
    assert sorted(p.name for p in exported.parent.rglob("*")) == ["slice-contour.py"]


def test_an_exported_scene_runs_through_catalyst_on_a_written_case(exported, tmp_path):
    (said,) = _run([PYTHON, str(EXPORT), "replay", PREFIX, str(exported), "-"], cwd=tmp_path)
    assert said["error"] is None
    assert (tmp_path / "postProcessing" / "slice-contour" / "slice-contour_000000.png").read_bytes()[:4] == b"\x89PNG"


def test_pvbatch_runs_an_exported_scene(exported, tmp_path):
    """ParaView's own post-processing mode: no solver, the extracts written once."""
    _run([str(Path(PREFIX) / "bin" / "pvbatch"), str(exported)], cwd=tmp_path)
    assert (tmp_path / "postProcessing" / "slice-contour" / "slice-contour_000000.png").exists()


def test_a_field_that_is_not_there_fails_the_node_that_reads_it():
    (frame,) = _frames(["typo"])
    assert frame["error"] == "scene 'typo': node 'c1' (Contour): no field 'Ux' (fields: T, U)"


MPIRUN = ("mpirun", "-np", "2")


@pytest.mark.skipif(not shutil.which("mpirun"), reason="needs mpirun")
def test_an_exported_layout_writes_one_picture_per_step_under_mpi(tmp_path):
    """side-by-side, exported and run with two ranks through Catalyst and in pvbatch: the script
    builds both cells' views in their ParaView layout and writes the layout as one picture
    (ParaView's image extractors take a single view: the script's ``catalyst_execute`` captures it)."""
    script = tmp_path / "side-by-side.py"
    _run([*MPIRUN, PYTHON, str(EXPORT), "export", PREFIX, f"{SCENES}/side-by-side.json", str(script)], cwd=tmp_path)
    text = script.read_text()
    assert "CreateLayout(name='side-by-side:layout')" in text and "def catalyst_execute(info):" in text
    for runner, where in (([*MPIRUN, PYTHON, str(EXPORT), "replay", PREFIX, str(script), "-"], "catalyst"),
                          ([*MPIRUN, str(Path(PREFIX) / "bin" / "pvbatch"), str(script)], "pvbatch")):
        (tmp_path / where).mkdir()
        _run(runner, cwd=tmp_path / where)
        written = sorted(p.name for p in (tmp_path / where / "postProcessing" / "side-by-side").iterdir())
        assert written == ["side-by-side_000000.png"], where


@pytest.mark.skipif(not shutil.which("mpirun"), reason="needs mpirun")
def test_out_writes_a_layout_as_one_picture_under_mpi(tmp_path):
    """``out=<dir>`` with two ranks: a layout's frame is one picture of the requested size with each
    cell's colours composited over the ranks; a one-cell scene between them is written as before."""
    frames = _frames(["side-by-side@600x400", "slice@600x400", "quad@600x400"], launch=(*MPIRUN, PYTHON),
                     out=tmp_path)
    written = [f["out"] for f in frames if f["rank"] == 0]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["view_000000.png", "view_000001.png", "view_000002.png"]
    assert [w["size"] for w in written] == [[600, 400]] * 3 and "cells" not in written[1]
    assert [_cell_colours({"layout": written[i]}) for i in (0, 2)] == [SIDE_BY_SIDE, QUAD]
