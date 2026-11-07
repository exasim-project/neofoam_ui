"""Run in a NeoFOAM interpreter inside a case dir: ``plain``, ``step`` or ``run`` → fields .npz."""

import sys

import numpy as np

mode, out = sys.argv[1], sys.argv[2]
if mode == "plain":
    from neofoam.solver.incompressibleFluid.incompressibleFluid import run

    ctx = run(["neofoam"])
    fields = {k: np.asarray(f.internalField()) for k, f in ctx.fields.items()
              if k in ("U", "p", "phi")}
else:
    from neofoam_ui.adapters.neofoam import incompressible_fluid
    from neofoam_ui.engine import Session

    messages = []
    session = Session(incompressible_fluid(), messages.append)
    session.start()
    while not session.finished:
        # step: pause before every single event; run: one free run (events passed in the solver)
        session.advance("into" if mode == "step" else "run")
    errors = [m for m in messages if m["type"] == "error"]
    if errors:
        sys.exit(errors[0]["traceback"])
    fields = {k: v for k, v in session.solver.fields(session.ctx).items() if k in ("U", "p", "phi")}
np.savez(out, **fields)
