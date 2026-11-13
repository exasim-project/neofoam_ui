"""trame app: the DAG stepper (activity-diagram simulation of a solver run).

Launch: ``neofoam-ui [--solver module:Factory] [--port 8080]``. The solver runs
in a worker process (:mod:`neofoam_ui.worker`); this process only renders and
sends stepping commands. Panes can be added without editing it: :mod:`neofoam_ui.extensions`.
The field view's data mode (Catalyst Live, drawn interactively here) needs this process under
the ``pvpython`` of a ParaView 6.0 with web modules, e.g. Kitware's 6.0.1 release (a
Catalyst-only build has none; data mode is greyed out there), while the worker keeps the
Catalyst build::

    ~/software/ParaView-6.0.1/bin/pvpython --venv .pixi/envs/default -m neofoam_ui.app
        --catalyst <prefix> --python <solver venv>/bin/python ...
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

from trame.app import get_server

from neofoam_ui._ui._app import StepperApp
from neofoam_ui.protocol import CATALYST_ENV, DELAY_ENV, WAIT_ENV

__all__ = ["StepperApp", "main", "parse_args"]

#: The solver run without ``--solver``.
_DEFAULT_SOLVER = "neofoam_ui.toy.heat1d:Heat1D"


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--solver", default=_DEFAULT_SOLVER, help="module:Factory of the solver")
    parser.add_argument("--case", help="case directory the worker runs in (default: cwd)")
    parser.add_argument("--python", help="interpreter for the worker, e.g. a NeoFOAM venv")
    parser.add_argument("--np", type=int, default=1, help="MPI ranks (decomposed case)")
    parser.add_argument("--mpirun", default="mpirun", help="MPI launcher command")
    parser.add_argument("--catalyst", metavar="PREFIX",
                        help=f"Catalyst 2 + ParaView install for the in-situ field view "
                             f"(default ${CATALYST_ENV}); ParaView must be built for the worker's Python")
    parser.add_argument("--delay", type=float, metavar="MS", default=float(os.environ.get(DELAY_ENV) or 0),
                        help=f"pause after every event while running, in ms (default ${DELAY_ENV} or 0; "
                             "the toolbar slider changes it live)")
    parser.add_argument("--wait-ui", action="store_true",
                        default=os.environ.get(WAIT_ENV, "").lower() in ("1", "true", "yes", "on"),
                        help=f"the solver waits until the UI showed every event and frame (default ${WAIT_ENV}); "
                             "off, it never waits and a lagging UI skips progress messages")
    parser.add_argument("--sync-host", default="127.0.0.1",
                        help="address rank 0 listens on for the other MPI ranks (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_known_args(argv)[0]


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    server = get_server("neofoam_ui", client_type="vue3")
    StepperApp(server, args.solver, case=args.case, python=args.python, np=args.np,
               mpirun=args.mpirun, catalyst=args.catalyst, delay_ms=args.delay, wait_ui=args.wait_ui,
               sync_host=args.sync_host)
    # timeout=0: keep serving when no browser is connected (wslink's default exits after 300 s).
    server.start(port=args.port, host=args.host, open_browser=not args.no_browser, timeout=0)


if __name__ == "__main__":
    main()
