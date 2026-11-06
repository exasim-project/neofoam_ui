# ParaView Catalyst (v2-style) script for `neofoam-ui --catalyst scripts/catalyst_pipeline.py`.
#
# Channel "input": the field view's mesh with the viewed field as cell data.
# Env: NEOFOAM_UI_CATALYST_LIVE=host:port  enables Catalyst Live (default localhost:22222;
#      "off" disables) — connect from ParaView of the *same version* via Catalyst > Connect;
#      NEOFOAM_UI_CATALYST_VTU=<dir>         also writes one .vtu per pause into <dir>.
import os

from paraview import catalyst
from paraview.simple import CreateExtractor, TrivialProducer

producer = TrivialProducer(registrationName="input")

options = catalyst.Options()
options.GlobalTrigger = "TimeStep"
out = os.environ.get("NEOFOAM_UI_CATALYST_VTU")
if out:
    CreateExtractor("VTU", producer, registrationName="vtu").Trigger = "TimeStep"
    options.ExtractsOutputDirectory = out
live = os.environ.get("NEOFOAM_UI_CATALYST_LIVE", "localhost:22222")
if live != "off":
    options.EnableCatalystLive = 1
    options.CatalystLiveURL = live
    options.CatalystLiveTrigger = "TimeStep"


def catalyst_execute(info):
    data = producer.GetClientSideObject().GetOutputDataObject(0)
    print(f"[catalyst] step {info.timestep} t={info.time:.6g} cells={data.GetNumberOfCells()}")
