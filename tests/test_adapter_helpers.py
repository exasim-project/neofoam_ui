"""The NeoFOAM adapter's small helpers: coupling reads, the stepper's own tables, field reads,
the polyMesh directory and the ASCII face reader."""

from types import SimpleNamespace

import numpy as np

from neofoam_ui.adapters.neofoam._fields import _own_fields
from neofoam_ui.adapters.neofoam._mesh import _polymesh_dir
from neofoam_ui.adapters.neofoam._ops import _exchange, _exchange_of, _region_coupling, _scheme
from neofoam_ui.geometry import _read_faces
from neofoam_ui.pptables import rows_message
from neofoam_ui.pptables._rows import table_row


def test_the_region_coupling_of_a_context_or_none():
    coupling = SimpleNamespace(parallel=True)
    ctx = SimpleNamespace(models={"coupling": SimpleNamespace(coupling=coupling)})
    assert _region_coupling(ctx) is coupling
    assert _region_coupling(SimpleNamespace(models={})) is None
    assert _region_coupling(SimpleNamespace()) is None
    assert _scheme(SimpleNamespace(controller=None), ctx) == "parallel"
    # the op's coupling loop tells first
    assert _scheme(SimpleNamespace(loop=SimpleNamespace(scheme="serial")), ctx) == "serial"
    assert _scheme(SimpleNamespace(), SimpleNamespace()) is None


def test_a_port_s_exchange_and_partner_end():
    exchange = SimpleNamespace(name="a-b", partner=lambda region: ("b", "to_a"))
    port = SimpleNamespace(region="a", receives=(SimpleNamespace(names=("T",)),))
    op = SimpleNamespace(exchange_of=lambda s: exchange)
    assert _exchange_of(op, port) == (exchange, "b", "to_a")
    got = _exchange(op, port)
    assert (got.source, got.data, got.partner, got.name) == ("b.to_a", ("T",), "b", "a-b")

    def broken(port):
        raise KeyError(port)

    assert _exchange_of(SimpleNamespace(exchange_of=broken), port) is None
    assert _exchange(SimpleNamespace(exchange_of=broken), port) is None
    assert _exchange_of(SimpleNamespace(), port) is None


def test_a_table_row_is_a_one_row_ungrouped_pptable():
    assert table_row("t", 2, ["a", "b"], [1.0, None], region="r") == {
        "type": "pptable", "name": "t", "time": 2.0, "columns": ["a", "b"], "group_columns": [],
        "values": [[1.0, None]], "groups": [[]], "region": "r"}
    result = SimpleNamespace(values=[SimpleNamespace(group_name=["bin"])], headers=["v", "bin"],
                             grouped_values=[[1.5, 3.0]])
    assert rows_message("t", 1, result, ms=0.12345) == {
        "type": "pptable", "name": "t", "time": 1.0, "columns": ["v"], "group_columns": ["bin"],
        "values": [[1.5]], "groups": [[3]], "ms": 0.123}


def test_own_fields_reads_arrays_and_geometric_fields():
    class Foam:
        def internalField(self):
            return [1.0, 2.0]

    class Neon:
        def internal_vector(self):
            return SimpleNamespace(copy_to_host=lambda: [3.0])

    ctx = SimpleNamespace(fields={"x": np.array(4.0), "p": Foam(), "T": Neon(), "s": "skip"})
    assert {k: v.tolist() for k, v in _own_fields(ctx).items()} == {"x": [4.0], "p": [1.0, 2.0],
                                                                    "T": [3.0]}
    assert _own_fields(SimpleNamespace()) == {}


def test_polymesh_dir_per_rank():
    assert _polymesh_dir(0, 1) == "constant/polyMesh"
    assert _polymesh_dir(3, 4) == "processor3/constant/polyMesh"


def test_ascii_faces_with_mixed_sizes(tmp_path):
    header = "FoamFile\n{\n    format ascii;\n    class faceList;\n    object faces;\n}\n// * //\n"
    faces = [[0, 1, 2], [3, 4, 5, 6], [7, 8, 9, 10, 11]]
    body = f"{len(faces)}\n(\n" + "\n".join(f"{len(f)}({' '.join(map(str, f))})" for f in faces)
    (tmp_path / "faces").write_text(header + body + "\n)\n\n// ***** //\n")
    offsets, conn = _read_faces(str(tmp_path / "faces"))
    assert offsets.tolist() == [0, 3, 7, 12] and conn.tolist() == list(range(12))
    (tmp_path / "empty").write_text(header + "0\n(\n)\n")
    offsets, conn = _read_faces(str(tmp_path / "empty"))
    assert offsets.tolist() == [0] and conn.tolist() == []
