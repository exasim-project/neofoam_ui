# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NeoFOAM authors

"""The ``neofoam agent`` command group: LLM-driven case scaffolding."""

from typing import Optional

import typer

from neofoam_ui.agent.case_fill import build_case_agent, fill_case

__all__ = ["app"]

app = typer.Typer(help="LLM-driven case scaffolding.")


@app.command("fill")
def agent_fill(
    source: str = typer.Argument(..., help="Source case directory."),
    target: str = typer.Argument(..., help="Target case directory."),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help=(
            "Skip the LLM and roundtrip the source configs from disk. Useful"
            " for testing the schema + IO path without an API key."
        ),
    ),
    model_name: str = typer.Option(
        "claude-haiku-4-5",
        "--model",
        help="Anthropic model name when not in --no-llm mode.",
    ),
    prompt: Optional[str] = typer.Option(
        None,
        "--prompt",
        help=(
            "Extra natural-language guidance appended to the source-case"
            " text before sending to the LLM."
        ),
    ),
) -> None:
    """Fill the configs of TARGET by reading SOURCE.

    Copies mesh + ``0/``/``0.orig/`` fields from SOURCE, then either
    asks an LLM to rewrite the configs (default) or roundtrips them from
    disk (``--no-llm``). Either way TARGET ends up runnable by
    ``neofoam solver incompressiblefluid``.
    """
    agent_obj = None
    if not no_llm:
        agent_obj = build_case_agent(model_name=model_name)
        if prompt:
            # The default prompt only contains source-case text; append the
            # user note as an extra instruction the agent will see verbatim.
            original_run = agent_obj.run_sync

            def _run_sync(p: str, *args: object, **kwargs: object) -> object:
                return original_run(f"{p}\n\nAdditional guidance:\n{prompt}")

            agent_obj.run_sync = _run_sync

    spec = fill_case(source, target, agent=agent_obj)
    written = [n for n in type(spec).model_fields if getattr(spec, n) is not None]
    typer.echo(f"Wrote configs to {target}: {', '.join(written)}")
