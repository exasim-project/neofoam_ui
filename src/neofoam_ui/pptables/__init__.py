"""NeoFOAM post-processing tables, seen from the stepper: their rows and their pipelines.

A case declares *tables* (``system/postProcess.py`` / ``.yaml``); NeoFOAM's
``post_process`` op evaluates the due ones each step through a ``PostProcessor``
(``neofoam.postprocess.model``) and hands every result to the table's writer.
:func:`observe` hooks that processor from the outside — nothing in NeoFOAM is
changed — and turns what passes into two worker messages:

* ``pptables`` once, when the processor is built: every table's pipeline as a
  diagram (:func:`describe_table`, read off the node objects' pydantic fields);
* ``pptable`` per evaluated table and step: the newest rows, already reduced over
  the MPI ranks by NeoFOAM's aggregators (only rank 0's messages reach the UI)::

      {"type": "pptable", "name": "p_mean", "time": 0.001, "ms": 0.4,
       "columns": ["p_mean"], "group_columns": [], "values": [[-1.2]], "groups": [[]]}

Duck-typed throughout (no neofoam import), so a fake processor tests it.

A node the generic reading describes badly (a case's own plugin, say) gets a
:class:`NodeDescriber`: ``register_describer(fn)`` once at start-up; ``fn(obj)`` returns the
node's ``name`` / ``type`` / ``kind`` / ``settings`` (any of them) or ``None`` for "not mine".
"""

from neofoam_ui.pptables._describe import NodeDescriber, describe_table, register_describer
from neofoam_ui.pptables._rows import rows_message
from neofoam_ui.pptables._tee import observe

__all__ = ["NodeDescriber", "describe_table", "observe", "register_describer", "rows_message"]
