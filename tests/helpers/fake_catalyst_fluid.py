"""NeoFOAM's incompressibleFluid with Catalyst faked inside the worker (frames are names, nothing renders)."""

import os

from fake_view_toy import _FakeInSitu

from neofoam_ui import catalyst
from neofoam_ui.adapters.neofoam import incompressible_fluid


def FakeViewFluid():  # noqa: N802 - a solver factory, like a class
    os.environ.setdefault(catalyst.ENV, "/nowhere")
    os.environ.setdefault(catalyst.OUT_ENV, "/nowhere")
    catalyst.InSitu = _FakeInSitu
    return incompressible_fluid()
