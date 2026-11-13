"""The Scenes panel (``_ui/panes/_scenes.py``): the built-in ``Field`` and the case's scene files,
switch, new, duplicate, rename, delete and save, unsaved edits, bad files listed with their error.
It runs on the app the way the page does (state and triggers), on a copy of a case on disk."""

import json
import shutil
from pathlib import Path

import pytest
from trame.app import get_server

from neofoam_ui.app import StepperApp

CASES = Path(__file__).parent / "scenes" / "cases"


@pytest.fixture
def case(tmp_path):
    """``slice`` and ``threshold``; ``not-json``, ``two-views`` and ``Field`` are not scenes."""
    shutil.copytree(CASES / "listing", tmp_path / "case")
    return tmp_path / "case"


def _scenes(name, case):
    app = StepperApp(get_server(name, client_type="vue3"), "neofoam_ui.toy.heat1d:Heat1D", case=str(case))
    app.link.send = lambda cmd: None
    return app.pane("field").scenes, app.state


def _listed(state):
    return {s["name"]: (s["sub"], s["unsaved"], s["error"]) for s in state.sc_list}


def test_the_list_has_the_built_in_scene_and_the_case_files(case):
    _, state = _scenes("scenes_list", case)
    listed = _listed(state)
    assert {k: v for k, v in listed.items() if not v[2]} == {
        "Field": ("built-in", False, False), "slice": ("Slice", False, False),
        "threshold": ("Threshold", False, False)}


@pytest.mark.parametrize("name, error", [
    ("not-json", "not JSON"),
    ("two-views", "a scene has one View node, not 2"),
    ("Field.json", "Field is the built-in scene's name"),
])
def test_a_bad_file_is_listed_with_its_error(case, name, error):
    _, state = _scenes("scenes_bad_" + name.replace(".", "_"), case)
    sub, _, bad = _listed(state)[name]
    assert bad and error in sub


def test_a_bad_file_cannot_be_shown(case):
    scenes, state = _scenes("scenes_bad_switch", case)
    scenes.switch("slice")
    scenes.switch("two-views")
    assert state.sc_current == "slice"


def test_duplicating_field_makes_field_copy_unsaved(case):
    scenes, state = _scenes("scenes_dup_field", case)
    scenes.duplicate()
    assert state.sc_current == "field-copy" and _listed(state)["field-copy"] == ("new, not saved", True, False)
    assert not (case / "system" / "scenes" / "field-copy.json").exists()


def test_saving_writes_the_scene_file(case):
    scenes, state = _scenes("scenes_save", case)
    scenes.duplicate()
    scenes.edit_view("+z", 2.0)
    scenes.save()
    saved = json.loads((case / "system" / "scenes" / "field-copy.json").read_text())
    assert next(n for n in saved["nodes"] if n["type"] == "View")["props"] == {"camera": "+z", "zoom": 2.0}
    assert not _listed(state)["field-copy"][1]


def test_an_edit_marks_the_scene_unsaved(case):
    scenes, state = _scenes("scenes_edit", case)
    scenes.switch("slice")
    scenes.edit_view("-x", 1.0)
    assert _listed(state)["slice"] == ("edited, not saved", True, False)


def test_new_is_an_unsaved_scene_of_its_own(case):
    scenes, state = _scenes("scenes_new", case)
    scenes.new()
    scenes.new()
    assert state.sc_current == "scene-2" and _listed(state)["scene"][1]


def test_a_second_copy_gets_a_free_name(case):
    scenes, state = _scenes("scenes_dup_twice", case)
    scenes.switch("slice")
    scenes.duplicate()
    scenes.switch("slice")
    scenes.duplicate()
    assert state.sc_current == "slice-copy-2"


def test_rename_titles_the_scene_and_its_file_gets_the_slug(case):
    scenes, state = _scenes("scenes_rename", case)
    scenes.switch("slice")
    scenes.rename("p slice + U contour")
    path = case / "system" / "scenes" / "p-slice-u-contour.json"
    assert state.sc_current == "p-slice-u-contour" and not (case / "system" / "scenes" / "slice.json").exists()
    assert json.loads(path.read_text())["title"] == "p slice + U contour"
    assert next(s for s in state.sc_list if s["name"] == "p-slice-u-contour")["title"] == "p slice + U contour"
    assert not _listed(state)["p-slice-u-contour"][1]  # renaming a saved scene leaves nothing unsaved


def test_a_scene_without_a_title_is_listed_by_its_file_name(case):
    _, state = _scenes("scenes_untitled", case)
    assert {s["name"]: s["title"] for s in state.sc_list if not s["error"]} == {
        "Field": "Field", "slice": "slice", "threshold": "threshold"}


def test_duplicating_titles_the_copy(case):
    scenes, state = _scenes("scenes_dup_title", case)
    scenes.duplicate()
    assert next(s for s in state.sc_list if s["name"] == "field-copy")["title"] == "Field copy"


@pytest.mark.parametrize("name, message", [
    ("Threshold", "a scene 'Threshold' exists (system/scenes/threshold.json)"),
    ("Field", "a scene 'Field' exists"),
    ("+ -", "a title needs a letter or a digit"),
])
def test_rename_refuses_a_taken_or_bad_name(case, name, message):
    scenes, state = _scenes("scenes_rename_bad_" + str(len(name)) + name[:2].replace(" ", "_"), case)
    scenes.switch("slice")
    scenes.rename(name)
    assert state.sc_current == "slice" and message in state.sc_msg


def test_delete_removes_the_file_and_shows_field(case):
    scenes, state = _scenes("scenes_delete", case)
    scenes.switch("threshold")
    scenes.delete()
    assert state.sc_current == "Field" and "threshold" not in _listed(state)
    assert not (case / "system" / "scenes" / "threshold.json").exists()


def test_field_cannot_be_deleted(case):
    scenes, state = _scenes("scenes_delete_field", case)
    scenes.delete()
    assert "Field" in _listed(state)


def test_reading_again_finds_new_files_and_keeps_unsaved_edits(case):
    scenes, state = _scenes("scenes_reload", case)
    scenes.switch("slice")
    scenes.edit_view("-x", 1.0)
    shutil.copy(case / "system" / "scenes" / "threshold.json", case / "system" / "scenes" / "by-hand.json")
    scenes.load()
    assert _listed(state)["by-hand"] == ("Threshold", False, False) and _listed(state)["slice"][1]


def test_a_case_without_scenes_has_field_only(tmp_path):
    _, state = _scenes("scenes_none", tmp_path)
    assert list(_listed(state)) == ["Field"]
