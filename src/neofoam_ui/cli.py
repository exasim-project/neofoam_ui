# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NeoFOAM authors

"""The ``neofoam ui`` command group: the case wizard and the DAG stepper.

``neofoam`` adds it through the ``neofoam.cli`` entry point of this package, so it
is there whenever ``neofoam_ui`` is installed next to ``neofoam``. trame is imported
only when a command starts a UI, so ``neofoam --help`` stays fast.

Example::

    neofoam ui wizard --solver incompressibleFluid --port 8080
    neofoam ui run --case run/pitzDaily --python ~/NeoFOAM/.venv/bin/python
"""

import typer

__all__ = ["app"]

app = typer.Typer(help="Browser UIs for NeoFOAM: the case wizard and the DAG stepper.")


@app.command("wizard")
def wizard(
    port: int = typer.Option(8080, "--port", help="Port to serve the wizard on."),
    host: str = typer.Option("localhost", "--host", help="Bind host."),
    solver: str = typer.Option(
        "incompressibleFluid", "--solver", help="Solver whose configs to edit."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not open a web browser on launch."
    ),
) -> None:
    """Launch the interactive case-wizard web UI (trame + JSONForms).

    A browser wizard: pick models on the left, fill the JSON-schema forms (or use
    the AI chat on the right), then Save to write a runnable case (with
    ``Allrun``/``Allclean``) and validate it.
    """
    from neofoam_ui.wizard import build_app  # noqa: PLC0415 - trame only when the UI starts

    server = build_app(solver_name=solver)
    typer.echo(f"NeoFOAM case wizard → http://{host}:{port}/")
    server.start(host=host, port=port, open_browser=not no_browser)


@app.command(
    "run",
    add_help_option=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run(ctx: typer.Context) -> None:
    """Step through a solver run as an activity diagram (``--help`` lists its options)."""
    from neofoam_ui.app import main  # noqa: PLC0415 - trame only when the UI starts

    main.main(args=list(ctx.args), prog_name="neofoam ui run")
