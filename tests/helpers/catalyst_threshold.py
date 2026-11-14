"""A Catalyst-v2 script other than the stepper's own: threshold, slice, a data extractor.

Used by the post-processing diagram test (``catalyst.script``-style pipelines).
"""

from paraview import catalyst
from paraview.simple import CreateExtractor, CreateView, Show, Threshold, TrivialProducer

_args = dict(arg.split("=", 1) for arg in catalyst.get_args())
options = catalyst.Options()
options.ExtractsOutputDirectory = _args.get("out", ".")

grid = TrivialProducer(registrationName="grid")
hot = Threshold(registrationName="hot", Input=grid)
hot.Scalars = ["CELLS", "p"]
hot.LowerThreshold = 1.5
hot.UpperThreshold = 10.0
view = CreateView("RenderView")
view.ViewSize = [400, 300]
Show(hot, view)
png = CreateExtractor("PNG", view, registrationName="image")
png.Writer.FileName = "view_{timestep:06d}.png"
data = CreateExtractor("VTPD", grid, registrationName="data")
data.Writer.FileName = "grid_{timestep:06d}.vtpd"
data.Trigger.Frequency = 5


def catalyst_execute(info):
    pass
