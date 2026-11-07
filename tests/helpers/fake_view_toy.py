"""Heat1D with Catalyst faked inside the worker process (frames are names, nothing renders)."""

import os

from neofoam_ui import catalyst
from neofoam_ui.toy.heat1d import Heat1D


class _FakeInSitu:
    def __init__(self):
        self.step = 0

    def execute(self, geom, fields, time, params):
        self.step += 1
        return f"view_{self.step - 1:06d}.png"

    def finalize(self):
        pass


def ViewHeat():  # noqa: N802 - a solver factory, like a class
    os.environ.setdefault(catalyst.ENV, "/nowhere")
    os.environ.setdefault(catalyst.OUT_ENV, "/nowhere")
    catalyst.InSitu = _FakeInSitu
    return Heat1D()
