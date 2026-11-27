"""NeoFOAM's incompressibleFluid with Catalyst faked inside the worker (frames are placeholder bytes, nothing renders)."""

from fake_view_toy import _FakeInSitu

from neofoam_ui import catalyst
from neofoam_ui.adapters.neofoam import incompressible_fluid
from neofoam_ui.config import CatalystConfig


def FakeViewFluid():  # noqa: N802 - a solver factory, like a class
    catalyst.use(CatalystConfig(prefix="/nowhere"))
    catalyst.InSitu = _FakeInSitu
    return incompressible_fluid()
