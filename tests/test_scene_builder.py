"""The solver-side scene builder (``pipelines/view.py``) in a real ParaView, through Catalyst 2:
a changed graph keeps the proxies of unchanged nodes, switching keeps every scene built and runs
only the shown one, every node type builds, a bad scene fails alone, under ``mpirun -np 2``
every rank builds the same scene, and an exported
scene runs on its own (through Catalyst on a written case, and in pvbatch).

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


def _frames(steps, launch=(PYTHON,)):
    res = subprocess.run([*launch, HELPER, PREFIX, json.dumps(steps), SCENES], capture_output=True, text=True,
                         timeout=300, env=_env())
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
