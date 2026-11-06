"""Catalyst publisher — only where ParaView's Python modules are importable (``pixi run -e catalyst``)."""

import os

import numpy as np
import pytest

pytest.importorskip("paraview.catalyst.bridge")

from neofoam_ui.engine import Session  # noqa: E402
from neofoam_ui.insitu import CatalystPublisher  # noqa: E402
from neofoam_ui.toy.heat1d import Heat1D  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "catalyst_pipeline.py")


def test_publishes_vtu_per_pause(tmp_path, monkeypatch):
    monkeypatch.setenv("NEOFOAM_UI_CATALYST_LIVE", "off")
    monkeypatch.setenv("NEOFOAM_UI_CATALYST_VTU", str(tmp_path))
    messages = []
    session = Session(Heat1D(), messages.append)
    session.start()
    session.advance("skip_init")
    session.set_view("T")
    session.advance("time_step")
    pub = CatalystPublisher(os.path.abspath(SCRIPT))
    for m in messages:
        if m["type"] in ("mesh", "field"):
            getattr(pub, m["type"])(m)
    pub.finalize()
    assert pub.step == 2
    vtus = sorted(p for p in tmp_path.rglob("*.vtu"))
    assert len(vtus) >= 2

    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridReader

    reader = vtkXMLUnstructuredGridReader()
    reader.SetFileName(str(vtus[-1]))
    reader.Update()
    grid = reader.GetOutput()
    assert grid.GetNumberOfCells() == 40
    assert np.allclose(vtk_to_numpy(grid.GetCellData().GetArray("T")), session.ctx.fields["T"], rtol=1e-6)
