"""Heat1D with Catalyst faked inside the worker process (frames are placeholder bytes, nothing renders)."""

from neofoam_ui import catalyst
from neofoam_ui.config import CatalystConfig
from neofoam_ui.toy.heat1d import Heat1D


class _FakeInSitu:
    def __init__(self, out_dir=None):
        self.step = 0

    def execute(self, geom, fields, time, params):
        self.step += 1
        return None if params["mode"] == "data" else b"png"

    def finalize(self):
        pass


def ViewHeat():  # noqa: N802 - a solver factory, like a class
    catalyst.use(CatalystConfig(prefix="/nowhere"))
    catalyst.InSitu = _FakeInSitu
    return Heat1D()
