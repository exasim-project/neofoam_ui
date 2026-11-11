"""NeoFOAM adapter helpers that run without NeoFOAM."""

import os

from neofoam_ui.adapters.neofoam._fields import _internal
from neofoam_ui.adapters.neofoam._mesh import _region_dir


def test_neon_fields_are_read_through_internal_vector():
    class Vector:
        def copy_to_host(self):
            return [1.0, 2.0]

    class NeonField:
        def internal_vector(self):
            return Vector()

    assert _internal(NeonField()).tolist() == [1.0, 2.0]
    assert _internal(object()) is None


def test_a_multirun_region_reads_its_own_case_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "solid" / "constant" / "polyMesh").mkdir(parents=True)
    assert _region_dir("solid") == os.path.join("solid", "constant", "polyMesh")
    assert _region_dir("fluid") is None and _region_dir(None) is None
