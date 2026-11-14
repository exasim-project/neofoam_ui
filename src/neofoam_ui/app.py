"""trame app: the DAG stepper (activity-diagram simulation of a solver run).

Launch: ``neofoam-ui [--solver module:Factory] [--port 8080]``; every option but ``--solver``,
``--case``, ``--no-browser`` and ``--trame-args`` has its default in the config files of the
case (:mod:`neofoam_ui.config`; the command line wins). Unknown options are an error; options
for trame / wslink go in ``--trame-args "--debug ..."`` (trame reads that one itself). The solver runs
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

from typing import Any, NamedTuple, Optional

import click
from trame.app import get_server

from neofoam_ui._ui._app import StepperApp
from neofoam_ui.config import Config, load_config

__all__ = ["Launch", "StepperApp", "main", "parse_args"]

#: The solver run without ``--solver``.
_DEFAULT_SOLVER = "neofoam_ui.toy.heat1d:Heat1D"


class Launch(NamedTuple):
    """What :func:`main` runs: the solver, its case directory, and the settings."""

    solver: str
    case: Optional[str]
    no_browser: bool
    config: Config


def _case_defaults(ctx: click.Context, param: click.Parameter, case: Optional[str]) -> Optional[str]:
    """Load the config files of ``case`` as the other options' defaults (``--case`` is eager)."""
    config = load_config(case or ".")
    ctx.meta["config"] = config
    ctx.default_map = {
        "python": config.worker.python, "np": config.worker.np, "mpirun": config.worker.mpirun,
        "sync_host": config.worker.sync_host, "catalyst": config.catalyst.prefix,
        "delay": config.ui.delay_ms, "wait_ui": config.ui.wait_ui, "host": config.ui.host, "port": config.ui.port,
    }
    return case


@click.command(help=__doc__.splitlines()[0], context_settings={"show_default": True})
@click.option("--solver", default=_DEFAULT_SOLVER, help="module:Factory of the solver")
@click.option("--case", is_eager=True, callback=_case_defaults,
              help="case directory the worker runs in (default: cwd); its config file applies")
@click.option("--python", help="interpreter for the worker, e.g. a NeoFOAM venv (worker.python)")
@click.option("--np", type=int, help="MPI ranks, decomposed case (worker.np)")
@click.option("--mpirun", help="MPI launcher command (worker.mpirun)")
@click.option("--catalyst", metavar="PREFIX",
              help="Catalyst 2 + ParaView install for the in-situ field view (catalyst.prefix); "
                   "ParaView must be built for the worker's Python")
@click.option("--delay", type=float, metavar="MS",
              help="pause after every event while running, in ms (ui.delay_ms; the toolbar slider changes it live)")
@click.option("--wait-ui/--no-wait-ui", default=None,
              help="the solver waits until the UI showed every event and frame (ui.wait_ui); "
                   "off, it never waits and a lagging UI skips progress messages")
@click.option("--sync-host", help="address rank 0 listens on for the other MPI ranks (worker.sync_host)")
@click.option("--port", type=int, help="(ui.port)")
@click.option("--host", help="(ui.host)")
@click.option("--no-browser", is_flag=True, help="do not open a browser")
@click.option("--trame-args", metavar="ARGS", help="options for trame / wslink, e.g. \"--debug\"")
def main(**options: Any) -> None:
    _serve(_launch(click.get_current_context(), options))


def _launch(ctx: click.Context, options: dict[str, Any]) -> Launch:
    """The options, checked by the config model (``~`` expanded)."""
    config = Config.model_validate({
        "worker": {key: options[key] for key in ("python", "np", "mpirun", "sync_host")},
        "catalyst": {**ctx.meta["config"].catalyst.model_dump(), "prefix": options["catalyst"]},
        "ui": {"delay_ms": options["delay"], "wait_ui": options["wait_ui"], "host": options["host"],
               "port": options["port"]},
    })
    return Launch(options["solver"], options["case"], options["no_browser"], config)


def parse_args(argv: list[str]) -> Launch:
    """What ``neofoam-ui argv`` would run, without running it (raises :class:`click.UsageError`).

    Example: ``parse_args(["--case", "run/pitzDaily"]).config.ui.port``.
    """
    with main.make_context("neofoam-ui", list(argv)) as ctx:
        return _launch(ctx, ctx.params)


def _serve(launch: Launch) -> None:
    worker, ui = launch.config.worker, launch.config.ui
    server = get_server("neofoam_ui", client_type="vue3")
    StepperApp(server, launch.solver, case=launch.case, python=worker.python, np=worker.np, mpirun=worker.mpirun,
               catalyst=launch.config.catalyst.prefix, delay_ms=ui.delay_ms, wait_ui=ui.wait_ui,
               sync_host=worker.sync_host)
    # timeout=0: keep serving when no browser is connected (wslink's default exits after 300 s).
    server.start(port=ui.port, host=ui.host, open_browser=not launch.no_browser, timeout=0)


if __name__ == "__main__":
    main()
