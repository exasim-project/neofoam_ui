"""In-situ field view through Catalyst 2: the data stays in the solver.

At a pause every rank describes *its* cells and fields as a conduit Blueprint
mesh (arrays passed without copying) and calls ``catalyst_execute``. The
ParaView implementation runs ``pipelines/view.py`` on all ranks together; in image
mode :meth:`InSitu.execute` captures the view (IceT composites the ranks) and returns
one PNG of the whole domain on rank 0, which goes to the UI as bytes. In data mode
the pipeline publishes to Catalyst Live instead (``NEOFOAM_UI_CATALYST_DATA=host:port``,
set by the UI, which draws the data itself). ``NEOFOAM_UI_CATALYST_LIVE=host:port`` lets
a desktop ParaView attach to the same pipeline at every frame. Rank 0 also describes the live ParaView pipeline
(:meth:`InSitu.describe`: sources, filters, displays, views, extractors and the
time each took) for the UI's post-processing diagram — settings only, no data.

Configuration (environment of the worker):

* ``NEOFOAM_UI_CATALYST`` — install prefix holding ``lib/libcatalyst.so``,
  ``lib/catalyst/libcatalyst-paraview.so`` and the ``catalyst`` /
  ``catalyst_conduit`` / ``paraview`` Python packages under
  ``lib/python3.X/site-packages``. ParaView must be built for this interpreter
  (shared Python modules, same NumPy major, same MPI as the solver).
* ``NEOFOAM_UI_CATALYST_OUT`` — directory for the images of a solver's own in-situ entry
  outside the stepper (default ``postProcessing/<entry>``); the stepper writes no files.
* ``NEOFOAM_UI_CATALYST_SCRIPT`` — another pipeline script (optional). The default
  pipeline (``neofoam_ui/pipelines/view.py``) gets only the viewed field; another script
  gets every cell field.

Collective: under MPI every rank must call :meth:`InSitu.execute` for the same
pause, on the thread that initialised MPI.

A NeoFOAM case can also render from inside the solver: an ``insitu`` entry of type
``catalyst`` in ``system/postProcess.yaml`` (:func:`neofoam_ui.adapters.neofoam.register_insitu`).
Under the stepper its frames go through the field view (:func:`set_frame_driver`), so a
process never starts two Catalyst sessions.
"""

from neofoam_ui.catalyst._config import ENV, SCRIPT_ENV, configured, custom_script
from neofoam_ui.catalyst._driver import FrameDriver, frame_driver, set_frame_driver
from neofoam_ui.catalyst._insitu import InSitu
from neofoam_ui.protocol import CATALYST_LIVE_ENV as LIVE_ENV
from neofoam_ui.protocol import CATALYST_OUT_ENV as OUT_ENV

__all__ = ["ENV", "LIVE_ENV", "OUT_ENV", "SCRIPT_ENV", "FrameDriver", "InSitu", "configured", "custom_script",
           "frame_driver", "set_frame_driver"]
